"""What a recorded call sounds like, judged mechanically.

Pure and stdlib-only, on purpose. The worker's CI job installs only the dev
group -- no ``livekit-agents`` -- so anything that imports the heavy stack
cannot run there. Keeping the judgement here and the call itself next door means
these rules are covered by the existing ``uv run --only-group dev pytest`` job
with no workflow change, exactly as the prompt repo's runner splits its pure
half from its I/O half.

The rules here answer questions a transcript cannot. A transcript records what
the platform *believes* was said; it does not record the greeting being spoken
twice, the first word being clipped, or four seconds of silence while a tool
call runs without its filler line. Those are the bugs that survive a green
scripted suite and greet a real caller.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from difflib import SequenceMatcher

# Two utterances counted as "the same thing said twice". Not 1.0: ASR of the
# same synthesized line twice over is rarely character-identical.
DEFAULT_SIMILARITY = 0.9

# How close together two similar utterances have to be to count as a duplicate.
#
# Legitimate repetition exists -- "did you take your Lipitor?" asked again after
# a digression is the prompt working -- so an unbounded comparison would flag
# good calls. The bugs this exists for are immediate: PR #224's greeting spoken
# twice in a row, PR #216's swap reply doubled at the handoff. Thirty seconds is
# generous around both while leaving normal conversational repetition alone.
DEFAULT_WINDOW_S = 30.0

# Utterances shorter than this are never compared with each other.
#
# Without it the rule is a false-positive machine: "Okay." and "Okay." are 100%
# similar and a caller hears nothing wrong, and a call is full of "mm-hm",
# "right", "of course". Those are backchannels, not duplicated turns. The bugs
# being hunted are whole sentences -- a greeting, a handoff line -- so requiring
# a handful of words costs nothing and removes the noise entirely.
DEFAULT_MIN_WORDS = 4

# How far either side of the middle to look for the seam between two copies of
# the same sentence. Narrow on purpose -- see `repeats_itself`.
SEAM_SEARCH = 2

# Dead air after the caller stops talking before the agent answers.
#
# Baselined, no longer guessed. Across 13 Clara calls on
# `gemini-3.1-flash-lite` (35 answered gaps) the distribution splits cleanly by
# whether a tool ran inside the gap:
#
#     no tool in the gap   n=16   median 1.95s   p90 3.29s   max 5.11s
#     a tool ran           n=19   median 3.41s   p90 4.71s   max 4.99s
#
# Nothing exceeded 5.11s. The previous limit of 4.0s — taken from 1.46s/1.73s
# measurements against a two-line smoke agent — therefore flagged 6 of 35
# ORDINARY turns, which is how a rule stops being read.
#
# 6.0s clears every gap observed by ~0.9s and still catches the thing worth
# catching: at six seconds a listener has concluded the line is dead. One limit
# rather than two, even though the tool split is real, because the honest
# reading of a tool gap is not "allow more silence" — it is "the prompt
# requires a filler line before a lookup", and that is a per-case `heard`
# expectation (see A03), not a threshold.
#
# This number is a property of ONE configuration. Production runs a
# speech-to-speech Live model against the same 63k-character prompt; it should
# be faster, 6.0s may be far too generous there, and the measurement has to be
# redone before any of it is believed.
DEFAULT_MAX_SILENCE_S = 6.0

AGENT = "agent"
CALLER = "caller"

# Apostrophes are deleted rather than replaced with a space, and every other
# punctuation mark is replaced with one.
#
# The difference decides whether a contraction survives normalisation. Blanking
# them all to spaces turns "It's Clara" into "it s clara" while ASR's "its
# clara" stays two words -- so the same sentence scores as different, and the
# rule misses exactly the doubled greeting it exists to find, because greetings
# are full of contractions.
_APOSTROPHES = re.compile(r"['’ʼ]")
_PUNCTUATION = re.compile(r"[^\w\s]+")
_WHITESPACE = re.compile(r"\s+")


@dataclass(frozen=True)
class Segment:
    """One continuous stretch of speech by one party.

    `start` and `end` are seconds from the start of the call. `text` is what ASR
    heard for the agent, or the scripted line for the caller -- the caller's
    words are known exactly, because the harness is the one saying them.
    """

    start: float
    end: float
    text: str
    speaker: str


@dataclass(frozen=True)
class Finding:
    """One thing wrong with the call, in terms a person can act on."""

    rule: str
    detail: str
    # Where in the recording to listen. The whole point of the audio layer is
    # that a failure hands you the two seconds that broke instead of asking you
    # to place another call and hope.
    at: float


def normalise(text: str) -> str:
    """Text reduced to what two utterances being "the same" should depend on.

    Case and punctuation are ASR's guesses, not the agent's behaviour: the same
    line transcribed twice can differ by a comma and a capital letter, and
    treating that as a difference would hide the duplicate this looks for.
    """
    without_apostrophes = _APOSTROPHES.sub("", text.lower())
    lowered = _PUNCTUATION.sub(" ", without_apostrophes)
    return _WHITESPACE.sub(" ", lowered).strip()


def similarity(a: str, b: str) -> float:
    """How alike two utterances are, 0.0 to 1.0, after normalisation.

    Compared as sequences of WORDS, not of characters. Clara reads a medication
    list, so her own script contains pairs like "Did you take your Lipitor this
    morning?" and "Did you take your Metformin this morning?" -- a legitimate
    run-through that a duplicate rule must not fire on. Character-wise those
    score 0.872 against a 0.90 threshold; word-wise they score 0.857, while a
    genuine repeat still scores 1.0 either way. The margin is what calibration
    will eat into, and one shared word matters more than seven shared letters
    inside a drug name.
    """
    return SequenceMatcher(None, normalise(a).split(), normalise(b).split()).ratio()


def _word_count(text: str) -> int:
    normalised = normalise(text)
    return len(normalised.split()) if normalised else 0


def _in_time_order(segments: list[Segment]) -> list[Segment]:
    """Segments sorted by when they happened.

    Both rules below walk the list assuming time order -- one of them `break`s
    out of a scan on the first segment past its window. Nothing guarantees the
    caller supplies them sorted: they are merged from two per-track event logs,
    where interleaving is easy to get subtly wrong. Unsorted, a single late
    segment ends the scan early and the duplicate right after it is never seen,
    which is precisely the greeting-twice bug the rule exists for.
    """
    return sorted(segments, key=lambda s: (s.start, s.end))


def repeats_itself(
    text: str, *, threshold: float = DEFAULT_SIMILARITY, min_words: int = DEFAULT_MIN_WORDS
) -> float:
    """How much one utterance is just the same thing said twice, 0.0 to 1.0.

    A duplicate does not always arrive as two segments. Whether it does depends
    on how long a pause the model left between the two copies, and on the
    silence threshold the segmenter happens to be using -- neither of which has
    anything to do with whether the caller heard the same sentence twice.

    A real control call showed both halves of this. The doubled *greeting* came
    back as two segments and was caught; the doubled reply that followed it --
    "I am glad to hear that you are doing well. I am glad to hear that you are
    doing well." -- had a shorter pause, arrived as one segment, and sailed
    through a rule that only ever compares one segment with another. Same bug,
    same call, invisible.

    So the text is also split down the middle and its halves compared. Both
    halves must be substantial in their own right, which is what keeps ordinary
    repetition out: "did you take your Lipitor / did you take your Metformin"
    scores 0.8 against a 0.9 threshold, and short emphatic doubling like "yes,
    yes" never has the words to qualify.
    """
    words = normalise(text).split()
    half = len(words) // 2
    if half < min_words:
        return 0.0
    # The split point is searched, not assumed to be the middle, because the
    # two copies are rarely the same length -- ASR adds or drops a word, or the
    # model does. A single word is enough to defeat a fixed midpoint: "…doing
    # well. …doing well today." and "…help you with. …help with." both score
    # 0.875 split down the middle and slip under the bar, and both score 0.94
    # when the split lands on the real seam.
    #
    # The search is deliberately narrow. Two copies of the same sentence differ
    # by a word or two; anything wider starts finding "seams" in sentences that
    # merely rhyme with themselves. At this width the medication run-through --
    # "did you take your Lipitor / did you take your Metformin" -- still scores
    # 0.8 at every split point, well under the threshold.
    # Bounded so neither side of a split can fall below the minimum, rather
    # than skipping such splits inside the loop: as a bound it is structural,
    # as a conditional it was a branch no input could reach.
    first = max(min_words, half - SEAM_SEARCH)
    last = min(len(words) - min_words, half + SEAM_SEARCH)
    best = 0.0
    for split in range(first, last + 1):
        best = max(best, SequenceMatcher(None, words[:split], words[split:]).ratio())
    return best if best >= threshold else 0.0


def duplicate_utterances(
    segments: list[Segment],
    *,
    threshold: float = DEFAULT_SIMILARITY,
    window_s: float = DEFAULT_WINDOW_S,
    min_words: int = DEFAULT_MIN_WORDS,
) -> list[Finding]:
    """The agent said the same substantial thing twice in quick succession.

    Twice across two segments, or twice inside one -- see `repeats_itself` for
    why both have to be checked.

    Only the agent's own speech is compared. The caller's lines are the
    harness's script, and a case that deliberately repeats a line -- to test
    what the agent does when asked twice -- must not be reported as a bug in the
    agent.
    """
    spoken = [
        s
        for s in _in_time_order(segments)
        if s.speaker == AGENT and _word_count(s.text) >= min_words
    ]
    findings = []
    reported = set()
    for segment in spoken:
        doubled = repeats_itself(segment.text, threshold=threshold, min_words=min_words)
        if doubled:
            reported.add(id(segment))
            findings.append(
                Finding(
                    rule="no_duplicate_utterance",
                    detail=(
                        f"the agent said the same thing twice within one utterance, "
                        f"{doubled:.0%} alike: {segment.text!r}"
                    ),
                    at=segment.start,
                )
            )
    for index, earlier in enumerate(spoken):
        for later in spoken[index + 1 :]:
            # Segments are compared from the end of the first to the start of
            # the second: two long utterances that merely overlap a window
            # boundary are not "in quick succession".
            if later.start - earlier.end > window_s:
                break
            # Each repeat is reported once, against the first thing it repeated.
            # Reporting every pair means an agent stuck looping its greeting
            # produces N*(N-1)/2 findings -- six for four repeats -- and one bug
            # fills the whole report.
            if id(later) in reported:
                continue
            ratio = similarity(earlier.text, later.text)
            if ratio >= threshold:
                reported.add(id(later))
                findings.append(
                    Finding(
                        rule="no_duplicate_utterance",
                        detail=(
                            f"the agent said the same thing twice, "
                            f"{ratio:.0%} alike and {later.start - earlier.end:.1f}s apart: "
                            f"{earlier.text!r}"
                        ),
                        at=later.start,
                    )
                )
    return findings


def silences(
    segments: list[Segment], *, call_end: float | None = None
) -> list[tuple[float, float]]:
    """Every stretch of dead air the caller was left sitting in.

    Measured from the moment the caller stops to the moment the agent starts,
    because that is the silence a listener experiences as the assistant having
    frozen. Silence after the *agent* stops is the caller's turn to think and is
    not the agent's fault.

    A caller turn the agent never answers at all is the worst case of this, not
    an absence of one, so it is measured to the end of the call.

    Two things this has to get right, both of which are the difference between a
    rule people trust and a rule people mute:

    **One turn, not one segment.** VAD and ASR routinely chop a single caller
    turn into several segments. Measuring from each of them to the next agent
    reply counts the caller's own later speech as dead air -- "Morning" at 0-2s,
    "and I slept badly" at 2.3-10s, agent at 10.4s reads as 8.4s of silence when
    the caller was talking for 7.7 of it. So a gap is only measured from the
    LAST caller segment before the agent answers. The same collapse stops a
    single freeze at the end of a call being reported once per trailing segment.

    **An answer that overlaps is still an answer.** A strict "starts after the
    caller finished" test misses barge-in and the early starts that are ordinary
    on a speech-to-speech model, and then falls through to the never-answered
    branch -- reporting the whole turn as dead air on a call the agent answered
    *before* the caller stopped. An agent segment counts as the answer if it
    ends after the caller's turn ends, and an overlap is zero silence, not
    negative.
    """
    ordered = _in_time_order(segments)
    gaps = []
    for index, segment in enumerate(ordered):
        if segment.speaker != CALLER:
            continue
        rest = ordered[index + 1 :]
        following = next((s for s in rest if s.speaker == AGENT and s.end > segment.end), None)
        # Another caller segment before the agent answers means this one was not
        # the end of the turn; the gap belongs to the last segment of the run.
        boundary = following.start if following is not None else call_end
        if boundary is not None and any(s.speaker == CALLER and s.start < boundary for s in rest):
            continue
        if following is not None:
            gaps.append((segment.end, max(segment.end, following.start)))
        elif call_end is not None and call_end > segment.end:
            gaps.append((segment.end, call_end))
    return gaps


def long_silences(
    segments: list[Segment],
    *,
    call_end: float,
    limit_s: float = DEFAULT_MAX_SILENCE_S,
) -> list[Finding]:
    """Dead air past the limit, as findings.

    `call_end` is REQUIRED, and that is the whole design of it. Without a
    boundary, a caller turn the agent never answered has nothing to measure to
    and produces no finding -- so the loudest failure in the layer, the agent
    freezing outright, reads as a clean call. Defaulting it does not help
    either: the obvious default, the end of the last segment, IS the caller's
    own end in exactly that case, so the gap is zero and the freeze still
    vanishes. The only version that cannot be forgotten is the one that will not
    run without it, and the harness always knows how long the recording is.
    """
    findings = []
    for start, end in silences(segments, call_end=call_end):
        gap = end - start
        if gap > limit_s:
            findings.append(
                Finding(
                    rule="max_silence",
                    detail=(
                        f"{gap:.1f}s of silence after the caller stopped, limit is {limit_s:.1f}s"
                    ),
                    at=start,
                )
            )
    return findings


def analyse(
    segments: list[Segment],
    *,
    call_end: float,
    similarity_threshold: float = DEFAULT_SIMILARITY,
    window_s: float = DEFAULT_WINDOW_S,
    min_words: int = DEFAULT_MIN_WORDS,
    max_silence_s: float = DEFAULT_MAX_SILENCE_S,
) -> list[Finding]:
    """Every rule, over one call, in the order they happened.

    Ordered by time rather than by rule so the report reads as a walk through
    the recording -- which is how someone listening to it will use it.
    """
    findings = duplicate_utterances(
        segments,
        threshold=similarity_threshold,
        window_s=window_s,
        min_words=min_words,
    ) + long_silences(segments, limit_s=max_silence_s, call_end=call_end)
    return sorted(findings, key=lambda f: f.at)


def format_findings(findings: list[Finding]) -> str:
    """The findings as something to read next to a WAV file."""
    if not findings:
        return "No audio findings."
    lines = [f"{len(findings)} audio finding(s):"]
    for finding in findings:
        lines.append(f"  [{finding.at:7.2f}s] {finding.rule}: {finding.detail}")
    return "\n".join(lines)

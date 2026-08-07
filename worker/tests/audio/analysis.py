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

# Dead air after the caller stops talking before the agent answers.
#
# A guess until there is a baseline, and stated as one: the spec expects the
# first week of real calls to move it. An `agent_swap` costs a ~850ms socket
# rebuild that is known and tolerable; a tool call that lost its filler line is
# not.
DEFAULT_MAX_SILENCE_S = 4.0

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
    """How alike two utterances are, 0.0 to 1.0, after normalisation."""
    return SequenceMatcher(None, normalise(a), normalise(b)).ratio()


def _word_count(text: str) -> int:
    normalised = normalise(text)
    return len(normalised.split()) if normalised else 0


def duplicate_utterances(
    segments: list[Segment],
    *,
    threshold: float = DEFAULT_SIMILARITY,
    window_s: float = DEFAULT_WINDOW_S,
    min_words: int = DEFAULT_MIN_WORDS,
) -> list[Finding]:
    """The agent said the same substantial thing twice in quick succession.

    Only the agent's own speech is compared. The caller's lines are the
    harness's script, and a case that deliberately repeats a line -- to test
    what the agent does when asked twice -- must not be reported as a bug in the
    agent.
    """
    spoken = [s for s in segments if s.speaker == AGENT and _word_count(s.text) >= min_words]
    findings = []
    for index, earlier in enumerate(spoken):
        for later in spoken[index + 1 :]:
            # Segments are compared from the end of the first to the start of
            # the second: two long utterances that merely overlap a window
            # boundary are not "in quick succession".
            if later.start - earlier.end > window_s:
                break
            ratio = similarity(earlier.text, later.text)
            if ratio >= threshold:
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
    """
    gaps = []
    for index, segment in enumerate(segments):
        if segment.speaker != CALLER:
            continue
        following = next(
            (s for s in segments[index + 1 :] if s.speaker == AGENT and s.start >= segment.end),
            None,
        )
        if following is not None:
            gaps.append((segment.end, following.start))
        elif call_end is not None and call_end > segment.end:
            gaps.append((segment.end, call_end))
    return gaps


def long_silences(
    segments: list[Segment],
    *,
    limit_s: float = DEFAULT_MAX_SILENCE_S,
    call_end: float | None = None,
) -> list[Finding]:
    """Dead air past the limit, as findings."""
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
    call_end: float | None = None,
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

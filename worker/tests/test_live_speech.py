"""Verbatim-line directives for a Gemini Live session.

Data-only, so these run in the dev-only test group. The livekit half — that
`ArhiteqAgent` actually pins the greeting and never passes ``instructions=`` to
``generate_reply`` — is in `test_live_greeting.py`.
"""

from arhiteq_worker.live_speech import (
    live_handoff_instructions,
    live_opening_instructions,
    live_placeholder_note,
    live_verbatim_instructions,
)

GREETING = "Hi Tom, it's Clara — how are you doing today?"


def test_the_opening_block_carries_the_greeting_verbatim() -> None:
    block = live_opening_instructions(GREETING)
    # Delimited on its own line so a greeting containing quotes, colons or
    # newlines cannot blur into the surrounding prose.
    assert f"<<<SAY EXACTLY>>>\n{GREETING}\n<<<END>>>" in block


def test_the_opening_block_scopes_itself_to_the_first_turn() -> None:
    # It stays in the instructions for the whole call, so it has to read as
    # "your first turn", never as a standing order to repeat the greeting.
    block = live_opening_instructions(GREETING)
    assert "first spoken turn of this call" in block
    assert "next spoken turn" not in block


def test_the_mid_call_block_scopes_itself_to_the_next_turn() -> None:
    block = live_verbatim_instructions("Great, let me transfer you.")
    assert "next spoken turn" in block
    assert "first spoken turn" not in block


def test_the_note_disarms_the_placeholder_user_turn() -> None:
    """The `"."` the google plugin sends must not read as the caller.

    Left unexplained it cost a production call its whole conversation: the
    model scored the placeholder as an unanswered greeting and fired
    log_mood → log_outcome(user_busy) → end_call four seconds in.
    """
    note = live_placeholder_note()
    assert '"."' in note
    assert "never log an outcome or end the call over it" in note


def test_the_opening_block_carries_the_note_itself() -> None:
    # It is the one block installed before the session starts, so it cannot
    # rely on anything else having put the note there first.
    assert live_placeholder_note() in live_opening_instructions(GREETING)


def test_the_mid_call_block_leaves_the_note_to_the_instructions_it_extends() -> None:
    # `_FlowWiring.set_instructions` puts the note on every node, so repeating
    # it here would only stack two copies in the same prompt.
    assert live_placeholder_note() not in live_verbatim_instructions("Hi.")


def test_the_handoff_block_forbids_opening_the_call_again() -> None:
    """The symptom the block exists for.

    On the last hand-back of call call_6ed66e6ae63f4a95f6f9294e42dd641f the
    check-in agent answered a caller who had just said goodbye with its opening
    greeting, because a rebuilt socket and a bare prompt read as a call that
    was only now starting.
    """
    block = live_handoff_instructions()
    assert "already in progress" in block
    assert "do not greet" in block


def test_the_handoff_block_names_what_the_reconnect_erased() -> None:
    # Both halves matter: the tool calls (so the destination agent does not
    # re-run a lookup and re-speak its answer) and the handoff itself (so it
    # does not bounce the subject back to the agent that gave it up).
    block = live_handoff_instructions()
    assert "tool calls made before the handoff" in block
    assert "handing it straight back" in block.lower()


def test_the_handoff_block_stops_applying_once_the_handoff_turn_is_over() -> None:
    """It is a suffix with no removal path — the next swap is what replaces it.

    So the two rules that would be wrong later in the call are scoped in the
    text itself: opening the call is barred for the first turn after the
    handoff, and the ban on re-running a lookup covers the answers already in
    the transcript, not a request the caller makes afterwards.
    """
    block = live_handoff_instructions()
    assert "on your first turn here" in block
    assert "later in the call" in block


def test_the_handoff_block_leaves_self_introduction_to_the_agents_own_prompt() -> None:
    # Suppressing the greeting is the point (Clara's specialists are the same
    # voice and must not be caught changing), but this block is appended AFTER
    # the destination prompt and so has the last word: an agent whose own
    # instructions say to name itself on taking over keeps that.
    assert "unless your instructions above tell you" in live_handoff_instructions()


def test_the_handoff_block_carries_the_note_itself() -> None:
    # update_instructions replaces the whole prompt, so from the first swap
    # onwards this block is the only thing putting the note back — and a Live
    # session without it can read the plugin's "." as caller silence and hang up.
    assert live_placeholder_note() in live_handoff_instructions()


def test_a_block_is_appended_not_substituted() -> None:
    # Every block is a suffix: callers concatenate them onto a prompt, so the
    # leading separator is the module's job, not every call site's.
    for block in (
        live_opening_instructions(GREETING),
        live_verbatim_instructions("Hi."),
        live_placeholder_note(),
        live_handoff_instructions(),
    ):
        assert block.startswith("\n\n## ")

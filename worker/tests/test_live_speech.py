"""Verbatim-line directives for a Gemini Live session.

Data-only, so these run in the dev-only test group. The livekit half — that
`ArhiteqAgent` actually pins the greeting and never passes ``instructions=`` to
``generate_reply`` — is in `test_live_greeting.py`.
"""

from arhiteq_worker.live_speech import live_opening_instructions, live_verbatim_instructions

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


def test_both_blocks_disarm_the_placeholder_user_turn() -> None:
    """The `"."` the google plugin sends must not read as the caller.

    Left unexplained it cost a production call its whole conversation: the
    model scored the placeholder as an unanswered greeting and fired
    log_mood → log_outcome(user_busy) → end_call four seconds in.
    """
    for block in (live_opening_instructions(GREETING), live_verbatim_instructions("Hi.")):
        assert '"."' in block
        assert "never log an outcome or end the call over it" in block


def test_a_block_is_appended_not_substituted() -> None:
    # Both blocks are suffixes: callers concatenate them onto a prompt, so a
    # leading separator is the module's job, not every call site's.
    for block in (live_opening_instructions(GREETING), live_verbatim_instructions("Hi.")):
        assert block.startswith("\n\n## ")

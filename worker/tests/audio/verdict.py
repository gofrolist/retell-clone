"""What a run's exit code means, kept where it can be tested.

The exit code is the only part of a run a scheduler reads, and it carries a
distinction the whole layer depends on:

    0  the call ran and sounded fine
    1  the call ran and the rules found something
    2  the run was broken

Only 1 is a statement about the prompt. A 2 is a statement about the harness or
the deployment — a missing key, an agent id that does not exist, a provider
refusing, nobody joining the room. A nightly that treats those as findings
reports a prompt regression every time someone rotates a credential, and a
suite that cries wolf like that stops being read. That is why this lives in its
own module rather than as a few `return` statements inside the driver: the
driver imports `livekit` and cannot run in CI, and this is exactly the decision
that should not go untested.
"""

from __future__ import annotations

CLEAN = 0
FINDINGS = 1
BROKEN = 2

# Ways a call can end that mean the run measured nothing worth grading.
#
# `max_call_s` is in here and is the easy one to miss: the call did happen and
# produced findings, but the script never finished, so the findings cover half
# the case. Reporting that as a prompt verdict grades a conversation that was
# cut off, and the half that was never spoken is silently scored as fine.
BROKEN_REASONS = frozenset(
    {
        "agent_never_joined",
        "hard_timeout",
        "error",
        "cancelled",
        "max_call_s",
        "room_closed",
    }
)

# `agent_ended_call` is deliberately NOT in there, and it is the one that looks
# like it should be. It shares a symptom with `max_call_s` — the script did not
# finish — but not a cause: the harness giving up says nothing about anything,
# while the agent deciding the call is over is a fact about the prompt, and
# frequently the exact fact a case is grading (an agent that hangs up before
# saying goodbye is the bug, not a broken run). The lines the hangup cut off are
# counted instead and handed to `expectations.check`, which marks what depended
# on them unproven — so a reader can tell an expectation that failed from one the
# call never reached, in the finding itself rather than in a warning elsewhere.
#
# `room_closed` is its neighbour and the reason that exemption is safe. The
# caller's room can also go away because the caller's own connection failed, a
# token expired, or the server shut down; those end the script exactly the way
# a hangup does, and folding them into `agent_ended_call` would make a harness
# failure exit CLEAN. `room_close_ended_the_call` below is where the two are
# told apart, and this is why it matters that they are.


# LiveKit `DisconnectReason` names that mean the WORKER ended the call.
#
# Here rather than in `caller.py` for the same reason the exit code is: it
# decides between a verdict about the prompt and a verdict about nothing, and
# `caller.py` imports `livekit` and cannot run in CI. Names rather than enum
# members so this module stays stdlib-only.
#
# `RuntimeControl.end_call` finishes a call with `delete_room`
# (`arhiteq_worker/main.py`), and `delete_room_on_close` does the same when the
# session closes for any other reason — so an agent hanging up reaches the
# caller as the room being deleted, not only as the agent's participant
# leaving.
WORKER_ENDED_ROOM = frozenset({"ROOM_DELETED", "ROOM_CLOSED"})


def room_close_ended_the_call(reason_name: str) -> bool:
    """Whether a room-level disconnect was the agent hanging up.

    Everything else — the caller's own network dropping, a token expiring, the
    signal socket closing, the server shutting down — is this harness losing
    its connection. Both end the script in the same place, and telling them
    apart is the difference between exit 0 and exit 2: `agent_ended_call` is
    deliberately not a broken reason, so a harness failure counted as a hangup
    is reported as a call that ran and sounded fine.

    An unrecognised reason is NOT a hangup. A new `DisconnectReason` in a later
    livekit-rtc is far more likely to be a new way for a connection to fail than
    a new way for an agent to say goodbye, and the safe default is the one that
    admits the run proved nothing.
    """
    return reason_name in WORKER_ENDED_ROOM


def exit_code(*, stopped: str, segments: int, findings: int) -> int:
    """The verdict for one run.

    A run with no segments at all is broken however it ended: the recording
    contains nothing that was said, so "no findings" is not an observation
    about the prompt but an absence of any observation at all.
    """
    if stopped in BROKEN_REASONS or segments == 0:
        return BROKEN
    return FINDINGS if findings else CLEAN


def describe(code: int) -> str:
    return {
        CLEAN: "the call ran and sounded fine",
        FINDINGS: "the rules found something",
        BROKEN: "the run was broken — this says nothing about the prompt",
    }[code]

"""`SwapGuard`: the agent_swap ping-pong breaker.

Data-only, so these run in the dev-only test group. `_do_agent_swap` — the one
caller — is a closure inside `entrypoint`, reachable only with a live job
context, so what it does with a refusal (return the reason to the model without
fetching a config, touching the instructions or paying for a reconnect), and
that it arms the guard on Live sessions only, are held by review rather than by
a test.

The scenario throughout is the one that produced the guard, production call
call_6ed66e6ae63f4a95f6f9294e42dd641f: the caller asks the billing specialist
whether their subscription is active, billing cannot answer and hands back, and
the check-in agent reads the same unanswered question and hands it straight
over again — a loop neither side can see, because the reconnect a swap forces
replays the spoken turns without the tool calls.

`tool_seq` is `CallState.tool_seq` read at the top of the swap, so it always
counts the swap itself: consecutive swaps with nothing between them are N and
N+1.
"""

from arhiteq_worker.state import SwapGuard

CHECKIN = "agent_checkin"
BILLING = "agent_billing"
PHARMACY = "agent_pharmacy"


def test_the_first_swap_of_a_call_is_never_a_ping_pong() -> None:
    guard = SwapGuard()
    assert not guard.is_ping_pong(current=CHECKIN, destination=BILLING, user_turns=3, tool_seq=7)


def test_a_hand_back_is_allowed_the_caller_asked_for_it() -> None:
    """The specialist was reached on a caller turn and hands back on a later one."""
    guard = SwapGuard()
    guard.record(source=CHECKIN, destination=BILLING, user_turns=3, tool_seq=7)
    assert not guard.is_ping_pong(current=BILLING, destination=CHECKIN, user_turns=4, tool_seq=8)


def test_handing_the_subject_straight_back_is_refused() -> None:
    guard = SwapGuard()
    guard.record(source=BILLING, destination=CHECKIN, user_turns=4, tool_seq=12)
    assert guard.is_ping_pong(current=CHECKIN, destination=BILLING, user_turns=4, tool_seq=13)


def test_a_caller_turn_clears_the_guard() -> None:
    """The caller is one of the two signals.

    Once they have spoken, a swap to the same specialist is a new request —
    including a request that genuinely belongs there — not the loop closing.
    """
    guard = SwapGuard()
    guard.record(source=BILLING, destination=CHECKIN, user_turns=4, tool_seq=12)
    assert not guard.is_ping_pong(current=CHECKIN, destination=BILLING, user_turns=5, tool_seq=13)


def test_work_done_before_the_hand_back_clears_the_guard() -> None:
    """The round trip the split agents are built on, inside one caller turn.

    "How much is my metformin?" — check-in routes to pharmacy (tool 7),
    pharmacy looks the price up (tool 8) and hands back having answered
    (tool 9). Clara's specialist prompt requires exactly this ("your subject is
    finished the moment it is answered"), and refusing it would leave the
    caller on the pharmacy agent until they spoke again.
    """
    guard = SwapGuard()
    guard.record(source=CHECKIN, destination=PHARMACY, user_turns=3, tool_seq=7)
    assert not guard.is_ping_pong(current=PHARMACY, destination=CHECKIN, user_turns=3, tool_seq=9)


def test_a_bounce_with_nothing_between_the_two_swaps_is_still_the_loop() -> None:
    # The counterpart of the test above, and the difference the guard turns on:
    # consecutive tool_seq means the arriving agent called nothing of its own —
    # it bounced the subject on sight.
    guard = SwapGuard()
    guard.record(source=CHECKIN, destination=PHARMACY, user_turns=3, tool_seq=7)
    assert guard.is_ping_pong(current=PHARMACY, destination=CHECKIN, user_turns=3, tool_seq=8)


def test_only_the_agent_that_handed_off_is_barred() -> None:
    # A hand-back does not freeze the whole roster: the check-in agent may still
    # route the caller onwards to a specialist that has not just given up.
    guard = SwapGuard()
    guard.record(source=BILLING, destination=CHECKIN, user_turns=4, tool_seq=12)
    assert not guard.is_ping_pong(current=CHECKIN, destination=PHARMACY, user_turns=4, tool_seq=13)


def test_a_third_agent_swapping_to_the_source_is_not_the_loop() -> None:
    # A -> B recorded, then C -> A. Nothing was handed back to C, so C handing
    # the call to A is an ordinary route, not a bounce.
    guard = SwapGuard()
    guard.record(source=CHECKIN, destination=BILLING, user_turns=3, tool_seq=7)
    assert not guard.is_ping_pong(current=PHARMACY, destination=CHECKIN, user_turns=3, tool_seq=8)


def test_only_the_most_recent_swap_is_held_against_the_next_one() -> None:
    # The bounce that matters is the one just made; an older pair must not keep
    # barring a route for the rest of the call.
    guard = SwapGuard()
    guard.record(source=BILLING, destination=CHECKIN, user_turns=4, tool_seq=12)
    guard.record(source=CHECKIN, destination=PHARMACY, user_turns=4, tool_seq=13)
    assert not guard.is_ping_pong(current=PHARMACY, destination=BILLING, user_turns=4, tool_seq=14)


def test_the_production_loop_is_broken_at_the_second_leg() -> None:
    """The full sequence from the call above, swap by swap."""
    guard = SwapGuard()

    # "Can you tell me about my billing?" — the route out.
    assert not guard.is_ping_pong(current=CHECKIN, destination=BILLING, user_turns=3, tool_seq=7)
    guard.record(source=CHECKIN, destination=BILLING, user_turns=3, tool_seq=7)

    # "Do I have an active subscription?" — billing cannot, and hands back
    # without looking anything up.
    assert not guard.is_ping_pong(current=BILLING, destination=CHECKIN, user_turns=4, tool_seq=8)
    guard.record(source=BILLING, destination=CHECKIN, user_turns=4, tool_seq=8)

    # The leg that cost the call: check-in routes the same question back, on the
    # same caller turn, having done nothing with it.
    assert guard.is_ping_pong(current=CHECKIN, destination=BILLING, user_turns=4, tool_seq=9)

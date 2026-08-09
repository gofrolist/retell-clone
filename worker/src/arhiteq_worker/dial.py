"""Outbound dial progress, read off livekit-sip's `sip.callStatus`.

Deliberately free of livekit imports: CI installs only the dev dependency
group (no livekit-agents runtime stack), so anything that imports `main` is
skipped there. Keeping the verdict here means the rule that actually decides
whether a call connected is covered by every CI run.
"""

# livekit-sip walks a leg through dialing -> ringing -> active (or -> hangup),
# and reports SIP-level automation (voicemail drops) as "automation".
ANSWERED_STATUSES = frozenset({"active", "automation"})


def dial_verdict(status: str | None, present: bool) -> bool | None:
    """Answered (True), gave up (False), or still dialing (None).

    `status` is the SIP leg's `sip.callStatus` and `present` whether the leg is
    still in the room. livekit-sip publishes the participant *before* it sends
    the INVITE, so `status is None` means "not dialed yet" — emphatically not
    "answered". Reading it as answered finalized carrier-rejected dials as
    `ended` / `user_hangup` with a ~1s duration.
    """
    if status in ANSWERED_STATUSES:
        return True
    if status == "hangup" or not present:
        return False
    return None

"""Deterministic ordering of verified pharmacy requests."""
URGENCY_RANK = {'HIGH': 0, 'NORMAL': 1}
CONTINUITY_RANK = {'CONTINUITY': 0, 'NO_CONTINUITY': 1, 'UNKNOWN': 1}
POLICY_VERSION = '1.0.0'


def priority_key(booking):
    override = booking['override_rank']
    return (0 if override is not None else 1, override or 0,
            URGENCY_RANK[booking['urgency']], CONTINUITY_RANK[booking['continuity']],
            booking['created_at'], booking['id'])


def priority_reasons(booking):
    reasons = [f"VERIFIED_URGENCY:{booking['urgency']}", f"CONTINUITY:{booking['continuity']}",
               f"WAITING_SINCE:{booking['created_at']}", 'TIE_BREAK:BOOKING_ID']
    if booking['override_rank'] is not None:
        reasons.insert(0, f"PHARMACIST_OVERRIDE:{booking['override_rank']}")
    return reasons

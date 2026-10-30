"""
A 'tool' is just a normal Python function that an agent is allowed to call.
This one does a simple, illustrative penalty calculation.

NOTE: the numbers/slabs here are MADE UP for learning. Later you replace this
body with your real penalty logic from your existing scripts - the agent
doesn't care what's inside, it just calls the tool.
"""


def calculate_penalty(shortfall_amount: float, days_short: int) -> dict:
    """Estimate a margin-shortfall penalty.

    shortfall_amount : how much margin the client is short, in rupees
    days_short       : how many consecutive days they've been short
    """
    # Illustrative slab: 1% per day of shortfall, with a higher rate after 3 days.
    if days_short <= 3:
        rate_per_day = 0.01
    else:
        rate_per_day = 0.015  # repeated default -> steeper rate

    penalty = shortfall_amount * rate_per_day * days_short

    return {
        "shortfall_amount": shortfall_amount,
        "days_short": days_short,
        "rate_per_day": rate_per_day,
        "penalty": round(penalty, 2),
        "note": "Illustrative calculation - replace with real slab logic later.",
    }

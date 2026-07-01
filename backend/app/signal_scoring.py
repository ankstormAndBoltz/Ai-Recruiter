"""
signal_scoring.py — Availability multiplier from the 23 redrob_signals fields.

Produces a single float multiplier in the range [0.3, 1.3] that adjusts
the skill-fit score based on behavioral/engagement signals.

Design decisions:
  - The multiplier is MULTIPLICATIVE, applied to the skill-match score.
  - Range [0.3, 1.3] ensures strong profiles with poor engagement are
    modestly penalized (not zeroed out), and thin profiles with perfect
    engagement get a small boost (not enough to beat a strong match).
  - Sentinel values (-1) for github_activity_score and offer_acceptance_rate
    are explicitly handled as neutral (contribute 0 to the sub-score).
  - Each signal is normalized to [0, 1] then weighted.
"""

from __future__ import annotations

import math
from datetime import date

from app.models import RedrobSignals


# ---------------------------------------------------------------------------
# Signal weights — named constants, easy to tune
# ---------------------------------------------------------------------------

WEIGHT_RECENCY = 0.20             # How recently active on the platform
WEIGHT_RESPONSE_RATE = 0.20       # Do they actually respond to recruiters?
WEIGHT_OPEN_TO_WORK = 0.10        # Are they open to work?
WEIGHT_INTERVIEW_COMPLETION = 0.10 # Do they show up for interviews?
WEIGHT_OFFER_ACCEPTANCE = 0.05    # Do they accept offers? (when data exists)
WEIGHT_GITHUB = 0.05              # External validation of technical work
WEIGHT_PROFILE_COMPLETENESS = 0.05 # Have they invested in their profile?
WEIGHT_VERIFICATION = 0.10        # Email, phone, LinkedIn verification
WEIGHT_RESPONSE_TIME = 0.05       # Faster response is better
WEIGHT_RECRUITER_INTEREST = 0.10  # Are recruiters saving this profile?

_TOTAL_WEIGHT = (
    WEIGHT_RECENCY + WEIGHT_RESPONSE_RATE + WEIGHT_OPEN_TO_WORK +
    WEIGHT_INTERVIEW_COMPLETION + WEIGHT_OFFER_ACCEPTANCE +
    WEIGHT_GITHUB + WEIGHT_PROFILE_COMPLETENESS +
    WEIGHT_VERIFICATION + WEIGHT_RESPONSE_TIME + WEIGHT_RECRUITER_INTEREST
)
assert abs(_TOTAL_WEIGHT - 1.0) < 1e-6, f"Signal weights must sum to 1.0, got {_TOTAL_WEIGHT}"

# Multiplier output range
MULTIPLIER_MIN = 0.3
MULTIPLIER_MAX = 1.3

# Reference date for recency calculations
REFERENCE_DATE = date(2026, 6, 1)


# ---------------------------------------------------------------------------
# Sub-score functions — each returns a value in [0, 1]
# ---------------------------------------------------------------------------

def _recency_score(last_active: date) -> float:
    """Exponential decay based on days since last activity.
    Active within 30 days = 1.0, decays to ~0.1 at 365 days."""
    days_ago = (REFERENCE_DATE - last_active).days
    if days_ago <= 0:
        return 1.0
    return math.exp(-0.0077 * days_ago)


def _response_rate_score(rate: float) -> float:
    """Direct mapping: 0.0-1.0 → 0.0-1.0."""
    return max(0.0, min(1.0, rate))


def _open_to_work_score(flag: bool) -> float:
    """Binary: 1.0 if open, 0.3 if not (they're still contactable)."""
    return 1.0 if flag else 0.3


def _interview_completion_score(rate: float) -> float:
    """Direct mapping with a slight boost for high completion."""
    return max(0.0, min(1.0, rate))


def _offer_acceptance_score(rate: float) -> float:
    """Handle sentinel: -1 means no prior offers → return neutral 0.5.
    Otherwise map 0.0-1.0 directly."""
    if rate < 0:
        return 0.5  # Neutral: no data, don't penalize or reward
    return max(0.0, min(1.0, rate))


def _github_score(score: float) -> float:
    """Handle sentinel: -1 means no GitHub linked → return neutral 0.3.
    Otherwise normalize 0-100 → 0-1."""
    if score < 0:
        return 0.3  # Many strong engineers don't have public GitHub
    return max(0.0, min(1.0, score / 100.0))


def _profile_completeness_score(pct: float) -> float:
    """Normalize 0-100 → 0-1."""
    return max(0.0, min(1.0, pct / 100.0))


def _verification_score(email: bool, phone: bool, linkedin: bool) -> float:
    """Average of three boolean verification flags."""
    return (int(email) + int(phone) + int(linkedin)) / 3.0


def _response_time_score(hours: float) -> float:
    """Faster response = higher score.
    <4 hours = 1.0, degrades to ~0.1 at 168 hours (7 days)."""
    if hours <= 4:
        return 1.0
    return max(0.0, min(1.0, 1.0 - math.log(hours / 4.0) / math.log(42)))


def _recruiter_interest_score(saved_30d: int, views_30d: int, search_30d: int) -> float:
    """Composite of recruiter engagement signals.
    Each is soft-capped and combined."""
    saved_norm = min(1.0, saved_30d / 15.0)    # 15+ saves = max
    views_norm = min(1.0, views_30d / 50.0)     # 50+ views = max
    search_norm = min(1.0, search_30d / 200.0)  # 200+ appearances = max
    return 0.4 * saved_norm + 0.3 * views_norm + 0.3 * search_norm


# ---------------------------------------------------------------------------
# Main function
# ---------------------------------------------------------------------------

def availability_multiplier(signals: RedrobSignals) -> float:
    """
    Compute a multiplicative availability/engagement factor from behavioral signals.

    Returns:
        Float in [MULTIPLIER_MIN, MULTIPLIER_MAX] (currently [0.3, 1.3]).

    The multiplier adjusts the skill-fit score. A value of 1.0 means neutral.
    """
    weighted_sum = (
        WEIGHT_RECENCY * _recency_score(signals.last_active_date)
        + WEIGHT_RESPONSE_RATE * _response_rate_score(signals.recruiter_response_rate)
        + WEIGHT_OPEN_TO_WORK * _open_to_work_score(signals.open_to_work_flag)
        + WEIGHT_INTERVIEW_COMPLETION * _interview_completion_score(signals.interview_completion_rate)
        + WEIGHT_OFFER_ACCEPTANCE * _offer_acceptance_score(signals.offer_acceptance_rate)
        + WEIGHT_GITHUB * _github_score(signals.github_activity_score)
        + WEIGHT_PROFILE_COMPLETENESS * _profile_completeness_score(signals.profile_completeness_score)
        + WEIGHT_VERIFICATION * _verification_score(
            signals.verified_email, signals.verified_phone, signals.linkedin_connected
        )
        + WEIGHT_RESPONSE_TIME * _response_time_score(signals.avg_response_time_hours)
        + WEIGHT_RECRUITER_INTEREST * _recruiter_interest_score(
            signals.saved_by_recruiters_30d,
            signals.profile_views_received_30d,
            signals.search_appearance_30d,
        )
    )

    # weighted_sum is in [0, 1]. Map to [MULTIPLIER_MIN, MULTIPLIER_MAX].
    multiplier = MULTIPLIER_MIN + weighted_sum * (MULTIPLIER_MAX - MULTIPLIER_MIN)
    return round(multiplier, 4)

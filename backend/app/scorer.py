"""
scorer.py — Combines all signals into a final ranking score.

Components:
  1. Structured requirement match score (must-haves + disqualifiers)
  2. Embedding similarity to ideal profile narrative
  3. Nice-to-have bonus
  4. Availability multiplier (from signal_scoring.py)
  5. Logistics fit multiplier

Hard-exclude: honeypots and disqualified candidates get score = 0.0.

All weights are named constants at the top — easy to defend and tune.
"""

from __future__ import annotations

from app.jd_matcher import MatchResult, LogisticsResult
from app.models import JobDescription


# ---------------------------------------------------------------------------
# Scoring weights — named constants, not inline magic numbers
# ---------------------------------------------------------------------------

# Must-have match: each matched must-have contributes this fraction of the total
# There are 4 must-haves; matching all 4 = full score (1.0)
WEIGHT_MUST_HAVE_TOTAL = 0.45
MUST_HAVE_COUNT = 4
WEIGHT_PER_MUST_HAVE = WEIGHT_MUST_HAVE_TOTAL / MUST_HAVE_COUNT  # 0.1125 each

# Embedding similarity to ideal profile narrative
WEIGHT_EMBEDDING_SIMILARITY = 0.30

# Nice-to-have bonus (5 possible nice-to-haves)
WEIGHT_NICE_TO_HAVE_TOTAL = 0.10
NICE_TO_HAVE_COUNT = 5
WEIGHT_PER_NICE_TO_HAVE = WEIGHT_NICE_TO_HAVE_TOTAL / NICE_TO_HAVE_COUNT  # 0.02 each

# Experience fit bonus — being in the ideal experience range
WEIGHT_EXPERIENCE_FIT = 0.05

# Education tier bonus
WEIGHT_EDUCATION = 0.05

# Career depth (product companies, diverse roles)
WEIGHT_CAREER_DEPTH = 0.05

# Total base weights should sum to 1.0
_BASE_TOTAL = (
    WEIGHT_MUST_HAVE_TOTAL + WEIGHT_EMBEDDING_SIMILARITY +
    WEIGHT_NICE_TO_HAVE_TOTAL + WEIGHT_EXPERIENCE_FIT +
    WEIGHT_EDUCATION + WEIGHT_CAREER_DEPTH
)
assert abs(_BASE_TOTAL - 1.0) < 1e-6, f"Base weights must sum to 1.0, got {_BASE_TOTAL}"

# Logistics multiplier range
LOGISTICS_MULTIPLIER_MIN = 0.5
LOGISTICS_MULTIPLIER_MAX = 1.1


# ---------------------------------------------------------------------------
# Logistics fit multiplier
# ---------------------------------------------------------------------------

def logistics_fit_multiplier(logistics: LogisticsResult) -> float:
    """
    Compute a multiplier based on location + notice period fit.

    Range: [LOGISTICS_MULTIPLIER_MIN, LOGISTICS_MULTIPLIER_MAX]
    i.e. [0.5, 1.1]
    """
    # Location component (0.0 - 1.0)
    location_scores = {
        "preferred": 1.0,
        "acceptable": 0.85,
        "india_other": 0.6,
        "international": 0.3,
        "none": 0.2,
    }
    loc_score = location_scores.get(logistics.location_match, 0.3)

    # Boost if willing to relocate and not in preferred location
    if logistics.willing_to_relocate and logistics.location_match not in ("preferred",):
        loc_score = min(1.0, loc_score + 0.15)

    # Notice period component (0.0 - 1.0)
    notice_scores = {
        "ideal": 1.0,       # ≤30 days
        "acceptable": 0.7,  # 31-90 days
        "high": 0.4,        # >90 days
    }
    notice_score = notice_scores.get(logistics.notice_period_fit, 0.5)

    # Work mode component
    work_mode_bonus = 0.05 if logistics.work_mode_compatible else 0.0

    # Combine: location is more important than notice period
    combined = 0.6 * loc_score + 0.35 * notice_score + work_mode_bonus

    # Map to multiplier range
    multiplier = LOGISTICS_MULTIPLIER_MIN + combined * (LOGISTICS_MULTIPLIER_MAX - LOGISTICS_MULTIPLIER_MIN)
    return round(min(LOGISTICS_MULTIPLIER_MAX, max(LOGISTICS_MULTIPLIER_MIN, multiplier)), 4)


# ---------------------------------------------------------------------------
# Experience fit score
# ---------------------------------------------------------------------------

def experience_fit_score(years: float, jd: JobDescription) -> float:
    """
    Score how well years of experience fits the JD's ideal range.
    Returns 0.0-1.0.

    Uses the experience range from the JobDescription:
      - ideal_min_years to ideal_max_years → score 1.0
      - min_years_experience to ideal_min_years → linear ramp 0.5-1.0
      - ideal_max_years to max_years_experience → linear decay 1.0-0.5
      - Outside the max → 0.2
    """
    ideal_min = jd.ideal_min_years
    ideal_max = jd.ideal_max_years
    hard_min = jd.min_years_experience
    hard_max = jd.max_years_experience

    if ideal_min <= years <= ideal_max:
        return 1.0
    elif hard_min <= years < ideal_min and ideal_min > hard_min:
        # Below ideal but acceptable — linear ramp
        return 0.5 + 0.5 * (years - hard_min) / (ideal_min - hard_min)
    elif ideal_max < years <= hard_max and hard_max > ideal_max:
        # Above ideal but acceptable — linear decay
        return 0.5 + 0.5 * (hard_max - years) / (hard_max - ideal_max)
    else:
        # Way outside range
        return 0.2


# ---------------------------------------------------------------------------
# Main scoring function
# ---------------------------------------------------------------------------

def final_score(
    match_result: MatchResult,
    embedding_similarity: float,
    availability_mult: float,
    is_honeypot: bool,
    jd: JobDescription,
    years_of_experience: float = 0.0,
    education_tier: str = "unknown",
    num_product_companies: int = 0,
) -> float:
    """
    Compute the final ranking score for a candidate.

    Args:
        match_result: Structured output from JDMatcher.match()
        embedding_similarity: Cosine similarity to ideal profile narrative (0-1)
        availability_mult: From signal_scoring.availability_multiplier()
        is_honeypot: From honeypot_checks.is_honeypot()
        jd: The JobDescription — used to read experience range thresholds
        years_of_experience: Candidate's total years of experience
        education_tier: Best education tier (tier_1..tier_4, unknown)
        num_product_companies: Number of non-consulting companies in career

    Returns:
        Float score. Higher = better fit.
        0.0 for honeypots and hard-disqualified candidates.
    """
    # Hard exclusions — always return 0.0
    if is_honeypot:
        return 0.0
    if match_result.is_disqualified:
        return 0.0

    # --- Component 1: Must-have matches ---
    must_have_score = match_result.must_have_count * WEIGHT_PER_MUST_HAVE

    # --- Component 2: Embedding similarity ---
    # Clip to [0, 1] (cosine similarity can be slightly negative)
    embed_score = max(0.0, min(1.0, embedding_similarity)) * WEIGHT_EMBEDDING_SIMILARITY

    # --- Component 3: Nice-to-have bonus ---
    nice_to_have_score = match_result.nice_to_have_count * WEIGHT_PER_NICE_TO_HAVE

    # --- Component 4: Experience fit ---
    exp_score = experience_fit_score(years_of_experience, jd) * WEIGHT_EXPERIENCE_FIT

    # --- Component 5: Education tier ---
    tier_scores = {
        "tier_1": 1.0,
        "tier_2": 0.7,
        "tier_3": 0.4,
        "tier_4": 0.2,
        "unknown": 0.3,
    }
    edu_score = tier_scores.get(education_tier, 0.3) * WEIGHT_EDUCATION

    # --- Component 6: Career depth ---
    # More product companies = better
    career_score = min(1.0, num_product_companies / 3.0) * WEIGHT_CAREER_DEPTH

    # --- Base score (before multipliers) ---
    base_score = (
        must_have_score + embed_score + nice_to_have_score +
        exp_score + edu_score + career_score
    )

    # --- Apply multipliers ---
    logistics_mult = logistics_fit_multiplier(match_result.logistics)
    adjusted = base_score * availability_mult * logistics_mult

    return round(adjusted, 6)

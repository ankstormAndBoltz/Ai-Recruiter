"""
scorer.py — Combines all signals into a final ranking score.

Implements the build plan's hybrid scoring formula (Stage 3):

    base = w1*semantic + w2*must_have_coverage + w3*career_fit + w4*behavioral
         =  0.25*sem   + 0.40*coverage         + 0.20*fit      + 0.15*behavioral

Then a logistics multiplier and a honeypot penalty are applied:
  - Honeypots are NO LONGER hard-excluded. Instead base *= 0.5 and the concern
    is surfaced in reasoning. This makes the auditing visible and lets a
    strong-but-flagged profile still be seen rather than silently deleted.
  - Behavioral engagement is folded in as a weighted component (see w4) using
    the narrowed [0.65, 1.15] multiplier, so passive strong-fit seniors are
    flagged, not cratered.

All weights are named constants at the top — easy to defend and tune.
"""

from __future__ import annotations

from app.jd_matcher import MatchResult, LogisticsResult
from app.models import JobDescription


# ---------------------------------------------------------------------------
# Scoring weights — build plan hybrid formula (sum to 1.0)
# ---------------------------------------------------------------------------

WEIGHT_SEMANTIC = 0.25            # Cosine similarity to JD ideal-profile narrative
WEIGHT_MUST_HAVE_COVERAGE = 0.40  # Depth-weighted must-have coverage (prof×dur×assess)
WEIGHT_CAREER_FIT = 0.20          # JD-specific career-fit (disqualifier bypasses)
WEIGHT_BEHAVIORAL = 0.15          # Behavioral/engagement signal

_BASE_TOTAL = (
    WEIGHT_SEMANTIC + WEIGHT_MUST_HAVE_COVERAGE +
    WEIGHT_CAREER_FIT + WEIGHT_BEHAVIORAL
)
assert abs(_BASE_TOTAL - 1.0) < 1e-6, f"Base weights must sum to 1.0, got {_BASE_TOTAL}"

# Honeypot down-weight (not exclusion).
HONEYPOT_PENALTY = 0.5

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

def _behavioral_component(availability_mult: float) -> float:
    """Map the narrowed availability multiplier [0.65, 1.15] onto a [0, 1]
    component so it can enter the weighted sum. 0.65→0.0, 1.15→1.0."""
    from app.signal_scoring import MULTIPLIER_MIN, MULTIPLIER_MAX

    span = MULTIPLIER_MAX - MULTIPLIER_MIN
    if span <= 0:
        return 0.0
    return max(0.0, min(1.0, (availability_mult - MULTIPLIER_MIN) / span))


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
    Compute the final ranking score for a candidate using the hybrid formula.

    Args:
        match_result: Structured output from JDMatcher.match() — provides
            depth-weighted must_have_coverage and career_fit.
        embedding_similarity: Cosine similarity to the JD's ideal-profile
            narrative in [0, 1] (real, per-candidate — no longer a constant).
        availability_mult: From signal_scoring.availability_multiplier()
            (now in the narrowed [0.65, 1.15] range).
        is_honeypot: From honeypot_checks.is_honeypot(). Down-weights (× 0.5),
            it no longer excludes the candidate.
        jd: The JobDescription (kept for signature/back-compat; thresholds now
            live in career_fit).
        years_of_experience, education_tier, num_product_companies: retained
            for back-compat; the hybrid formula folds these into career_fit.

    Returns:
        Float score in [0, 1] range (before logistics). Higher = better fit.
        Hard-disqualified candidates still return 0.0.
    """
    # Generic hard disqualifiers (experience floor, zero-skill) still exclude.
    if match_result.is_disqualified:
        return 0.0

    # --- Component 1: Semantic similarity (real embeddings) ---
    semantic = max(0.0, min(1.0, embedding_similarity))

    # --- Component 2: Must-have coverage (proficiency × duration × assessment) ---
    coverage = max(0.0, min(1.0, match_result.must_have_coverage))

    # --- Component 3: Career fit (JD-specific disqualifier bypasses) ---
    career_fit = max(0.0, min(1.0, match_result.career_fit))

    # --- Component 4: Behavioral signal ---
    behavioral = _behavioral_component(availability_mult)

    # --- Hybrid base score ---
    base_score = (
        WEIGHT_SEMANTIC * semantic +
        WEIGHT_MUST_HAVE_COVERAGE * coverage +
        WEIGHT_CAREER_FIT * career_fit +
        WEIGHT_BEHAVIORAL * behavioral
    )

    # --- Honeypot penalty (down-weight, not exclude) ---
    if is_honeypot:
        base_score *= HONEYPOT_PENALTY

    # --- Logistics multiplier (location + notice fit) ---
    logistics_mult = logistics_fit_multiplier(match_result.logistics)
    adjusted = base_score * logistics_mult

    return round(adjusted, 6)

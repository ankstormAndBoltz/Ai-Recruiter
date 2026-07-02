"""
ranking.py — Core ranking pipeline for the FastAPI backend.

Accepts a JobDescription inline (from the API request) so it works
for any role — not hardcoded to a specific JD.

Pipeline (build plan Stages 2–4):
  0. Pre-pass: compute per-skill 80th-percentile assessment thresholds across
     the whole candidate pool (used to boost validated skills in Stage 3).
  1. Run honeypot consistency checks → FLAG (not exclude); down-weight later.
  2. Compute availability multiplier from behavioral signals ([0.65, 1.15]).
  3. Run JD matcher (depth-weighted must-haves + JD-specific career fit).
  4. Compute real semantic similarity (candidate narrative vs JD narrative).
  5. Compute final hybrid score using scorer.py; honeypots × 0.5.
  6. Sort deterministically, return top_k results with evidence-cited reasoning.
"""

from __future__ import annotations

from app.models import Candidate, JobDescription, RankedCandidate, RankResponse
from app.honeypot_checks import is_honeypot
from app.signal_scoring import availability_multiplier
from app.jd_matcher import JDMatcher
from app.scorer import final_score
from app.reasoning_templates import reasoning
from app.utils import build_candidate_narrative, semantic_similarity


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

CONSULTING_FIRMS = {
    "tcs", "infosys", "wipro", "accenture", "cognizant", "capgemini",
    "hcl", "tech mahindra", "ltimindtree", "mphasis", "deloitte",
    "kpmg", "ey", "pwc",
}


def _count_product_companies(candidate: Candidate) -> int:
    """Count career entries at non-consulting companies."""
    count = 0
    for job in candidate.career_history:
        if not any(firm in job.company.lower() for firm in CONSULTING_FIRMS):
            count += 1
    return count


def _percentile(sorted_values: list[float], pct: float) -> float:
    """Linear-interpolation percentile of a pre-sorted list. pct in [0, 100]."""
    if not sorted_values:
        return 0.0
    if len(sorted_values) == 1:
        return sorted_values[0]
    rank = (pct / 100.0) * (len(sorted_values) - 1)
    lo = int(rank)
    hi = min(lo + 1, len(sorted_values) - 1)
    frac = rank - lo
    return sorted_values[lo] + frac * (sorted_values[hi] - sorted_values[lo])


def _assessment_thresholds(candidates: list[Candidate], pct: float = 80.0) -> dict[str, float]:
    """
    Compute the `pct`-th percentile assessment score for each skill across the
    whole pool. Used in Stage 3 to give a +10% boost to skills whose assessment
    beats the dataset's 80th percentile — rewarding *validated* depth.
    """
    by_skill: dict[str, list[float]] = {}
    for c in candidates:
        for name, score in c.redrob_signals.skill_assessment_scores.items():
            by_skill.setdefault(name.lower(), []).append(float(score))

    thresholds: dict[str, float] = {}
    for name, scores in by_skill.items():
        scores.sort()
        thresholds[name] = _percentile(scores, pct)
    return thresholds


def _get_best_education_tier(candidate: Candidate) -> str:
    """Get the highest education tier from the candidate's education history."""
    tiers = {"tier_1": 4, "tier_2": 3, "tier_3": 2, "tier_4": 1, "unknown": 0}
    best = "unknown"
    best_score = 0
    for edu in candidate.education:
        t = edu.tier
        if tiers.get(t, 0) > best_score:
            best = t
            best_score = tiers[t]
    return best


# ---------------------------------------------------------------------------
# Main ranking function
# ---------------------------------------------------------------------------

def rank_candidates(
    candidates: list[Candidate],
    job_description: JobDescription,
    top_k: int = 10,
) -> RankResponse:
    """
    Rank a list of candidates against the provided JobDescription.

    Args:
        candidates: List of validated Candidate objects.
        job_description: The JD provided by the recruiter in the request.
        top_k: Number of top candidates to return (max 100).

    Returns:
        RankResponse with the ranked candidates and metadata.
    """
    # Initialize a fresh JDMatcher (stateless — no YAML config needed)
    matcher = JDMatcher()

    total_processed = len(candidates)

    # --- Step 0: Pre-pass — 80th-percentile assessment thresholds per skill ---
    thresholds = _assessment_thresholds(candidates)

    # --- JD narrative for semantic similarity (fall back to description) ---
    jd_narrative = job_description.ideal_profile_narrative or job_description.description

    scored = []

    for candidate in candidates:
        # --- Step 1: Honeypot check — FLAG (not exclude) ---
        flagged = is_honeypot(candidate)

        # --- Step 2: JD matching (depth-weighted must-haves + career fit) ---
        match_result = matcher.match(candidate, job_description, thresholds)

        # Only generic hard disqualifiers (experience floor / zero skill) exclude.
        if match_result.is_disqualified:
            continue

        # --- Step 3: Availability multiplier ([0.65, 1.15]) ---
        avail_mult = availability_multiplier(candidate.redrob_signals)

        # --- Step 4: Real semantic similarity ---
        narrative = build_candidate_narrative(candidate)
        embed_sim = semantic_similarity(narrative, jd_narrative)

        # --- Step 5: Final hybrid score (honeypots down-weighted × 0.5) ---
        score = final_score(
            match_result=match_result,
            embedding_similarity=embed_sim,
            availability_mult=avail_mult,
            is_honeypot=flagged,
            jd=job_description,
            years_of_experience=candidate.profile.years_of_experience,
            education_tier=_get_best_education_tier(candidate),
            num_product_companies=_count_product_companies(candidate),
        )

        if score > 0:
            scored.append((candidate, score, match_result, flagged))

    # --- Step 6: Sort deterministically ---
    # Primary: score descending
    # Tie-break 1: must-have count descending
    # Tie-break 2: candidate_id ascending (lexicographic)
    scored.sort(key=lambda x: (-x[1], -x[2].must_have_count, x[0].candidate_id))

    # Take top_k
    top_results = scored[:top_k]

    # --- Step 7: Build response with evidence-cited reasoning ---
    ranked = []
    for rank, (candidate, score, match_result, flagged) in enumerate(top_results, start=1):
        reason_text = reasoning(candidate, match_result, rank, is_flagged=flagged)
        reason_text = reason_text.replace("\n", " ").replace("\r", " ").strip()

        ranked.append(RankedCandidate(
            rank=rank,
            candidate_id=candidate.candidate_id,
            score=round(score, 4),
            reasoning=reason_text,
            must_have_count=match_result.must_have_count,
            is_disqualified=match_result.is_disqualified,
        ))

    return RankResponse(
        total_processed=total_processed,
        total_eligible=len(scored),
        top_k=len(ranked),
        results=ranked,
    )

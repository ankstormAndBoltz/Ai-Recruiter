"""
ranking.py — Core ranking pipeline for the FastAPI backend.

Accepts a JobDescription inline (from the API request) so it works
for any role — not hardcoded to a specific JD.

Pipeline:
  1. Run honeypot consistency checks → hard-exclude bad profiles
  2. Compute availability multiplier from behavioral signals
  3. Run JD matcher against the provided JobDescription
  4. Compute final score using scorer.py
  5. Sort deterministically, return top_k results with reasoning
"""

from __future__ import annotations

from app.models import Candidate, JobDescription, RankedCandidate, RankResponse
from app.honeypot_checks import is_honeypot
from app.signal_scoring import availability_multiplier
from app.jd_matcher import JDMatcher
from app.scorer import final_score
from app.reasoning_templates import reasoning


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
    scored = []

    for candidate in candidates:
        # --- Step 1: Honeypot check (hard exclusion for bad data) ---
        if is_honeypot(candidate):
            continue

        # --- Step 2: JD matching against the inline job description ---
        match_result = matcher.match(candidate, job_description)

        # Skip hard-disqualified candidates
        if match_result.is_disqualified:
            continue

        # --- Step 3: Availability multiplier from behavioral signals ---
        avail_mult = availability_multiplier(candidate.redrob_signals)

        # --- Step 4: Compute final score ---
        score = final_score(
            match_result=match_result,
            embedding_similarity=0.5,   # Neutral; enable sentence-transformers for real per-candidate similarity
            availability_mult=avail_mult,
            is_honeypot=False,          # Already filtered above
            jd=job_description,
            years_of_experience=candidate.profile.years_of_experience,
            education_tier=_get_best_education_tier(candidate),
            num_product_companies=_count_product_companies(candidate),
        )

        if score > 0:
            scored.append((candidate, score, match_result))

    # --- Step 5: Sort deterministically ---
    # Primary: score descending
    # Tie-break 1: must-have count descending
    # Tie-break 2: candidate_id ascending (lexicographic)
    scored.sort(key=lambda x: (-x[1], -x[2].must_have_count, x[0].candidate_id))

    # Take top_k
    top_results = scored[:top_k]

    # --- Step 6: Build response with reasoning ---
    ranked = []
    for rank, (candidate, score, match_result) in enumerate(top_results, start=1):
        reason_text = reasoning(candidate, match_result, rank)
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

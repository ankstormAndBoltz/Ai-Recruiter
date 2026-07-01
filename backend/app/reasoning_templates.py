"""
reasoning_templates.py — Generate 1-2 sentence reasoning from real candidate data.

No LLM calls. Composes reasoning purely from extracted facts:
- Matched must-haves with evidence snippets
- Actual notice period, response rate, location
- Named employers from career history
- Honest caveats for lower-ranked candidates

Sentence structure varies based on which evidence is present.
"""

from __future__ import annotations

from app.models import Candidate
from app.jd_matcher import MatchResult


# ---------------------------------------------------------------------------
# Helper: extract key facts for reasoning
# ---------------------------------------------------------------------------

def _get_top_employer(candidate: Candidate) -> str:
    """Get the most relevant employer name (current or most recent)."""
    for job in candidate.career_history:
        if job.is_current:
            return job.company
    return candidate.career_history[0].company if candidate.career_history else "unknown"


def _get_role_description(candidate: Candidate) -> str:
    """Get current or most recent role title."""
    return candidate.profile.current_title


def _get_experience_summary(candidate: Candidate) -> str:
    """e.g. '6.0 years of experience'"""
    yoe = candidate.profile.years_of_experience
    return f"{yoe:.1f} years" if yoe != int(yoe) else f"{int(yoe)} years"


def _get_notice_text(candidate: Candidate) -> str:
    """Notice period in human-readable form."""
    days = candidate.redrob_signals.notice_period_days
    if days <= 0:
        return "immediately available"
    elif days <= 30:
        return f"{days}-day notice"
    elif days <= 60:
        return f"{days}-day notice period"
    else:
        return f"long notice period ({days} days)"


def _get_location_text(candidate: Candidate) -> str:
    """Location summary."""
    loc = candidate.profile.location
    country = candidate.profile.country
    if country.lower() == "india":
        return loc
    return f"{loc}, {country}"


# ---------------------------------------------------------------------------
# Main reasoning generator
# ---------------------------------------------------------------------------

def reasoning(candidate: Candidate, match_result: MatchResult, rank: int) -> str:
    """
    Generate 1-2 sentence reasoning for a candidate's ranking.

    Args:
        candidate: The parsed candidate.
        match_result: Structured match result from JDMatcher.
        rank: The candidate's assigned rank (1-100).

    Returns:
        A 1-2 sentence reasoning string using only facts from the
        candidate's own record. Varies structure based on which
        signals dominate.
    """
    title = _get_role_description(candidate)
    employer = _get_top_employer(candidate)
    exp = _get_experience_summary(candidate)
    notice = _get_notice_text(candidate)
    location = _get_location_text(candidate)
    response_rate = candidate.redrob_signals.recruiter_response_rate

    # Gather matched must-haves and nice-to-haves
    matched_mh = match_result.matched_must_haves
    matched_nh = match_result.matched_nice_to_haves
    unmatched_mh = match_result.unmatched_must_haves

    # Logistics
    loc_match = match_result.logistics.location_match

    # Build reasoning based on rank tier and available evidence
    if rank <= 10:
        return _top_tier_reasoning(
            title, employer, exp, notice, location, response_rate,
            matched_mh, matched_nh, loc_match, candidate,
        )
    elif rank <= 30:
        return _mid_tier_reasoning(
            title, employer, exp, notice, location, response_rate,
            matched_mh, matched_nh, unmatched_mh, loc_match, candidate,
        )
    elif rank <= 60:
        return _lower_mid_reasoning(
            title, employer, exp, notice, location, response_rate,
            matched_mh, unmatched_mh, loc_match, candidate,
        )
    else:
        return _tail_reasoning(
            title, employer, exp, notice, location, response_rate,
            matched_mh, unmatched_mh, loc_match, candidate,
        )


# ---------------------------------------------------------------------------
# Tier-specific reasoning builders
# ---------------------------------------------------------------------------

def _top_tier_reasoning(
    title, employer, exp, notice, location, response_rate,
    matched_mh, matched_nh, loc_match, candidate,
):
    """Ranks 1-10: Lead with strongest evidence, mention engagement."""
    parts = []
    parts.append(f"{title} at {employer} with {exp}")

    if matched_mh:
        best = matched_mh[0]
        if best.evidence:
            parts.append(f"demonstrating {best.evidence}")

    if response_rate >= 0.7:
        parts.append(f"highly responsive ({response_rate:.0%} recruiter response rate)")
    elif response_rate >= 0.4:
        parts.append(f"responsive ({response_rate:.0%} response rate)")

    if loc_match == "preferred":
        parts.append(f"{location}-based with {notice}")
    elif notice:
        parts.append(notice)

    sentence1 = "; ".join(parts[:3]) + "."

    extras = []
    if matched_nh:
        nh_names = [n.id.replace("_", " ") for n in matched_nh[:2]]
        extras.append(f"Additional strengths include {' and '.join(nh_names)}")
    if len(matched_mh) >= 3:
        extras.append(f"matches {len(matched_mh)} of 4 core requirements")

    sentence2 = ". ".join(extras) + "." if extras else ""
    return f"{sentence1} {sentence2}".strip()


def _mid_tier_reasoning(
    title, employer, exp, notice, location, response_rate,
    matched_mh, matched_nh, unmatched_mh, loc_match, candidate,
):
    """Ranks 11-30: Balanced — strengths plus a specific concern."""
    parts = []
    parts.append(f"{title} at {employer} ({exp})")

    if matched_mh:
        evidence_bits = [m.evidence for m in matched_mh[:2] if m.evidence]
        if evidence_bits:
            parts.append(f"with {'; '.join(evidence_bits)}")
        else:
            parts.append(f"matching {len(matched_mh)} core requirements")

    sentence1 = ", ".join(parts[:2]) + "."

    caveats = []
    if unmatched_mh:
        gap = unmatched_mh[0]
        caveats.append(f"gap in {gap.id.replace('_', ' ')}")
    if loc_match == "international":
        caveats.append(f"international location ({location})")
    elif loc_match == "india_other":
        caveats.append(f"would need relocation from {location}")
    if response_rate < 0.3:
        caveats.append(f"low response rate ({response_rate:.0%})")
    if candidate.redrob_signals.notice_period_days > 60:
        caveats.append(f"{candidate.redrob_signals.notice_period_days}-day notice period")

    sentence2 = f"Concern: {caveats[0]}." if caveats else f"Solid match with {notice}."
    return f"{sentence1} {sentence2}"


def _lower_mid_reasoning(
    title, employer, exp, notice, location, response_rate,
    matched_mh, unmatched_mh, loc_match, candidate,
):
    """Ranks 31-60: Lead with match count, explicit about gaps."""
    n_matched = len(matched_mh)

    sentence1 = (
        f"{title} at {employer} with {exp}; "
        f"matches {n_matched} of 4 core requirements."
    )

    if unmatched_mh:
        gaps = [m.id.replace("_", " ") for m in unmatched_mh[:2]]
        sentence2 = f"Missing: {', '.join(gaps)}."
    elif response_rate < 0.3:
        sentence2 = f"Low engagement ({response_rate:.0%} response rate) weakens candidacy."
    elif loc_match in ("international", "india_other"):
        sentence2 = f"Location ({location}) adds logistical friction."
    else:
        sentence2 = f"Moderate fit overall; {notice}."

    return f"{sentence1} {sentence2}"


def _tail_reasoning(
    title, employer, exp, notice, location, response_rate,
    matched_mh, unmatched_mh, loc_match, candidate,
):
    """Ranks 61-100: Honest about why they're near the cutoff."""
    n_matched = len(matched_mh)

    if n_matched <= 1:
        sentence1 = (
            f"{title} at {employer} ({exp}); "
            f"limited direct match to core requirements ({n_matched}/4)."
        )
    else:
        sentence1 = (
            f"{title} at {employer} ({exp}); "
            f"partial match ({n_matched}/4 core requirements)."
        )

    weaknesses = []
    if unmatched_mh:
        gaps = [m.id.replace("_", " ") for m in unmatched_mh[:2]]
        weaknesses.append(f"no evidence of {gaps[0]}")
    if response_rate < 0.2:
        weaknesses.append(f"very low engagement ({response_rate:.0%} response rate)")
    if loc_match == "international":
        weaknesses.append(f"international location ({location})")

    sentence2 = f"Key concern: {weaknesses[0]}." if weaknesses else f"Included for {notice} and adjacent experience."
    return f"{sentence1} {sentence2}"

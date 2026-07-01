"""
utils.py — General utility helpers for the Candidate Ranking System API.
"""

from __future__ import annotations

from app.models import Candidate


# ---------------------------------------------------------------------------
# Candidate text utilities
# ---------------------------------------------------------------------------

def build_candidate_narrative(candidate: Candidate) -> str:
    """
    Build a rich narrative string from a candidate's structured data.

    This text is what gets embedded by the embedding model for semantic
    similarity matching against the job description.

    Combines:
    - Headline and summary (professional identity)
    - Career history (roles, companies, descriptions)
    - Skills with proficiency levels
    - Education background
    """
    parts = []

    # Professional identity
    parts.append(f"{candidate.profile.headline}.")
    parts.append(candidate.profile.summary)

    # Career history — company, title, and what they actually did
    for job in candidate.career_history:
        tenure = f"{job.duration_months} months" if job.duration_months else ""
        current = " (current)" if job.is_current else ""
        parts.append(
            f"Role: {job.title} at {job.company} ({job.industry}, {job.company_size}){current}. "
            f"{tenure}. {job.description}"
        )

    # Skills — include proficiency for context
    if candidate.skills:
        skill_strs = [
            f"{s.name} ({s.proficiency}, {s.duration_months}mo)"
            for s in candidate.skills
        ]
        parts.append("Skills: " + "; ".join(skill_strs))

    # Education
    for edu in candidate.education:
        parts.append(
            f"Education: {edu.degree} in {edu.field_of_study} from {edu.institution} "
            f"({edu.start_year}-{edu.end_year}, {edu.tier})"
        )

    return " ".join(parts)


def get_candidate_summary(candidate: Candidate) -> dict:
    """Return a brief human-readable summary dict for a candidate."""
    return {
        "id": candidate.candidate_id,
        "name": candidate.profile.anonymized_name,
        "title": candidate.profile.current_title,
        "company": candidate.profile.current_company,
        "location": candidate.profile.location,
        "years_of_experience": candidate.profile.years_of_experience,
        "top_skills": [s.name for s in candidate.skills[:5]],
    }

"""
honeypot_checks.py — Pure functions detecting logically impossible candidate profiles.

Any candidate with ONE OR MORE consistency violations is FLAGGED as an
integrity concern. Per the build plan, flagged candidates are NOT silently
excluded: they are down-weighted hard in scoring (final score × 0.5) and the
concern is surfaced in the reasoning ("⚠️ INTEGRITY: ..."). A system that
flags "impossible dates detected" and down-weights is more trustworthy — and
more defensible to judges — than one that silently deletes candidates.

Each check is a standalone pure function:
    check_*(candidate) -> Optional[str]
    Returns None if clean, or a human-readable violation description.

The combiner functions:
    consistency_violations(candidate) -> list[str]
        Returns all violations found. Non-empty = integrity concern.
    is_honeypot(candidate) -> bool
        Convenience: True if any violation found (used to down-weight, not exclude).
    integrity_flag(candidate) -> Optional[str]
        A short reasoning-ready label for the first/primary violation, or None.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from app.models import Candidate


# ---------------------------------------------------------------------------
# Individual consistency checks
# ---------------------------------------------------------------------------

def check_signup_vs_last_active(c: Candidate) -> Optional[str]:
    """signup_date must be <= last_active_date."""
    sig = c.redrob_signals
    if sig.signup_date > sig.last_active_date:
        return (
            f"signup_date ({sig.signup_date}) is after "
            f"last_active_date ({sig.last_active_date})"
        )
    return None


def check_skill_proficiency_vs_duration(c: Candidate) -> Optional[str]:
    """A skill rated 'expert' or 'advanced' with 0 months of usage is impossible."""
    for skill in c.skills:
        if skill.proficiency in ("expert", "advanced") and skill.duration_months == 0:
            return (
                f"Skill '{skill.name}' rated '{skill.proficiency}' "
                f"but duration_months is 0"
            )
    return None


def check_education_date_ordering(c: Candidate) -> Optional[str]:
    """Education start_year must be <= end_year."""
    for edu in c.education:
        if edu.start_year > edu.end_year:
            return (
                f"Education at '{edu.institution}': start_year ({edu.start_year}) "
                f"> end_year ({edu.end_year})"
            )
    return None


def check_career_duration_vs_dates(c: Candidate) -> Optional[str]:
    """Stated duration_months should be roughly consistent with date span.
    A discrepancy of more than 12 months indicates a fabricated record."""
    reference_date = date(2026, 6, 1)

    for job in c.career_history:
        end = job.end_date if job.end_date else reference_date
        if job.start_date > end:
            return (
                f"Job '{job.company}/{job.title}': start_date ({job.start_date}) "
                f"is after end_date ({end})"
            )

        actual_months = (end.year - job.start_date.year) * 12 + (end.month - job.start_date.month)
        stated = job.duration_months

        if abs(actual_months - stated) > 12:
            return (
                f"Job '{job.company}/{job.title}': dates span ~{actual_months} months "
                f"but stated duration_months is {stated}"
            )
    return None


def check_employment_overlap(c: Candidate) -> Optional[str]:
    """Non-current employment entries should not overlap by more than 60 days."""
    completed_jobs = []
    for job in c.career_history:
        if job.end_date is not None:
            completed_jobs.append((job.start_date, job.end_date, job.company, job.title))

    completed_jobs.sort(key=lambda x: x[0])

    for i in range(len(completed_jobs) - 1):
        _, end1, company1, title1 = completed_jobs[i]
        start2, _, company2, title2 = completed_jobs[i + 1]
        overlap_days = (end1 - start2).days
        if overlap_days > 60:
            return (
                f"Employment overlap: '{company1}/{title1}' ends {end1}, "
                f"'{company2}/{title2}' starts {start2} "
                f"({overlap_days} days overlap)"
            )
    return None


def check_experience_exceeds_career_span(c: Candidate) -> Optional[str]:
    """years_of_experience should not massively exceed the actual career span."""
    if not c.career_history:
        return None

    reference_date = date(2026, 6, 1)
    earliest_start = min(job.start_date for job in c.career_history)
    career_span_years = (reference_date - earliest_start).days / 365.25
    stated_yoe = c.profile.years_of_experience

    if career_span_years > 0 and stated_yoe > career_span_years * 2.0:
        return (
            f"years_of_experience ({stated_yoe}) exceeds 2x career span "
            f"({career_span_years:.1f} years since {earliest_start})"
        )
    return None


def check_experience_exceeds_company_age(c: Candidate) -> Optional[str]:
    """A single job's duration_months cannot exceed the candidate's total experience."""
    total_experience_months = c.profile.years_of_experience * 12
    for job in c.career_history:
        if job.duration_months > total_experience_months + 6:
            return (
                f"Job '{job.company}/{job.title}': duration_months ({job.duration_months}) "
                f"exceeds total career ({total_experience_months:.0f} months)"
            )
    return None


def check_skill_duration_exceeds_experience(c: Candidate) -> Optional[str]:
    """A candidate cannot have used a skill for longer than their total career."""
    total_experience_months = c.profile.years_of_experience * 12
    max_plausible = total_experience_months + 48  # +4 years for college/pre-career

    for skill in c.skills:
        if skill.duration_months > max_plausible and skill.duration_months > 60:
            return (
                f"Skill '{skill.name}': duration_months ({skill.duration_months}) "
                f"far exceeds career span ({total_experience_months:.0f} months + 48 tolerance)"
            )
    return None


# ---------------------------------------------------------------------------
# Combiner
# ---------------------------------------------------------------------------

_ALL_CHECKS = [
    check_signup_vs_last_active,
    check_skill_proficiency_vs_duration,
    check_education_date_ordering,
    check_career_duration_vs_dates,
    check_employment_overlap,
    check_experience_exceeds_career_span,
    check_experience_exceeds_company_age,
    check_skill_duration_exceeds_experience,
]


# Human-readable short labels per check, for reasoning output.
_CHECK_LABELS = {
    "check_signup_vs_last_active": "impossible activity dates",
    "check_skill_proficiency_vs_duration": "skill proficiency without usage time",
    "check_education_date_ordering": "impossible education dates",
    "check_career_duration_vs_dates": "fabricated tenure dates",
    "check_employment_overlap": "overlapping employment dates",
    "check_experience_exceeds_career_span": "experience exceeds career span",
    "check_experience_exceeds_company_age": "job tenure exceeds total experience",
    "check_skill_duration_exceeds_experience": "skill duration exceeds career",
}


def consistency_violations(candidate: Candidate) -> list[str]:
    """
    Run all consistency checks on a candidate.

    Returns:
        List of human-readable violation descriptions.
        Non-empty list = candidate has an integrity concern → down-weight
        (× 0.5) and flag in reasoning; NOT a hard exclusion.
        Empty list = candidate passes all checks.
    """
    violations = []
    for check_fn in _ALL_CHECKS:
        result = check_fn(candidate)
        if result is not None:
            violations.append(f"[{check_fn.__name__}] {result}")
    return violations


def is_honeypot(candidate: Candidate) -> bool:
    """Convenience: True if any consistency violation found.

    Note: this no longer means "exclude". Callers down-weight flagged
    candidates (× 0.5) and surface the concern rather than dropping them.
    """
    return len(consistency_violations(candidate)) > 0


def integrity_flag(candidate: Candidate) -> Optional[str]:
    """
    Return a short, reasoning-ready label for the primary integrity concern,
    or None if the candidate is clean.

    Example: "fabricated tenure dates". Used to prepend
    "⚠️ Profile integrity concern: <label>. " in the reasoning text.
    """
    for check_fn in _ALL_CHECKS:
        if check_fn(candidate) is not None:
            return _CHECK_LABELS.get(check_fn.__name__, "consistency violation")
    return None

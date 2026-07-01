"""
models.py — Typed Pydantic models for the Candidate Ranking System API.

Mirrors the candidate data schema exactly. Each field name, nesting level,
and type matches the expected JSON input.
"""

from __future__ import annotations

from datetime import date
from typing import Optional

from pydantic import BaseModel, Field, field_validator


# ---------------------------------------------------------------------------
# Nested models — ordered leaf-first
# ---------------------------------------------------------------------------

class SalaryRange(BaseModel):
    """Expected salary in INR Lakhs Per Annum."""
    min: float = Field(ge=0)
    max: float = Field(ge=0)


class Profile(BaseModel):
    """Top-level profile summary fields."""
    anonymized_name: str
    headline: str
    summary: str
    location: str
    country: str
    years_of_experience: float = Field(ge=0, le=50)
    current_title: str
    current_company: str
    current_company_size: str
    current_industry: str

    @field_validator("current_company_size")
    @classmethod
    def validate_company_size(cls, v: str) -> str:
        allowed = {
            "1-10", "11-50", "51-200", "201-500",
            "501-1000", "1001-5000", "5001-10000", "10001+",
        }
        if v not in allowed:
            raise ValueError(f"current_company_size must be one of {allowed}, got '{v}'")
        return v


class CareerEntry(BaseModel):
    """A single entry in the candidate's career history."""
    company: str
    title: str
    start_date: date
    end_date: Optional[date] = None
    duration_months: int = Field(ge=0)
    is_current: bool
    industry: str
    company_size: str
    description: str

    @field_validator("company_size")
    @classmethod
    def validate_company_size(cls, v: str) -> str:
        allowed = {
            "1-10", "11-50", "51-200", "201-500",
            "501-1000", "1001-5000", "5001-10000", "10001+",
        }
        if v not in allowed:
            raise ValueError(f"company_size must be one of {allowed}, got '{v}'")
        return v


class Education(BaseModel):
    """A single education entry."""
    institution: str
    degree: str
    field_of_study: str
    start_year: int = Field(ge=1970, le=2030)
    end_year: int = Field(ge=1970, le=2035)
    grade: Optional[str] = None
    tier: str = "unknown"

    @field_validator("tier")
    @classmethod
    def validate_tier(cls, v: str) -> str:
        allowed = {"tier_1", "tier_2", "tier_3", "tier_4", "unknown"}
        if v not in allowed:
            raise ValueError(f"tier must be one of {allowed}, got '{v}'")
        return v


class Skill(BaseModel):
    """A single skill entry with proficiency and usage metadata."""
    name: str
    proficiency: str
    endorsements: int = Field(ge=0)
    duration_months: int = Field(ge=0, default=0)

    @field_validator("proficiency")
    @classmethod
    def validate_proficiency(cls, v: str) -> str:
        allowed = {"beginner", "intermediate", "advanced", "expert"}
        if v not in allowed:
            raise ValueError(f"proficiency must be one of {allowed}, got '{v}'")
        return v


class Certification(BaseModel):
    """A professional certification."""
    name: str
    issuer: str
    year: int


class Language(BaseModel):
    """A language the candidate speaks."""
    language: str
    proficiency: str

    @field_validator("proficiency")
    @classmethod
    def validate_proficiency(cls, v: str) -> str:
        allowed = {"basic", "conversational", "professional", "native"}
        if v not in allowed:
            raise ValueError(f"proficiency must be one of {allowed}, got '{v}'")
        return v


class RedrobSignals(BaseModel):
    """
    The 23 behavioral signals from the Redrob platform.

    Sentinel values:
    - github_activity_score = -1  → no GitHub linked
    - offer_acceptance_rate = -1  → no prior offers
    """
    profile_completeness_score: float = Field(ge=0, le=100)
    signup_date: date
    last_active_date: date
    open_to_work_flag: bool
    profile_views_received_30d: int = Field(ge=0)
    applications_submitted_30d: int = Field(ge=0)
    recruiter_response_rate: float = Field(ge=0, le=1)
    avg_response_time_hours: float = Field(ge=0)
    skill_assessment_scores: dict[str, float] = Field(default_factory=dict)
    connection_count: int = Field(ge=0)
    endorsements_received: int = Field(ge=0)
    notice_period_days: int = Field(ge=0, le=180)
    expected_salary_range_inr_lpa: SalaryRange
    preferred_work_mode: str
    willing_to_relocate: bool
    github_activity_score: float = Field(ge=-1, le=100)
    search_appearance_30d: int = Field(ge=0)
    saved_by_recruiters_30d: int = Field(ge=0)
    interview_completion_rate: float = Field(ge=0, le=1)
    offer_acceptance_rate: float = Field(ge=-1, le=1)
    verified_email: bool
    verified_phone: bool
    linkedin_connected: bool

    @field_validator("preferred_work_mode")
    @classmethod
    def validate_work_mode(cls, v: str) -> str:
        allowed = {"remote", "hybrid", "onsite", "flexible"}
        if v not in allowed:
            raise ValueError(f"preferred_work_mode must be one of {allowed}, got '{v}'")
        return v


# ---------------------------------------------------------------------------
# Top-level Candidate model
# ---------------------------------------------------------------------------

class Candidate(BaseModel):
    """
    Full candidate profile.

    Required: candidate_id, profile, career_history, education, skills, redrob_signals.
    Optional: certifications, languages.
    """
    candidate_id: str = Field(pattern=r"^CAND_[0-9]{7}$")
    profile: Profile
    career_history: list[CareerEntry] = Field(min_length=1, max_length=10)
    education: list[Education] = Field(min_length=0, max_length=5)
    skills: list[Skill] = Field(default_factory=list)
    certifications: list[Certification] = Field(default_factory=list)
    languages: list[Language] = Field(default_factory=list)
    redrob_signals: RedrobSignals

    @classmethod
    def from_record(cls, record: dict) -> "Candidate":
        """Construct a Candidate from a raw dict (e.g. parsed from JSON)."""
        return cls.model_validate(record)


# ---------------------------------------------------------------------------
# Job Description models
# ---------------------------------------------------------------------------

class SkillRequirement(BaseModel):
    """
    A single skill requirement in a job description.

    - name: Human-readable label (e.g. "Python", "SQL")
    - keywords: List of terms to match against candidate text
                (titles, descriptions, skill names)
    """
    name: str
    keywords: list[str] = Field(default_factory=list)


class JobDescription(BaseModel):
    """
    A complete job description provided by the recruiter.

    This drives all matching logic dynamically — no YAML needed.
    Pass this inside every POST /rank request.
    """
    title: str = Field(description="Job title, e.g. 'Senior Backend Engineer'")
    description: str = Field(default="", description="Full free-text JD (optional, for reference)")

    # What candidates MUST have (scored heavily)
    required_skills: list[SkillRequirement] = Field(
        default_factory=list,
        description="Must-have skills/requirements. Each missed one lowers the score significantly.",
    )

    # What candidates SHOULD have (small bonus)
    nice_to_have_skills: list[SkillRequirement] = Field(
        default_factory=list,
        description="Nice-to-have skills. Each match adds a small bonus.",
    )

    # Experience range
    min_years_experience: float = Field(
        default=0.0, ge=0,
        description="Minimum acceptable years of experience (hard floor for disqualification).",
    )
    ideal_min_years: float = Field(
        default=3.0, ge=0,
        description="Start of ideal experience range (scores 1.0).",
    )
    ideal_max_years: float = Field(
        default=10.0, ge=0,
        description="End of ideal experience range (scores 1.0).",
    )
    max_years_experience: float = Field(
        default=20.0, ge=0,
        description="Soft upper limit — above this, score decays.",
    )

    # Logistics
    preferred_locations: list[str] = Field(
        default_factory=list,
        description="Ideal candidate locations (e.g. ['Bangalore', 'Pune'])",
    )
    acceptable_locations: list[str] = Field(
        default_factory=list,
        description="Acceptable but non-ideal locations.",
    )
    preferred_work_modes: list[str] = Field(
        default=["hybrid", "remote", "flexible", "onsite"],
        description="Accepted work modes.",
    )
    max_notice_days: int = Field(
        default=90, ge=0, le=180,
        description="Maximum acceptable notice period in days.",
    )
    ideal_notice_days: int = Field(
        default=30, ge=0, le=180,
        description="Ideal notice period threshold.",
    )


# ---------------------------------------------------------------------------
# API Request / Response models
# ---------------------------------------------------------------------------

class RankRequest(BaseModel):
    """Request body for the /rank endpoint."""
    job_description: JobDescription = Field(
        description="The job description to rank candidates against."
    )
    candidates: list[Candidate]
    top_k: int = Field(default=10, ge=1, le=100, description="Number of top candidates to return")


class RankedCandidate(BaseModel):
    """A single ranked candidate in the API response."""
    rank: int
    candidate_id: str
    score: float
    reasoning: str
    must_have_count: int
    is_disqualified: bool


class RankResponse(BaseModel):
    """Response body for the /rank endpoint."""
    total_processed: int
    total_eligible: int
    top_k: int
    results: list[RankedCandidate]
    parse_warnings: Optional[list[str]] = None  # Set when some candidates failed validation

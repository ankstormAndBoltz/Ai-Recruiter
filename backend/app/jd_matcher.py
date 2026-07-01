"""
jd_matcher.py — Dynamic rule-based matcher driven by a JobDescription object.

Accepts any JobDescription (passed inline by the recruiter) and evaluates
candidates against it. Works for any role — not hardcoded to ML Engineer.

Matching logic:
  - 2 generic disqualifiers (experience too low, zero skill match)
  - Keyword-based required_skills → must-have scores
  - Keyword-based nice_to_have_skills → bonus scores
  - Logistics from JD locations / notice period / work modes

Evidence text is extracted for each match and used by reasoning_templates.py.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from app.models import Candidate, JobDescription


# ---------------------------------------------------------------------------
# Match result data structures
# ---------------------------------------------------------------------------

@dataclass
class DisqualifierResult:
    """A single hard disqualifier evaluation."""
    id: str
    description: str
    fired: bool
    evidence: str = ""


@dataclass
class RequirementMatch:
    """A single must-have or nice-to-have match."""
    id: str
    description: str
    matched: bool
    evidence: str = ""  # Specific text from the candidate proving the match


@dataclass
class LogisticsResult:
    """Logistics fit assessment."""
    location_match: str = "none"  # "preferred", "acceptable", "india_other", "international", "none"
    notice_period_fit: str = "ideal"  # "ideal", "acceptable", "high"
    notice_days: int = 0
    country: str = ""
    location: str = ""
    work_mode_compatible: bool = True
    willing_to_relocate: bool = False


@dataclass
class MatchResult:
    """Complete structured match result for a candidate."""
    candidate_id: str
    disqualifiers: list[DisqualifierResult] = field(default_factory=list)
    must_haves: list[RequirementMatch] = field(default_factory=list)
    nice_to_haves: list[RequirementMatch] = field(default_factory=list)
    logistics: LogisticsResult = field(default_factory=LogisticsResult)

    @property
    def is_disqualified(self) -> bool:
        return any(d.fired for d in self.disqualifiers)

    @property
    def must_have_count(self) -> int:
        return sum(1 for m in self.must_haves if m.matched)

    @property
    def nice_to_have_count(self) -> int:
        return sum(1 for n in self.nice_to_haves if n.matched)

    @property
    def fired_disqualifiers(self) -> list[DisqualifierResult]:
        return [d for d in self.disqualifiers if d.fired]

    @property
    def matched_must_haves(self) -> list[RequirementMatch]:
        return [m for m in self.must_haves if m.matched]

    @property
    def matched_nice_to_haves(self) -> list[RequirementMatch]:
        return [n for n in self.nice_to_haves if n.matched]

    @property
    def unmatched_must_haves(self) -> list[RequirementMatch]:
        return [m for m in self.must_haves if not m.matched]


# ---------------------------------------------------------------------------
# JD Matcher — dynamic, accepts any JobDescription
# ---------------------------------------------------------------------------

class JDMatcher:
    """
    Dynamic rule-based matcher that evaluates candidates against any JobDescription.

    Usage:
        matcher = JDMatcher()
        result = matcher.match(candidate, job_description)
    """

    def match(self, candidate: Candidate, jd: JobDescription) -> MatchResult:
        """Evaluate a candidate against the given job description."""
        result = MatchResult(candidate_id=candidate.candidate_id)

        # Build searchable text blobs
        all_text = self._build_all_text(candidate)
        skill_names = {s.name.lower() for s in candidate.skills}

        # 1. Generic disqualifiers
        result.disqualifiers = self._check_disqualifiers(candidate, jd, all_text, skill_names)

        # 2. Must-have matches (from jd.required_skills)
        result.must_haves = self._check_skill_list(
            candidate, all_text, skill_names, jd.required_skills
        )

        # 3. Nice-to-have matches (from jd.nice_to_have_skills)
        result.nice_to_haves = self._check_skill_list(
            candidate, all_text, skill_names, jd.nice_to_have_skills
        )

        # 4. Logistics
        result.logistics = self._check_logistics(candidate, jd)

        return result

    # ------------------------------------------------------------------
    # Text builders
    # ------------------------------------------------------------------

    def _build_all_text(self, c: Candidate) -> str:
        """All searchable text from a candidate's profile — lowercased."""
        parts = [
            c.profile.headline,
            c.profile.summary,
            c.profile.current_title,
        ]
        for job in c.career_history:
            parts.append(f"{job.title} at {job.company}: {job.description}")
        for s in c.skills:
            parts.append(s.name)
        return " ".join(parts).lower()

    # ------------------------------------------------------------------
    # Generic disqualifiers (work for any role)
    # ------------------------------------------------------------------

    def _check_disqualifiers(
        self,
        c: Candidate,
        jd: JobDescription,
        all_text: str,
        skill_names: set[str],
    ) -> list[DisqualifierResult]:
        """
        Two generic disqualifiers that apply to any job description:

        1. experience_too_low — candidate's YoE is below the JD's hard minimum.
        2. zero_skill_match  — candidate matches NONE of the required skills at all.
                               (Only fires if the JD has required skills defined.)
        """
        results = []

        # --- Disqualifier 1: Experience below hard minimum ---
        yoe = c.profile.years_of_experience
        if jd.min_years_experience > 0 and yoe < jd.min_years_experience:
            results.append(DisqualifierResult(
                id="experience_too_low",
                description=f"Candidate has fewer than {jd.min_years_experience} years of experience",
                fired=True,
                evidence=(
                    f"Candidate has {yoe} years; JD requires at least "
                    f"{jd.min_years_experience} years"
                ),
            ))
        else:
            results.append(DisqualifierResult(
                id="experience_too_low",
                description=f"Experience below {jd.min_years_experience} year minimum",
                fired=False,
            ))

        # --- Disqualifier 2: Zero skill match ---
        # Only fires if the JD specifies required skills AND candidate matches none of them
        if jd.required_skills:
            any_match = False
            for req in jd.required_skills:
                req_keywords = {kw.lower() for kw in req.keywords}
                req_skill_names = {req.name.lower()}
                if req_keywords & set(all_text.split()) or req_skill_names & skill_names:
                    any_match = True
                    break
                # Also check substring match in all_text
                if any(kw in all_text for kw in req_keywords):
                    any_match = True
                    break

            results.append(DisqualifierResult(
                id="zero_skill_match",
                description="Candidate matches none of the required skills",
                fired=not any_match,
                evidence=(
                    "No required skills found in candidate profile, titles, or descriptions"
                    if not any_match else ""
                ),
            ))
        else:
            results.append(DisqualifierResult(
                id="zero_skill_match",
                description="No required skills defined in JD",
                fired=False,
            ))

        return results

    # ------------------------------------------------------------------
    # Skill requirement matching (must-haves + nice-to-haves)
    # ------------------------------------------------------------------

    def _check_skill_list(
        self,
        c: Candidate,
        all_text: str,
        skill_names: set[str],
        requirements: list,
    ) -> list[RequirementMatch]:
        """Match a list of SkillRequirements against the candidate."""
        results = []
        for req in requirements:
            matched, evidence = self._match_single_skill(
                c, all_text, skill_names, req
            )
            results.append(RequirementMatch(
                id=req.name.lower().replace(" ", "_"),
                description=req.name,
                matched=matched,
                evidence=evidence,
            ))
        return results

    def _match_single_skill(
        self,
        c: Candidate,
        all_text: str,
        skill_names: set[str],
        req,
    ) -> tuple[bool, str]:
        """
        Check if candidate matches a single SkillRequirement.

        Match strategy (in priority order):
        1. Direct skill name match (candidate's skills list)
        2. Keyword substring match in all_text (career text, headline, summary)
        """
        evidence_parts = []

        # 1. Direct skill name match
        req_name_lower = req.name.lower()
        if req_name_lower in skill_names:
            # Find the full skill entry for proficiency info
            for s in c.skills:
                if s.name.lower() == req_name_lower:
                    evidence_parts.append(f"{s.name} ({s.proficiency}, {s.duration_months}mo)")
                    break

        # 2. Keyword match in all candidate text
        if not evidence_parts:
            req_keywords = [kw.lower() for kw in req.keywords]
            matched_kws = [kw for kw in req_keywords if kw in all_text]
            if matched_kws:
                # Check if any matched keyword also appears in their skill list
                skill_match = next(
                    (s for s in c.skills if s.name.lower() in matched_kws),
                    None
                )
                if skill_match:
                    evidence_parts.append(
                        f"{skill_match.name} ({skill_match.proficiency}, {skill_match.duration_months}mo)"
                    )
                else:
                    evidence_parts.append(f"Keywords found: {', '.join(matched_kws[:3])}")

        matched = len(evidence_parts) > 0
        evidence = "; ".join(evidence_parts) if evidence_parts else ""
        return matched, evidence

    # ------------------------------------------------------------------
    # Logistics check
    # ------------------------------------------------------------------

    def _check_logistics(self, c: Candidate, jd: JobDescription) -> LogisticsResult:
        """Evaluate logistics fit against the JD's location and notice constraints."""
        location = c.profile.location.lower()
        country = c.profile.country.lower()

        preferred = [loc.lower() for loc in jd.preferred_locations]
        acceptable = [loc.lower() for loc in jd.acceptable_locations]

        # Location matching
        if preferred and any(loc in location for loc in preferred):
            location_match = "preferred"
        elif acceptable and any(loc in location for loc in acceptable):
            location_match = "acceptable"
        elif country == "india":
            location_match = "india_other"
        else:
            location_match = "international"

        # Notice period — use JD thresholds
        notice = c.redrob_signals.notice_period_days
        if notice <= jd.ideal_notice_days:
            notice_fit = "ideal"
        elif notice <= jd.max_notice_days:
            notice_fit = "acceptable"
        else:
            notice_fit = "high"

        # Work mode
        preferred_modes = set(m.lower() for m in jd.preferred_work_modes)
        work_mode_ok = c.redrob_signals.preferred_work_mode.lower() in preferred_modes

        return LogisticsResult(
            location_match=location_match,
            notice_period_fit=notice_fit,
            notice_days=notice,
            country=c.profile.country,
            location=c.profile.location,
            work_mode_compatible=work_mode_ok,
            willing_to_relocate=c.redrob_signals.willing_to_relocate,
        )

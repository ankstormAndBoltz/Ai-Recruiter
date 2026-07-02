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

import math
import statistics
from dataclasses import dataclass, field

from app.models import Candidate, JobDescription


# ---------------------------------------------------------------------------
# Depth-weighting constants (build plan: proficiency × duration × assessment)
# ---------------------------------------------------------------------------

_PROFICIENCY_WEIGHT = {
    "beginner": 0.3,
    "intermediate": 0.6,
    "advanced": 0.85,
    "expert": 1.0,
}

# Companies treated as consulting/services (career-fit disqualifiers).
CONSULTING_FIRMS = {
    "tcs", "infosys", "wipro", "accenture", "cognizant", "capgemini",
    "hcl", "tech mahindra", "ltimindtree", "mphasis", "deloitte",
    "kpmg", "ey", "pwc",
}

# Keywords indicating production/shipping work (vs pure research).
_PRODUCTION_KEYWORDS = (
    "production", "deployed", "shipped", "launched", "scale", "api",
    "pipeline", "a/b test", "serving", "real users", "latency",
)
_RESEARCH_KEYWORDS = (
    "research", "phd", "publication", "paper", "academic", "postdoc",
    "thesis", "lab",
)
# Recent-LLM-wrapper skills; presence-only, no pre-LLM ML depth is a red flag.
_LANGCHAIN_KEYWORDS = ("langchain", "llamaindex", "openai api", "prompt")


def _duration_weight(months: int) -> float:
    """Log-scale duration weight: ~0.1 @ 1mo, ~0.7 @ 12mo, 1.0 @ 60mo+."""
    if months <= 0:
        return 0.0
    # log10-based ramp, calibrated so 60+ months saturates at 1.0.
    return max(0.0, min(1.0, math.log10(1 + months) / math.log10(61)))


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
    """A single must-have or nice-to-have match.

    `strength` is the depth-weighted score in [0, 1] (proficiency × duration ×
    assessment boost). This replaces binary presence/absence matching: an
    "Embeddings" skill at expert / 60mo / assessed 89 scores ~0.95, while a
    beginner keyword hit scores ~0.15.
    """
    id: str
    description: str
    matched: bool
    evidence: str = ""  # Specific text from the candidate proving the match
    strength: float = 0.0  # Depth-weighted match score in [0, 1]


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
    # Stage-3 career-fit score in [0, 1]; 0.0 when a hard disqualifier fires.
    career_fit: float = 0.0

    @property
    def is_disqualified(self) -> bool:
        return any(d.fired for d in self.disqualifiers)

    @property
    def must_have_count(self) -> int:
        return sum(1 for m in self.must_haves if m.matched)

    @property
    def must_have_coverage(self) -> float:
        """Mean depth-weighted must-have strength in [0, 1].

        This is the value the scorer uses — it rewards depth (proficiency +
        duration + assessment), not mere presence.
        """
        if not self.must_haves:
            return 0.0
        return sum(m.strength for m in self.must_haves) / len(self.must_haves)

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

    def match(
        self,
        candidate: Candidate,
        jd: JobDescription,
        assessment_thresholds: dict[str, float] | None = None,
    ) -> MatchResult:
        """Evaluate a candidate against the given job description.

        Args:
            candidate: the candidate to score.
            jd: the job description driving all requirements.
            assessment_thresholds: optional {skill_name_lower: 80th-percentile
                assessment score across the dataset}. When a candidate's skill
                assessment beats its threshold, the must-have match gets a
                +10% boost. Computed once by the ranking pipeline.
        """
        result = MatchResult(candidate_id=candidate.candidate_id)
        thresholds = assessment_thresholds or {}

        # Build searchable text blobs
        all_text = self._build_all_text(candidate)
        skill_names = {s.name.lower() for s in candidate.skills}

        # 1. Disqualifiers — generic (experience floor, zero-skill) + JD-specific
        #    career-fit rules (research-only, langchain-only, no-recent-prod,
        #    consulting-only, title-chaser).
        result.disqualifiers = self._check_disqualifiers(candidate, jd, all_text, skill_names)

        # 2. Must-have matches — depth-weighted (proficiency × duration × assessment)
        result.must_haves = self._check_skill_list(
            candidate, all_text, skill_names, jd.required_skills, thresholds
        )

        # 3. Nice-to-have matches (from jd.nice_to_have_skills)
        result.nice_to_haves = self._check_skill_list(
            candidate, all_text, skill_names, jd.nice_to_have_skills, thresholds
        )

        # 4. Logistics
        result.logistics = self._check_logistics(candidate, jd)

        # 5. Career fit — 0.0 if a hard disqualifier fired, else product-yrs + yoe.
        result.career_fit = self._eval_career_fit(candidate, jd, result)

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
        Two generic HARD disqualifiers that apply to any job description:

        1. experience_too_low — candidate's YoE is below the JD's hard minimum.
        2. zero_skill_match  — candidate matches NONE of the required skills at all.
                               (Only fires if the JD has required skills defined.)

        JD-specific concerns (research-only, langchain-only, no-recent-prod,
        consulting-only, title-chaser) are handled as GRADED career-fit
        penalties in `_eval_career_fit`, not hard exclusions — per the build
        plan, these down-weight rather than disqualify outright.
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
        thresholds: dict[str, float],
    ) -> list[RequirementMatch]:
        """Match a list of SkillRequirements against the candidate, computing a
        depth-weighted strength for each (proficiency × duration × assessment)."""
        results = []
        for req in requirements:
            matched, evidence, strength = self._match_single_skill(
                c, all_text, skill_names, req, thresholds
            )
            results.append(RequirementMatch(
                id=req.name.lower().replace(" ", "_"),
                description=req.name,
                matched=matched,
                evidence=evidence,
                strength=strength,
            ))
        return results

    def _match_single_skill(
        self,
        c: Candidate,
        all_text: str,
        skill_names: set[str],
        req,
        thresholds: dict[str, float],
    ) -> tuple[bool, str, float]:
        """
        Check if a candidate matches a single SkillRequirement and how DEEPLY.

        Strategy (build plan Component 2 — replaces binary substring matching):
        1. Find the best backing skill entry — either the requirement name or
           any of its keywords appearing in the candidate's skills list.
        2. If a real skill entry backs the match, strength =
              proficiency_weight × duration_weight × assessment_boost
           where assessment_boost is 1.1 when the skill's assessment score
           beats the dataset's 80th-percentile threshold for that skill.
        3. If only free-text keywords match (no skill entry), it's a shallow
           mention → strength 0.15 (a "beginner"-equivalent floor), so keyword
           stuffing in bullet points cannot masquerade as real depth.

        Returns (matched, evidence, strength in [0, 1]).
        """
        req_name_lower = req.name.lower()
        req_keywords = [kw.lower() for kw in req.keywords]

        # 1. Find the best backing skill entry (name match or keyword match).
        backing_skill = None
        for s in c.skills:
            sn = s.name.lower()
            if sn == req_name_lower or sn in req_keywords:
                if backing_skill is None or _PROFICIENCY_WEIGHT.get(
                    s.proficiency, 0.0
                ) > _PROFICIENCY_WEIGHT.get(backing_skill.proficiency, 0.0):
                    backing_skill = s

        if backing_skill is not None:
            prof_w = _PROFICIENCY_WEIGHT.get(backing_skill.proficiency, 0.3)
            dur_w = _duration_weight(backing_skill.duration_months)
            # Duration alone should not zero out a real, endorsed skill.
            dur_w = max(dur_w, 0.2)

            assess = c.redrob_signals.skill_assessment_scores.get(backing_skill.name)
            threshold = thresholds.get(backing_skill.name.lower())
            assess_boost = 1.0
            assess_note = ""
            if assess is not None:
                assess_note = f", assessed {assess:.0f}/100"
                if threshold is not None and assess >= threshold:
                    assess_boost = 1.1

            strength = min(1.0, prof_w * dur_w * assess_boost)
            evidence = (
                f"{backing_skill.name} ({backing_skill.proficiency}, "
                f"{backing_skill.duration_months}mo{assess_note})"
            )
            return True, evidence, round(strength, 4)

        # 2. Only free-text keyword mentions — shallow signal, capped low.
        matched_kws = [kw for kw in req_keywords if kw in all_text]
        if matched_kws:
            evidence = f"Mentioned (no rated skill): {', '.join(matched_kws[:3])}"
            return True, evidence, 0.15

        return False, "", 0.0

    # ------------------------------------------------------------------
    # Career fit — JD-specific graded penalties (build plan Component 3)
    # ------------------------------------------------------------------

    def _eval_career_fit(
        self, c: Candidate, jd: JobDescription, result: MatchResult
    ) -> float:
        """
        Grade how well the candidate's *career shape* fits the role, in [0, 1].

        Hard-ish disqualifier bypasses (return immediately):
          - Pure research, no production deployment ever → 0.0
          - Recent LLM-wrapper-only (<12mo) w/ no pre-LLM ML depth → 0.1
          - Senior but no production code in ~18mo → 0.15
          - All-consulting, no product company → 0.25
          - Title-chaser (never a 3+ year tenure, 3+ jobs) → 0.35

        Otherwise combine product-company years and years-of-experience fit.
        """
        history = c.career_history
        yoe = c.profile.years_of_experience
        joined_text = " ".join(
            f"{j.title} {j.description}".lower() for j in history
        )

        has_production = any(k in joined_text for k in _PRODUCTION_KEYWORDS)
        has_research = any(k in joined_text for k in _RESEARCH_KEYWORDS)

        # Pure research, never shipped.
        if has_research and not has_production:
            return 0.0

        # Recent LLM-wrapper-only: every AI-ish skill is short-tenured and there's
        # no production evidence predating the LLM era.
        langchain_only = (
            any(k in joined_text for k in _LANGCHAIN_KEYWORDS)
            and all(s.duration_months < 12 for s in c.skills if s.duration_months)
            and not has_production
        )
        if langchain_only:
            return 0.1

        # Senior (meets ideal experience) but no recent production code: current
        # role is management/architecture-only for 18+ months.
        current = next((j for j in history if j.is_current), None)
        if current is not None and yoe >= jd.ideal_min_years:
            cur_text = f"{current.title} {current.description}".lower()
            mgmt_only = any(
                t in cur_text for t in ("manager", "director", "vp", "head of", "architect")
            ) and not any(k in cur_text for k in _PRODUCTION_KEYWORDS + ("code", "built", "developed"))
            if mgmt_only and current.duration_months >= 18:
                return 0.15

        # Product-company years (non-consulting).
        product_jobs = [
            j for j in history
            if not any(firm in j.company.lower() for firm in CONSULTING_FIRMS)
        ]
        if history and not product_jobs:
            return 0.25  # entirely consulting

        # Title-chaser: 3+ jobs and never held a role 3+ years.
        if len(history) >= 3 and all(j.duration_months < 36 for j in history):
            return 0.35

        # --- Graded fit for everyone else ---
        product_years = sum(j.duration_months for j in product_jobs) / 12.0
        if product_years > 4:
            product_score = 1.0
        elif product_years >= 2:
            product_score = 0.7
        else:
            product_score = 0.4

        lo, hi = jd.ideal_min_years, jd.ideal_max_years
        if lo <= yoe <= hi:
            yoe_score = 1.0
        elif yoe < lo:
            yoe_score = 0.5
        elif yoe <= jd.max_years_experience:
            yoe_score = 0.7
        else:
            yoe_score = 0.4

        return round(0.6 * product_score + 0.4 * yoe_score, 4)

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

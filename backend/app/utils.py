"""
utils.py — General utility helpers for the Candidate Ranking System API.

Also hosts the semantic-embedding layer (Stage 1/3 of the build plan).
Previously the semantic score was hardcoded to 0.5 — a dead layer worth 30%
of every candidate's score. This now computes a REAL cosine similarity
between the candidate narrative and the JD's ideal-profile narrative using
sentence-transformers (all-MiniLM-L6-v2), cached once.

If sentence-transformers/torch are not installed, we fall back to a
deterministic token-overlap similarity so the pipeline still runs offline
and reproducibly (never silently reverting to the old constant 0.5).
"""

from __future__ import annotations

import math
import re
from functools import lru_cache

from app.models import Candidate


# ---------------------------------------------------------------------------
# Embedding model — lazy singleton (loaded once, cached in-process)
# ---------------------------------------------------------------------------

_MODEL_NAME = "all-MiniLM-L6-v2"
_TOKEN_RE = re.compile(r"[a-z0-9+#.]+")


@lru_cache(maxsize=1)
def _get_model():
    """
    Load and cache the sentence-transformers model once.

    Returns the model, or None if sentence-transformers/torch are unavailable
    (in which case callers fall back to token-overlap similarity).
    """
    try:
        from sentence_transformers import SentenceTransformer

        return SentenceTransformer(_MODEL_NAME)
    except Exception:
        return None


def embed_text(text: str):
    """
    Embed a single text into a vector.

    Returns a normalized embedding (numpy array) when the model is available,
    otherwise None so the caller uses the token-overlap fallback.
    """
    model = _get_model()
    if model is None:
        return None
    return model.encode(text, normalize_embeddings=True, show_progress_bar=False)


def _token_overlap_similarity(a: str, b: str) -> float:
    """
    Deterministic offline fallback: weighted Jaccard / cosine over token sets.

    Not as good as embeddings, but grounded in shared vocabulary and never a
    constant — so a strong ML narrative still out-scores an off-topic one.
    """
    ta = set(_TOKEN_RE.findall(a.lower()))
    tb = set(_TOKEN_RE.findall(b.lower()))
    if not ta or not tb:
        return 0.0
    inter = len(ta & tb)
    # Cosine over binary term vectors.
    return inter / math.sqrt(len(ta) * len(tb))


def semantic_similarity(candidate_narrative: str, jd_narrative: str) -> float:
    """
    Cosine similarity in [0, 1] between the candidate narrative and the JD's
    ideal-profile narrative.

    This is the KEY difference from keyword matching: "ML Engineer who shipped
    a ranking system" matches "needs someone with retrieval experience"
    semantically, even when the exact words differ.
    """
    if not candidate_narrative or not jd_narrative:
        return 0.0

    vec_c = embed_text(candidate_narrative)
    vec_j = embed_text(jd_narrative)

    if vec_c is not None and vec_j is not None:
        # Vectors are already normalized → dot product is cosine similarity.
        import numpy as np

        sim = float(np.dot(vec_c, vec_j))
        # Map cosine [-1, 1] → [0, 1], clipped.
        return max(0.0, min(1.0, (sim + 1.0) / 2.0))

    # Offline fallback.
    return _token_overlap_similarity(candidate_narrative, jd_narrative)


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

"""
main.py — FastAPI server for the AI-powered Candidate Ranking System.

Endpoints:
    GET  /health      — Server liveness check
    POST /rank        — Rank candidates against an inline job description
    POST /validate    — Validate a candidate payload without scoring it
    GET  /jd/example  — Get an example JobDescription payload to use as a template

The JD is now passed inline with every /rank request — no YAML config
or server restart needed to change roles.
"""

from __future__ import annotations

import json

from fastapi import FastAPI, File, Form, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import RedirectResponse

from app.models import Candidate, JobDescription, RankRequest, RankResponse
from app.ranking import rank_candidates


# ---------------------------------------------------------------------------
# FastAPI app
# ---------------------------------------------------------------------------

app = FastAPI(
    title="Candidate Ranking System API",
    description=(
        "AI-powered backend for ranking job candidates against **any** job description. "
        "Pass your JD inline with each request — works for any role, fully offline.\n\n"
        "**How to use**: Send a `POST /rank` with your `job_description` + `candidates` list."
    ),
    version="2.0.0",
)

# Allow requests from the frontend (file://, localhost, any port)
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/", include_in_schema=False)
def root():
    """Redirect root URL to the Swagger docs."""
    return RedirectResponse(url="/docs")


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------

@app.get("/health", tags=["System"])
def health_check():
    """
    Server liveness check.
    Returns OK status — no external dependencies to check.
    """
    return {
        "status": "ok",
        "message": "API is running successfully",
        "version": "2.0.0",
        "offline": True,
    }


@app.post("/rank", response_model=RankResponse, tags=["Ranking"])
def rank(request: RankRequest):
    """
    Rank a list of candidates against the provided job description.

    The `job_description` field drives all matching logic:
    - `required_skills` → must-have scoring (biggest weight)
    - `nice_to_have_skills` → bonus scoring
    - `min_years_experience` → hard disqualifier below this floor
    - `preferred_locations` + `acceptable_locations` → logistics multiplier
    - `preferred_work_modes` + `max_notice_days` → logistics multiplier

    Pipeline per candidate:
    1. Honeypot consistency check → hard-exclude fabricated profiles
    2. JD matching → must-haves, nice-to-haves, disqualifiers, logistics
    3. Behavioral signal scoring → availability multiplier [0.3-1.3]
    4. Final score computation (6 components × 2 multipliers)
    5. Deterministic sort → reasoning generation

    Returns the top `top_k` candidates with scores and reasoning.
    """
    if not request.candidates:
        raise HTTPException(status_code=400, detail="Candidates list cannot be empty.")

    result = rank_candidates(
        candidates=request.candidates,
        job_description=request.job_description,
        top_k=request.top_k,
    )
    return result


@app.post("/rank/file", response_model=RankResponse, tags=["Ranking"])
async def rank_from_file(
    file: UploadFile = File(...),
    job_description: str = Form(...),
    top_k: int = Form(default=10),
):
    """
    Rank candidates from an uploaded file instead of a JSON body.

    Use this for large datasets (1,000+ candidates) — it streams the file
    directly to the backend, avoiding the overhead of serialising a giant
    JSON body in the browser.

    Form fields:
    - `file`            — .json (array) or .jsonl (one object per line)
    - `job_description` — JSON string of the JobDescription object
    - `top_k`           — number of top candidates to return (default 10)
    """
    # --- Parse job description ---
    try:
        jd_dict = json.loads(job_description)
        jd = JobDescription(**jd_dict)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid job_description: {e}")

    # --- Read and parse uploaded file ---
    filename = file.filename or "upload"
    content  = await file.read()

    try:
        text = content.decode("utf-8")
    except UnicodeDecodeError:
        raise HTTPException(status_code=400, detail="File must be UTF-8 encoded text.")

    try:
        if filename.endswith(".jsonl"):
            # JSON Lines: one candidate object per line
            raw_candidates = [
                json.loads(line)
                for line in text.splitlines()
                if line.strip()
            ]
        else:
            # Standard JSON array
            raw_candidates = json.loads(text)
            if not isinstance(raw_candidates, list):
                raise ValueError("File must contain a JSON array [ ... ]")
    except (json.JSONDecodeError, ValueError) as e:
        raise HTTPException(status_code=422, detail=f"File parse error: {e}")

    if not raw_candidates:
        raise HTTPException(status_code=400, detail="File contains no candidates.")

    # --- Validate candidates ---
    validated: list[Candidate] = []
    errors: list[str] = []
    for i, raw in enumerate(raw_candidates):
        try:
            validated.append(Candidate.from_record(raw))
        except Exception as e:
            errors.append(f"Candidate #{i+1}: {e}")

    if not validated:
        raise HTTPException(
            status_code=422,
            detail=f"No valid candidates found. First error: {errors[0] if errors else 'unknown'}"
        )

    # --- Rank ---
    result = rank_candidates(
        candidates=validated,
        job_description=jd,
        top_k=min(top_k, 100),
    )

    # Attach parse warning if some candidates were skipped
    if errors:
        result.parse_warnings = errors[:10]  # first 10 errors

    return result


@app.post("/validate", tags=["Utilities"])
def validate_candidate(candidate: Candidate):
    """
    Validate a single candidate payload without scoring it.

    Useful for testing whether your candidate JSON structure is correct
    before sending a batch to /rank.

    Returns:
    - `valid`: Always true if parsing succeeded (Pydantic would raise 422 otherwise)
    - `is_honeypot`: Whether any consistency violations were found
    - `violations`: List of specific violation descriptions
    - `summary`: A short human-readable summary of the candidate
    """
    from app.utils import get_candidate_summary
    from app.honeypot_checks import consistency_violations

    violations = consistency_violations(candidate)

    return {
        "valid": True,
        "is_honeypot": len(violations) > 0,
        "violations": violations,
        "summary": get_candidate_summary(candidate),
    }


@app.get("/jd/example", tags=["Utilities"])
def get_example_jd():
    """
    Returns a fully-filled example JobDescription payload.

    Use this as a template when building your own /rank requests.
    Copy this, modify the fields for your role, then pass it in POST /rank.
    """
    return JobDescription(
        title="Senior ML Engineer — Search & Ranking",
        description="Looking for a senior ML engineer to build production search and ranking systems.",
        required_skills=[
            {"name": "Python", "keywords": ["python", "fastapi", "flask", "django", "asyncio"]},
            {"name": "Embeddings & Retrieval", "keywords": ["embeddings", "faiss", "vector search", "semantic search", "sentence-transformers"]},
            {"name": "Production ML", "keywords": ["production", "deployed", "pipeline", "api", "scale", "mlflow"]},
            {"name": "Ranking Systems", "keywords": ["ranking", "ndcg", "mrr", "a/b test", "recommendation", "search"]},
        ],
        nice_to_have_skills=[
            {"name": "LLM Fine-tuning", "keywords": ["lora", "qlora", "peft", "fine-tuning", "rlhf"]},
            {"name": "Distributed Systems", "keywords": ["kubernetes", "ray", "spark", "distributed"]},
        ],
        min_years_experience=3.0,
        ideal_min_years=5.0,
        ideal_max_years=9.0,
        max_years_experience=15.0,
        preferred_locations=["Noida", "Pune"],
        acceptable_locations=["Bangalore", "Bengaluru", "Hyderabad", "Mumbai", "Delhi"],
        preferred_work_modes=["hybrid", "flexible", "onsite"],
        max_notice_days=90,
        ideal_notice_days=30,
    )

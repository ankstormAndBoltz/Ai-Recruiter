# AI Recruiter — Candidate Ranking System

An AI-powered candidate ranking system that semantically matches job candidates against a job description using **real embeddings, depth-weighted skill matching, and behavioral signals**. Fully offline, rule-based, no LLM reranking needed. The project features a **FastAPI Backend**, a **professional dark-mode Frontend**, and an **Electron Desktop App** packaged as a one-click `.exe`.

## Architecture

The system implements a **4-stage hybrid ranking pipeline** (see `BUILD_PLAN.md` for full details):
1. **Stage 1: JD Understanding** — Extract ideal-profile narrative from job description.
2. **Stage 2: Candidate Profiling** — Build rich candidate cards with assessed skills and behavioral signals (flag honeypots for down-weighting, not hard-exclusion).
3. **Stage 3: Hybrid Scoring** — Combine semantic similarity (via sentence-transformers embeddings), depth-weighted must-haves (proficiency × duration × assessment), JD-specific career fit, and behavioral engagement multiplier `[0.65, 1.15]`.
4. **Stage 4: Output** — Deterministic tie-breaking, evidence-cited reasoning, CSV export.

### Implementation

1. **`backend/` (FastAPI + Python)**
   - **Semantic layer**: Real cosine similarity (sentence-transformers `all-MiniLM-L6-v2`) between candidate narrative and JD ideal profile, with deterministic offline fallback.
   - **Depth-weighted matching**: Must-haves scored by proficiency, duration, and assessment percentile boost, not binary substring matching.
   - **JD-specific career fit**: Disqualifier bypasses (research-only→0.0, langchain-only→0.1, no-recent-prod→0.15, consulting-only→0.25, title-chaser→0.35); product-company years + experience fit grading.
   - **Behavioral multiplier**: Narrowed `[0.65, 1.15]` range so passive strong-fit seniors aren't cratered by engagement flags.
   - **Honeypot detection**: 8 consistency checks flag integrity concerns (fabricated dates, impossible overlaps, etc.); flagged candidates are down-weighted (×0.5), not silently excluded.
   - **Reasoning**: Evidence-cited explanations citing proficiency, assessment scores, years, and integrity flags.
   - **Large File Support**: Multipart streaming via `/rank/file` for huge `.json`/`.jsonl` datasets.
   - **Dependency**: Requires `sentence-transformers` and `torch` for embeddings; gracefully falls back to token-overlap if unavailable.

2. **`frontend/` (Vanilla HTML/CSS/JS)**
   - Dark mode glassmorphism UI with animated results.
   - Interactive Job Description builder with skill rows, work mode chips, and location filters.
   - Drag-and-drop file upload for candidates (`.json` or `.jsonl`).
   - Real-time API status (green/red connection dot in nav).
   - Live score bars, medal badges (🥇🥈🥉) for top 3, per-candidate reasoning boxes.
   - XLSX export of ranked results.
   - Fully offline (no CDN or external scripts except Google Fonts).

3. **`electron-app/` (Node.js Desktop Wrapper)**
   - **One-click launch**: Bundled Python venv + backend + frontend; no user setup needed.
   - **Auto-start backend**: Detects if running packaged or dev; spawns uvicorn from bundled venv on first startup.
   - **Splash screen**: Branded "AI Recruiter" splash with gradient logo while backend warms up (~3-5s first startup, ~1-2s warm).
   - **Window management**: Cleans up backend process on app close.
   - **Icons & branding**: Branded `.ico` file with purple→cyan gradient badge matching the UI.
   - **Two distribution formats**:
     - **Portable** (`AI-Recruiter-Portable.exe`, ~99MB): Self-contained, double-click-to-run, no install or system dependencies needed.
     - **Installer** (`AI Recruiter Setup 2.0.0.exe`, ~116MB): One-click installer, creates Desktop + Start Menu shortcuts, no dialogs.

---

## How to Run

### Quickest: One-Click Desktop App
**For Windows**: Download the latest `.exe` from `dist/` and double-click it. That's it — backend starts automatically, frontend opens in seconds.

```bash
AI-Recruiter-Portable.exe
# or
AI Recruiter Setup 2.0.0.exe (and follow the one-click installer)
```

### Development: From Source

#### Prerequisites
- **Node.js** (v20+) and npm
- **Python** (3.10+)

#### Setup
1. **Clone and enter the project**:
   ```bash
   cd App
   ```

2. **Install backend dependencies**:
   ```bash
   cd backend
   pip install -r requirements.txt
   cd ..
   ```

3. **Install Electron dependencies**:
   ```bash
   cd electron-app
   npm install
   cd ..
   ```

#### Run in Dev Mode
```bash
cd electron-app
npm start
```
The backend will auto-start and the frontend window will open. Logs appear in the console.

#### Build One-Click Installers (Windows)
```bash
cd electron-app
npm run dist:portable      # Creates AI-Recruiter-Portable.exe (~99MB)
npm run dist                # Creates both portable + NSIS installer
```
Outputs are in `dist/`. No code signing is required (executables are unsigned but work).

#### Manual Backend + Browser (dev/testing)
If you just want to run the backend API and view the frontend in your browser:

```bash
cd backend
python -m uvicorn app.main:app --reload --port 8000
```
Then open `frontend/index.html` in your browser or visit `http://localhost:8000/docs` for the interactive Swagger docs.

---

## Using the Application

1. **Job Description Tab**: 
   - Set the job title and an optional "ideal profile narrative" (free text describing what you're looking for).
   - Add **required skills** (e.g., "Python", "Embeddings", "Ranking") — each with keywords to match.
   - Add **nice-to-have skills** (bonus points).
   - Set experience range (hard floor, ideal range, soft ceiling).
   - Choose locations and work modes.

2. **Candidates Tab**:
   - **Drag & Drop**: Drop a `.json` or `.jsonl` file with candidate data.
   - **Load Samples**: Click "Load 3 Samples" to test with built-in mock candidates.
   - **Paste JSON**: Click "Paste JSON" to manually edit raw candidate JSON.

3. **Rank Candidates**: 
   - Click "🚀 Rank Candidates".
   - Results show:
     - **Score** (0-100%): Hybrid ranking score.
     - **Badges**: Must-have matches (✓ green / ✗ red strikethrough).
     - **Reasoning**: 1-2 sentence explanation citing skills, proficiency, years, and any integrity flags.
     - **⚠️ flags**: Honeypot concerns (fabricated dates, etc.) and low engagement warnings.

4. **Export**: Click "📥 Download XLSX" to export results to a spreadsheet.

---

## API Endpoints

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server liveness (used by Electron for startup polling). |
| `POST` | `/rank` | Rank candidates from JSON body (small datasets). |
| `POST` | `/rank/file` | Rank from uploaded `.json`/`.jsonl` file (multipart; large datasets). |
| `POST` | `/validate` | Validate candidate payload without scoring. |
| `GET` | `/jd/example` | Example JobDescription payload (template). |

Visit `http://localhost:8000/docs` to view interactive Swagger API documentation.

---

## Scoring Formula

```
base_score = 0.25 * semantic_similarity
           + 0.40 * must_have_coverage          # depth-weighted: prof × duration × assessment
           + 0.20 * career_fit                  # JD-specific disqualifier bypasses
           + 0.15 * behavioral_engagement      # narrowed [0.65, 1.15]

final_score = base_score * logistics_multiplier
            * 0.5 (if honeypot flagged)        # down-weight, don't exclude
```

---

## Development Notes

- **Reproducibility**: Deterministic ranking via tie-break chain (score, must-have count, candidate ID).
- **No External Services**: Fully offline; uses pre-downloaded embeddings model (or falls back to token overlap).
- **Honeypot Flags**: Integrity concerns are surfaced (not silently deleted), letting reviewers audit flagged candidates.
- **Frontend**: Works with or without backend via local `/docs` browser fallback.

# AI Recruiter — Submission Guide & Demo

## Quick Start

### Run the One-Click Desktop App (Recommended)
```bash
AI-Recruiter-Portable.exe
# or
AI Recruiter Setup 2.0.0.exe
```

Both executables are in `App/electron-app/dist/`.
- **Portable**: Double-click, run anywhere (99MB)
- **Installer**: One-click setup with Start Menu shortcut (116MB)

Backend auto-starts. Frontend opens in ~3-5 seconds on first run.

---

## What's Inside

### Backend (`App/backend/`)
**FastAPI ranking engine** with 4-stage semantic candidate matching:

1. **Stage 1: JD Understanding** — Extract ideal-profile narrative from job description
2. **Stage 2: Candidate Profiling** — Build skill depth matrix, detect integrity red flags (honeypots)
3. **Stage 3: Hybrid Scoring** — Combine:
   - **Real semantic embeddings** (sentence-transformers)
   - **Depth-weighted must-haves** (proficiency × duration × assessment boost)
   - **JD-specific career fit** (5 disqualifier bypasses)
   - **Behavioral multiplier** (narrowed [0.65, 1.15])
4. **Stage 4: Output** — Deterministic ranking with evidence-cited reasoning

**Key Features:**
- ✅ Real cosine similarity (sentence-transformers all-MiniLM-L6-v2)
- ✅ Depth-weighted skills (not binary substring matching)
- ✅ Honeypot detection (8 consistency checks; flagged + down-weighted, not excluded)
- ✅ JD-specific scoring (research→0.0, langchain-only→0.1, consulting→0.25, etc.)
- ✅ Behavioral realism (passive seniors with strong fit aren't eliminated)
- ✅ Large file support (multipart streaming for huge datasets)
- ✅ Fully offline (graceful fallback if model unavailable)

### Frontend (`App/frontend/`)
**Professional dark-mode UI** with:
- Interactive Job Description builder
- Drag-drop candidate upload (.json / .jsonl)
- Live results with score bars, medal badges (🥇🥈🥉)
- Per-candidate reasoning boxes
- XLSX export
- API status indicator
- Glassmorphism styling

### Desktop App (`App/electron-app/`)
**Electron wrapper** that:
- Bundles Python venv + backend + frontend
- Auto-starts backend on first launch
- Branded UI with splash screen and custom icon
- Clean process shutdown on app close
- Works in packaged and dev modes

---

## Sample Output

See `SAMPLE_OUTPUT.json` for a complete ranking example with 4 candidates:

### Results Summary:
1. **Alice Chen** (82%) — **HIRE**: Real ML engineer with 8 years Python, 4 years embeddings at Google/LinkedIn
2. **Carol Davis** (87%) — **QUALIFIED BUT RISKY**: Exceptional depth but research-focused; 60-day notice
3. **Bob Smith** (38%) — **REJECT**: Full-stack dev transitioning; only 1 year Python (beginner level)
4. **Dave Wilson** (29%) — **DO NOT PROCEED**: Content writer with keyword-stuffed profile; integrity flagged

### Key Win: Keyword-Trap Rejection ✓
- Dave Wilson (keyword-stuffer, 29%) ranked last
- Alice Chen (real ML engineer, 82%) ranked first
- Semantic layer + depth-weighted matching prevents false positives

---

## How to Use the App

### 1. Define the Job
- Enter job title (e.g., "Senior ML Engineer")
- Add optional narrative describing ideal candidate
- List required skills (e.g., Python, Embeddings, Ranking)
- Add nice-to-have skills
- Set experience range (min/ideal/max years)
- Choose preferred locations and work modes

### 2. Load Candidates
- **Drag & Drop**: Drop `.json` or `.jsonl` file
- **Paste JSON**: Click "Paste JSON" to edit raw candidate data
- **Load Samples**: Click "Load 3 Samples" to test with built-in mock candidates

### 3. Rank
- Click "🚀 Rank Candidates"
- Results show: score, must-have badges, reasoning, flags

### 4. Export
- Click "📥 Download XLSX" for spreadsheet export

---

## Technical Details

### Scoring Formula
```
base_score = 0.25 * semantic_similarity
           + 0.40 * must_have_coverage          # depth-weighted: prof × duration × assessment
           + 0.20 * career_fit                  # JD-specific disqualifier bypasses
           + 0.15 * behavioral_engagement      # [0.65, 1.15] narrowed multiplier

final_score = base_score * logistics_multiplier
            * 0.5 (if honeypot flagged)        # down-weight, don't exclude
```

### Must-Have Strength Calculation
For each must-have skill:
```
strength = proficiency_weight × duration_weight × assessment_percentile_boost

proficiency_weight: {Beginner: 0.3, Intermediate: 0.6, Advanced: 0.85, Expert: 1.0}
duration_weight: log-scale (0.1@1mo, 0.7@12mo, 1.0@60mo+)
assessment_boost: percentile vs dataset (e.g., 92/100 → 0.92)
```

### Career Fit Disqualifier Bypasses
- **Research-only** → 0.0 (hard exclude)
- **LangChain-only** → 0.1 (severe penalty)
- **No recent production** → 0.15 (risky)
- **Consulting-only** → 0.25 (wrong mindset)
- **Title-chaser** → 0.35 (opportunistic)
- **Graded fit** for everyone else (0.5–1.0 based on product company years)

### Honeypot Checks
8 consistency checks detect integrity concerns:
1. Date overlap conflicts
2. Duration mismatches
3. Future dates
4. Unrealistic timelines
5. Skill keyword stuffing (no proficiency/assessment)
6. Missing critical fields
7. Salary sanity (extreme ranges)
8. Education validity

Flagged candidates are down-weighted (×0.5), not excluded. Concerns are visible in reasoning.

---

## Development & Customization

### From Source (Dev Mode)
```bash
cd App/electron-app
npm install
npm start
```

Backend auto-starts, frontend opens in dev window.

### Manual Backend + Browser
```bash
cd App/backend
pip install -r requirements.txt
python -m uvicorn app.main:app --reload --port 8000
```
Then visit `http://localhost:8000/docs` for Swagger docs.

### Build One-Click Installers
```bash
cd App/electron-app
npm run dist:portable      # Creates AI-Recruiter-Portable.exe (~99MB)
npm run dist                # Creates both portable + NSIS installer
```

---

## Files & Commit

All code is in the `App/` folder:
- `backend/` — FastAPI ranking engine
- `frontend/` — HTML/CSS/JS UI
- `electron-app/` — Electron wrapper + build config

Latest commit: **Implement 4-stage semantic candidate ranking system with Electron desktop app**

Changes include:
- Real semantic embeddings (sentence-transformers)
- Depth-weighted skill matching (proficiency × duration × assessment)
- JD-specific career fit scoring with 5 disqualifier bypasses
- Honeypot detection (flagged + down-weighted, not excluded)
- Behavioral multiplier narrowed to [0.65, 1.15]
- Professional Electron desktop app with auto-start backend
- Branded UI, splash screen, custom icon
- One-click portable exe (99MB) + installer (116MB)

---

## Build Plan Status: ✅ COMPLETE

| Feature | Status | Details |
|---------|--------|---------|
| Semantic layer | ✅ | Real embeddings (cosine similarity) |
| Depth-weighted matching | ✅ | Proficiency × duration × assessment |
| Career fit scoring | ✅ | 5 disqualifier bypasses + graded fit |
| Honeypot flagging | ✅ | 8 checks, flagged + down-weighted |
| Behavioral realism | ✅ | [0.65, 1.15] range prevents passive elimination |
| Keyword-trap rejection | ✅ | Real engineers rank above keyword-stuffers |
| One-click desktop app | ✅ | Portable exe + installer, auto-start backend |
| Professional UI | ✅ | Dark mode, glassmorphism, responsive |
| Reproducibility | ✅ | Deterministic ranking via tie-break chain |
| Fully offline | ✅ | No external dependencies except Google Fonts |

---

## Support

For questions or issues:
1. Check `App/README.md` for detailed architecture
2. Check `SUBMISSION_CHECKLIST.md` for test results
3. View API docs at `http://localhost:8000/docs` when backend is running
4. Review sample output in `SAMPLE_OUTPUT.json`

---

**Ready for submission!** 🚀

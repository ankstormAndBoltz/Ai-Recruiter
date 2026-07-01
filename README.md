# AI Recruiter — Candidate Ranking System

An AI-powered candidate ranking system that matches job candidates against a job description. The project features a **FastAPI Backend**, a **dynamic HTML/JS Frontend**, and an **Electron Desktop App** wrapper.

## Architecture

The system is split into three main parts:
1. **`backend/` (FastAPI + Python)**
   - **Rule-based JD matching**: Must-haves, nice-to-haves, and hard disqualifiers.
   - **Behavioral signal scoring**: Platform signals (engagement, availability, verification).
   - **Reasoning generation**: Human-readable explanations per candidate.
   - **Large File Support**: Uses multipart streaming via `/rank/file` to process huge `.json` or `.jsonl` candidate datasets without memory crashes.
2. **`frontend/` (Vanilla HTML/CSS/JS)**
   - Dark mode, glassmorphism UI.
   - Interactive Job Description builder.
   - Drag-and-drop file upload for candidates.
   - Real-time API status indicator.
3. **`electron-app/` (Node.js Desktop Wrapper)**
   - Auto-starts the Python FastAPI backend in the background.
   - Displays a custom branded splash screen while the server warms up.
   - Opens the frontend as a native desktop application window.

---

## How to Run

### Option 1: Desktop App (Recommended)
This is the easiest way. It handles starting the backend and opening the UI for you.
1. Make sure you have Node.js and Python installed.
2. Install Python dependencies:
   ```bash
   cd backend
   pip install -r requirements.txt
   cd ..
   ```
3. Install Electron dependencies and start the app:
   ```bash
   cd electron-app
   npm install
   npm start
   ```

### Option 2: Run manually (Backend + Browser)
If you just want to run the API and view the frontend in your web browser:
1. Start the backend:
   ```bash
   cd backend
   pip install -r requirements.txt
   python -m uvicorn app.main:app --reload --port 8000
   ```
2. Open the frontend:
   Simply double-click `frontend/index.html` in your file explorer, or drag it into your browser.

---

## Using the Application

1. **Job Description Tab**: Configure the job title, required skills, nice-to-have skills, required experience, and work modes.
2. **Candidates Tab**:
   - **Drag & Drop**: Drop a `.json` or `.jsonl` file containing your candidates into the upload zone.
   - **Sample Data**: Click "Load 3 Samples" to test the system with built-in mock candidates.
   - **Paste JSON**: Click "Paste JSON" to manually paste raw candidate JSON.
3. **Rank Candidates**: Click the "🚀 Rank Candidates" button. The system will send the data to the backend, rank the candidates, and display the results with scores, badges (🥇🥈🥉), and explanations for why they ranked where they did.

---

## API Endpoints (Backend)

| Method | Path | Description |
|--------|------|-------------|
| `GET` | `/health` | Server liveness check (used by Electron for warmup). |
| `POST` | `/rank` | Rank candidates by passing a JSON body (for small datasets). |
| `POST` | `/rank/file` | Rank candidates by uploading a `.json`/`.jsonl` file (multipart form data). Efficient for huge datasets (thousands of candidates). |
| `POST` | `/validate` | Validate a candidate payload without scoring. |

Visit `http://localhost:8000/docs` while the backend is running to view the interactive Swagger API documentation.

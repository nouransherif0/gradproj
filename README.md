# AI-Powered Software Maintenance Knowledge Extractor 🔍

> **Graduation Project** | Automated mining, risk assessment, and interactive AI assistance for software maintenance on Google Gson.

---

## 📌 What is Done So Far (Summary)

- **Bug Extraction & Git Mining**: Extracted 256 bug-fix commits touching 177 source files from Google Gson's git history.
- **LLM Bug Classification**: Classified the commits with Gemini into 126 verified bugs (53 wrong output, 42 crash, 22 parsing, 6 concurrency, 2 security, 1 other) with 1-sentence summaries.
- **Simulated Developer Slack**: Generated 126 realistic developer threads (655 messages, tagged `synthetic: true`) with controlled mention styles (PR, issue, file name, and vague symptoms).
- **Chat-to-Code Traceability Linking**: Rule-based heuristic cascade linking chats to files with 79% coverage, 100% precision, and 89% recall.
- **Grounded "Ask the Codebase" Agent**: Multi-source retrieval agent equipped with 7 tools (search bugs, commits, source code, docs, chats, file history, and bug count aggregation) that strictly cites sources (`BUG-xxx`, `T-xxx`, `CODE-xxx`, `DOC-xxx`).
- **Multi-Turn Conversation Memory**: Fully supports conversational follow-ups, pronoun resolution ("it", "that file", "w anwa3ha eh?"), and language switches (English, Arabic, Franco-Arabic).
- **Developer Profile & Role Awareness**: Agent recognizes who is logged in (e.g. Junior Developer vs Tech Lead) and customizes its guidance, warnings, and explanations accordingly.
- **Database & Persistence**: SQLite database (`data/knowledge_extractor.db`) storing developers, roles, chat sessions, and message history, with ready-to-import JSON exports for MongoDB.
- **Multi-Signal Risk Heatmap Dashboard**: Evaluated 115 source files combining 65% Bug Likelihood + 35% Dependency Impact (direct callers + transitive chain blast radius) with interactive color matrix and file deep-dive modal.
- **Hidden-Months Accuracy Validation**: Temporal hold-out test across multiple cutoff dates (2021, 2022, 2023) showing predictive validity with AUC scores of 0.77 – 0.81.

---

## 🚀 How to Run the Project (Step-by-Step)

### 1. Clone the repository
```bash
git clone https://github.com/nouransherif0/gradproj.git
cd gradproj
```

### 2. Create and activate virtual environment
```bash
python3 -m venv .venv
source .venv/bin/activate   # On Windows use: .venv\Scripts\activate
```

### 3. Install dependencies
```bash
pip install -r requirements.txt
```

### 4. Set up your Gemini API Key
Create a `.env` file from the template:
```bash
cp .env.example .env
```
Open `.env` and paste your Google Gemini API key:
```env
GEMINI_API_KEY=your_gemini_api_key_here
GEMINI_MODEL=gemini-3-flash-preview,gemini-3.1-flash-lite,gemini-2.5-flash
```

### 5. Run the Web Application
```bash
python app/ui_server.py
```

### 6. Open in Browser
Visit: **[http://127.0.0.1:8000](http://127.0.0.1:8000)**

---

## 🔑 Demo Login Accounts

Click any 1-Click Demo Profile on the login screen to sign in instantly:

- **Nouran Sherif** (`nouran` / `admin123`) — *Tech Lead & Maintainer*
- **Salma Ahmed** (`junior_dev` / `password123`) — *Junior Developer*
- **Omar Khaled** (`senior_dev` / `password123`) — *Senior Backend Developer*
- **Karim Mostafa** (`qa_engineer` / `password123`) — *QA & Reliability Engineer*

---

## 🗂️ Project Structure

- `app/ui_server.py` — FastAPI web server, live SSE streaming, and complete UI (Chat + Risk Heatmap).
- `app/mock_agent.py` & `app/agent_full.py` — Grounded reasoning agent with 7 retrieval tools and conversation memory.
- `app/risk_scores.py` — Multi-signal risk scoring based on dependency graph & bug history.
- `app/db.py` — SQLite database module managing users, roles, sessions, and messages.
- `data/processed/` — Processed bug datasets, corpus chunks, and risk scores.
- `db_export/` — Clean JSON collections, Mongoose schemas, and SQL DDL ready for MongoDB.

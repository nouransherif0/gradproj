"""
Ask the Codebase & Risk Heatmap Dashboard - Software Maintenance Knowledge Extractor.
Features:
- Multi-signal Risk Heatmap based on Dependency blast radius & Historical Bug fixes.
- Interactive File Deep-Dive with bug list and coupling metrics.
- Agent chat with developer profile and role awareness.
- SQLite persistence for users, roles, sessions, and messages.
"""

import builtins
import contextlib
import json
import os
import queue
import re
import sys
import threading
import time
from pathlib import Path

APP_DIR = Path(__file__).resolve().parent
ROOT = APP_DIR.parent
sys.path.insert(0, str(APP_DIR))
os.chdir(ROOT)

import db

# Initialize database schema and seed default users
db.init_db()

from fastapi import FastAPI, HTTPException, Request, Response
from fastapi.responses import HTMLResponse, StreamingResponse
from pydantic import BaseModel

RISK_SCORES_PATH = ROOT / "data" / "processed" / "risk_scores.json"
BUGS_CLASSIFIED_PATH = ROOT / "data" / "processed" / "bugs_classified.json"

# ----------------------------------------------------------------------------
# Load the real agent (with the patches from agent_full.py)
# ----------------------------------------------------------------------------
AGENT = None
LOAD_ERROR = None


def load_agent():
    """Import agent_full (applies your patches) without starting its terminal loop."""
    real_input = builtins.input

    def no_input(*args, **kwargs):
        raise EOFError

    builtins.input = no_input
    try:
        try:
            import agent_full  # noqa: F401
        except EOFError:
            pass
    finally:
        builtins.input = real_input
    import mock_agent

    return mock_agent


try:
    AGENT = load_agent()
except Exception as error:
    LOAD_ERROR = f"{type(error).__name__}: {error}"
    print("Could not load the agent:", LOAD_ERROR)

LOCK = threading.Lock()
CACHE = {}

TOOL_RE = re.compile(r"^\s*round\s+(\d+):\s+TOOL\s+(\w+)\((.*)\)\s*$")
RESULT_RE = re.compile(r"^\s*->\s*(.*)$")
ANSWER_RE = re.compile(r"^\s*round\s+\d+:\s+ANSWER")
CALLS_RE = re.compile(r"\(API calls used:\s*(\d+)\)")


class LineWriter:
    """Fake stdout: turns everything the agent prints into complete lines."""

    def __init__(self, put):
        self.put = put
        self.buf = ""

    def write(self, text):
        self.buf += text
        while "\n" in self.buf:
            line, self.buf = self.buf.split("\n", 1)
            self.put(line)
        return len(text)

    def flush(self):
        if self.buf:
            self.put(self.buf)
            self.buf = ""


def sse(obj):
    return "data: " + json.dumps(obj, ensure_ascii=False) + "\n\n"


def stream_answer(question, use_cache, history=None, user_context=None, session_id=None):
    if history is None:
        history = []

    user_id = user_context.get("id") if user_context else None

    # Key includes role and question to prevent greeting collisions across roles
    key = question.strip().lower()
    if user_context:
        key = f"{user_context.get('role', '')}:{user_context.get('username', '')} >> {key}"
    if history:
        hist_ctx = " | ".join(f"{m.get('role')}:{str(m.get('content', '')).strip().lower()}" for m in history[-6:])
        key = f"{hist_ctx} => {key}"

    if use_cache and key in CACHE:
        result = dict(CACHE[key])
        result.update(type="final", cached=True, seconds=0.0)
        if session_id and user_id:
            try:
                db.add_message(session_id, user_id, "assistant", result["answer"], sources=result.get("sources"))
            except Exception as e:
                print("DB cache message save error:", e)
        yield sse(result)
        return

    if AGENT is None:
        yield sse({"type": "error", "text": f"The agent could not be loaded: {LOAD_ERROR}"})
        return

    lines = queue.Queue()

    def work():
        writer = LineWriter(lines.put)
        with LOCK, contextlib.redirect_stdout(writer):
            try:
                AGENT.run_agent(question, history=history, user_context=user_context)
            except Exception as error:
                lines.put("ERROR: " + str(error)[:200])
        writer.flush()
        lines.put(None)

    threading.Thread(target=work, daemon=True).start()

    started = time.time()
    answer, in_answer, sources, calls, error = [], False, [], None, None
    tool_calls_recorded = []

    while True:
        line = lines.get()
        if line is None:
            break
        text = line.rstrip()

        if text.startswith("Sources:"):
            in_answer = False
            raw = text[len("Sources:"):].strip()
            if raw.lower() != "none":
                for item in raw.split(","):
                    item = item.strip().strip("\"'[]{} ")
                    if item and item not in sources:
                        sources.append(item)
            continue
        if in_answer:
            answer.append(text)
            continue
        if text.startswith("Agent:"):
            in_answer = True
            answer.append(text[len("Agent:"):].strip())
            yield sse({"type": "answering"})
            continue

        m = TOOL_RE.match(text)
        if m:
            tool_data = {"round": int(m.group(1)), "tool": m.group(2), "arg": m.group(3)}
            tool_calls_recorded.append(tool_data)
            yield sse({"type": "tool", **tool_data})
            continue
        m = RESULT_RE.match(text)
        if m:
            yield sse({"type": "result", "text": m.group(1)[:160]})
            continue
        if ANSWER_RE.match(text):
            yield sse({"type": "answering"})
            continue
        m = CALLS_RE.search(text)
        if m:
            calls = int(m.group(1))
            continue
        if text.startswith(("ERROR:", "Could not answer")):
            error = text
            continue
        if text.startswith(("[", "LLM error", "WARNING")):
            yield sse({"type": "note", "text": text[:160]})

    final_text = "\n".join(answer).strip()
    if not final_text:
        yield sse({"type": "error", "text": error or "The agent did not return an answer. Try again in a minute."})
        return

    # Save assistant message to SQLite
    if session_id and user_id:
        try:
            db.add_message(session_id, user_id, "assistant", final_text, sources=sources, tool_calls=tool_calls_recorded)
            msgs = db.get_session_messages(session_id, user_id)
            if len(msgs) <= 2:
                clean_title = (question[:42].replace("\n", " ").strip() + "...") if len(question) > 42 else question.strip()
                db.update_session_title(session_id, user_id, clean_title)
        except Exception as e:
            print("DB assistant message save error:", e)

    result = {"answer": final_text, "sources": sources, "calls": calls}
    CACHE[key] = result
    yield sse({"type": "final", "cached": False, "seconds": round(time.time() - started, 1), **result})


# ----------------------------------------------------------------------------
# Web API & Auth Endpoints
# ----------------------------------------------------------------------------
app = FastAPI(title="Software Maintenance Knowledge Extractor")


def get_current_user(request: Request):
    """Retrieve logged in user from cookie or X-User-Id header."""
    user_id = request.cookies.get("user_id") or request.headers.get("X-User-Id")
    if user_id:
        try:
            return db.get_user_by_id(int(user_id))
        except (ValueError, TypeError):
            pass
    return None


class LoginRequest(BaseModel):
    username: str
    password: str


class RegisterRequest(BaseModel):
    username: str
    password: str
    full_name: str
    role: str
    email: str | None = None
    bio: str | None = None


class AskRequest(BaseModel):
    question: str
    session_id: str | None = None
    history: list[dict] = []
    use_cache: bool = True


class NewSessionRequest(BaseModel):
    title: str = "New Conversation"


@app.post("/api/login")
def login(body: LoginRequest, response: Response):
    user = db.authenticate_user(body.username, body.password)
    if not user:
        raise HTTPException(status_code=401, detail="Invalid username or password")
    safe_user = {k: user[k] for k in ["id", "username", "full_name", "email", "role", "avatar_color", "bio"]}
    response.set_cookie(key="user_id", value=str(user["id"]), httponly=True, max_age=86400 * 30, samesite="lax")
    return {"ok": True, "user": safe_user}


@app.post("/api/register")
def register(body: RegisterRequest, response: Response):
    if not body.username.strip() or not body.password.strip() or not body.full_name.strip():
        raise HTTPException(status_code=400, detail="Username, password, and full name are required")
    user = db.create_user(
        username=body.username,
        password=body.password,
        full_name=body.full_name,
        role=body.role or "Junior Developer",
        email=body.email,
        bio=body.bio,
    )
    if not user:
        raise HTTPException(status_code=400, detail="Username already exists. Please pick another.")
    response.set_cookie(key="user_id", value=str(user["id"]), httponly=True, max_age=86400 * 30, samesite="lax")
    return {"ok": True, "user": user}


@app.post("/api/logout")
def logout(response: Response):
    response.delete_cookie(key="user_id")
    return {"ok": True}


@app.get("/api/me")
def me(request: Request):
    user = get_current_user(request)
    if not user:
        return {"authenticated": False}
    return {"authenticated": True, "user": user}


@app.get("/api/developers")
def developers():
    """List developers and roles currently in the database."""
    return {"developers": db.list_users()}


@app.get("/api/sessions")
def list_sessions(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    return {"sessions": db.list_sessions_for_user(user["id"])}


@app.post("/api/sessions")
def create_session(body: NewSessionRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    session_id = db.create_session(user["id"], title=body.title)
    return {"ok": True, "session_id": session_id, "title": body.title}


@app.get("/api/sessions/{session_id}")
def get_session(session_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    messages = db.get_session_messages(session_id, user["id"])
    return {"session_id": session_id, "messages": messages}


@app.delete("/api/sessions/{session_id}")
def delete_session(session_id: str, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Not logged in")
    db.delete_session(session_id, user["id"])
    return {"ok": True}


# ----------------------------------------------------------------------------
# Risk Scores API (Heatmap & File Deep-Dive)
# ----------------------------------------------------------------------------
@app.get("/api/risk-scores")
def get_risk_scores():
    """Return all scored files based on dependency blast radius and historical bugs."""
    if not RISK_SCORES_PATH.exists():
        raise HTTPException(status_code=404, detail="Risk scores not found. Run risk_scores.py first.")
    scores = json.loads(RISK_SCORES_PATH.read_text(encoding="utf-8"))
    colors = {
        "red": sum(1 for s in scores if s["color"] == "red"),
        "orange": sum(1 for s in scores if s["color"] == "orange"),
        "yellow": sum(1 for s in scores if s["color"] == "yellow"),
        "green": sum(1 for s in scores if s["color"] == "green"),
    }
    return {
        "total_files": len(scores),
        "colors": colors,
        "files": scores,
    }


@app.get("/api/risk-scores/file/{filename:path}")
def get_file_risk_detail(filename: str):
    """Return in-depth risk analysis and historical bug records for a specific file."""
    if not RISK_SCORES_PATH.exists():
        raise HTTPException(status_code=404, detail="Risk scores not found")
    scores = json.loads(RISK_SCORES_PATH.read_text(encoding="utf-8"))
    file_record = next(
        (s for s in scores if filename.lower() in s["path"].lower() or filename.lower() == s["path"].split("/")[-1].lower()),
        None,
    )
    if not file_record:
        raise HTTPException(status_code=404, detail="File not found in risk scores")

    # Find historical bug fixes touching this file
    bugs = []
    if BUGS_CLASSIFIED_PATH.exists():
        all_bugs = json.loads(BUGS_CLASSIFIED_PATH.read_text(encoding="utf-8"))
        for b in all_bugs:
            if any(filename.lower() in f.lower() or f.split("/")[-1].lower() == file_record["path"].split("/")[-1].lower() for f in b.get("files", [])):
                bugs.append({
                    "id": b["id"],
                    "date": str(b.get("date", ""))[:10],
                    "title": b.get("title", ""),
                    "author": b.get("author", ""),
                    "bug_type": b.get("bug_type", "other"),
                    "summary": b.get("summary", ""),
                    "pr_number": b.get("pr_number"),
                })
    bugs.sort(key=lambda x: x["date"], reverse=True)
    return {"file": file_record, "bugs": bugs}


@app.post("/ask")
def ask(body: AskRequest, request: Request):
    user = get_current_user(request)
    user_context = None
    if user:
        user_context = {
            "id": user["id"],
            "username": user["username"],
            "full_name": user["full_name"],
            "role": user["role"],
        }
        sess = db.get_or_create_session(user["id"], session_id=body.session_id, title=body.question[:40])
        body.session_id = sess["id"]
        db.add_message(body.session_id, user["id"], "user", body.question)

    return StreamingResponse(
        stream_answer(
            question=body.question,
            use_cache=body.use_cache,
            history=body.history,
            user_context=user_context,
            session_id=body.session_id,
        ),
        media_type="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@app.post("/clear-cache")
def clear_cache():
    CACHE.clear()
    return {"ok": True}


@app.get("/health")
def health():
    return {
        "agent_loaded": AGENT is not None,
        "error": LOAD_ERROR,
        "cached_answers": len(CACHE),
        "database": str(db.DB_PATH),
    }


@app.get("/", response_class=HTMLResponse)
def index():
    return HTML


# ----------------------------------------------------------------------------
# Complete Web Interface: Chatbot + Risk Heatmap Dashboard
# ----------------------------------------------------------------------------
HTML = r"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>Ask the Codebase & Risk Heatmap · Knowledge Extractor</title>
<style>
  :root {
    --bg: #0c0e12;
    --panel: #14171f;
    --panel2: #1b202b;
    --line: #262c3a;
    --text: #e8eaf0;
    --muted: #8b93a7;
    --accent: #7c9cff;
    --accent2: #a78bfa;
    --ok: #06d6a0;
    --warn: #ffb703;
    --orange: #fb8500;
    --red: #ff4d6d;
    --user-bg: #222b3e;
    --card-shadow: 0 10px 30px -10px rgba(0,0,0,0.6);
  }
  * { box-sizing: border-box; }
  html, body { height: 100%; margin: 0; }
  body {
    background: var(--bg);
    color: var(--text);
    font: 14.5px/1.6 -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, "Noto Sans Arabic", sans-serif;
    display: flex;
    flex-direction: column;
    overflow: hidden;
  }

  /* Header */
  header {
    height: 60px;
    display: flex;
    align-items: center;
    gap: 14px;
    padding: 0 20px;
    border-bottom: 1px solid var(--line);
    background: var(--panel);
    flex-shrink: 0;
    z-index: 20;
  }
  .logo {
    width: 34px;
    height: 34px;
    border-radius: 9px;
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    display: grid;
    place-items: center;
    font-weight: 800;
    color: #0c0e12;
    font-size: 16px;
  }
  .title-group h1 { font-size: 15px; margin: 0; font-weight: 600; line-height: 1.2; }
  .title-group small { color: var(--muted); font-size: 11.5px; }

  /* Navigation Mode Tabs */
  .nav-tabs {
    display: flex;
    background: var(--panel2);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 3px;
    gap: 2px;
    margin-left: 10px;
  }
  .nav-tab {
    background: transparent;
    border: 0;
    color: var(--muted);
    padding: 5px 14px;
    border-radius: 999px;
    font-size: 13px;
    font-weight: 600;
    cursor: pointer;
    transition: all .2s;
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .nav-tab:hover { color: var(--text); }
  .nav-tab.active {
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: #0c0e12;
    font-weight: 700;
  }

  .spacer { flex: 1; }
  .header-actions { display: flex; align-items: center; gap: 10px; }

  button.btn {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: var(--panel2);
    color: var(--text);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 6px 12px;
    cursor: pointer;
    font-size: 13px;
    font-weight: 500;
    transition: all .2s;
  }
  button.btn:hover { border-color: var(--accent); color: #fff; }
  button.btn.primary {
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: #0c0e12;
    border: 0;
    font-weight: 700;
  }

  /* User profile badge */
  .user-badge {
    display: flex;
    align-items: center;
    gap: 8px;
    background: var(--panel2);
    border: 1px solid var(--line);
    border-radius: 999px;
    padding: 3px 10px 3px 4px;
    cursor: pointer;
  }
  .avatar {
    width: 28px;
    height: 28px;
    border-radius: 50%;
    background: var(--accent);
    color: #0c0e12;
    display: grid;
    place-items: center;
    font-weight: 800;
    font-size: 12px;
  }
  .user-badge .info { display: flex; flex-direction: column; line-height: 1.1; }
  .user-badge .name { font-size: 12.5px; font-weight: 600; }
  .user-badge .role-tag {
    font-size: 10px;
    color: var(--accent);
    text-transform: uppercase;
    font-weight: 700;
    letter-spacing: .5px;
  }

  /* Workspace Containers */
  .workspace {
    flex: 1;
    display: flex;
    overflow: hidden;
    position: relative;
  }

  /* Sidebar Drawer */
  .sidebar {
    width: 270px;
    border-right: 1px solid var(--line);
    background: var(--panel);
    display: flex;
    flex-direction: column;
    flex-shrink: 0;
    transition: margin-left .25s ease;
  }
  .sidebar.hidden { margin-left: -270px; }
  .sidebar-header {
    padding: 14px 16px;
    display: flex;
    justify-content: space-between;
    align-items: center;
    border-bottom: 1px solid var(--line);
  }
  .sidebar-header h3 { margin: 0; font-size: 13px; font-weight: 600; text-transform: uppercase; color: var(--muted); }
  .session-list { flex: 1; overflow-y: auto; padding: 8px; }
  .session-item {
    display: flex;
    align-items: center;
    justify-content: space-between;
    padding: 8px 10px;
    border-radius: 8px;
    color: var(--muted);
    font-size: 13px;
    cursor: pointer;
    margin-bottom: 4px;
    transition: all .15s;
  }
  .session-item:hover, .session-item.active { background: var(--panel2); color: var(--text); }
  .session-item.active { border-left: 3px solid var(--accent); color: #fff; }
  .session-item .txt { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; flex: 1; }
  .session-item .del { opacity: 0; padding: 2px 6px; font-size: 14px; border-radius: 4px; color: var(--red); }
  .session-item:hover .del { opacity: 1; }

  /* ---------------- Chat View ---------------- */
  #chatView {
    flex: 1;
    display: flex;
    flex-direction: column;
    overflow-y: auto;
    scroll-behavior: smooth;
    position: relative;
  }
  #log {
    max-width: 860px;
    width: 100%;
    margin: 0 auto;
    padding: 24px 20px 100px;
    flex: 1;
  }
  .hero { text-align: center; margin: 6vh 0 24px; }
  .hero-badge {
    display: inline-flex;
    align-items: center;
    gap: 6px;
    background: rgba(124, 156, 255, 0.1);
    color: var(--accent);
    border: 1px solid rgba(124, 156, 255, 0.25);
    padding: 4px 12px;
    border-radius: 999px;
    font-size: 12px;
    font-weight: 600;
    margin-bottom: 14px;
  }
  .hero h2 { font-size: 26px; margin: 0 0 6px; font-weight: 700; }
  .hero p { color: var(--muted); margin: 0 0 22px; font-size: 14px; }
  .chips { display: flex; flex-wrap: wrap; gap: 8px; justify-content: center; max-width: 760px; margin: 0 auto; }
  .chip {
    background: var(--panel);
    border: 1px solid var(--line);
    color: var(--text);
    border-radius: 999px;
    padding: 7px 14px;
    cursor: pointer;
    font-size: 13px;
    transition: all .2s;
  }
  .chip:hover { border-color: var(--accent); background: var(--panel2); color: #fff; }

  /* Chat Bubbles */
  .msg { display: flex; gap: 12px; margin: 18px 0; animation: in .25s ease; }
  @keyframes in { from { opacity: 0; transform: translateY(6px); } to { opacity: 1; transform: none; } }
  .msg.user { justify-content: flex-end; }
  .msg.user .bubble {
    background: var(--user-bg);
    border: 1px solid rgba(124, 156, 255, 0.2);
    padding: 10px 15px;
    border-radius: 16px 16px 4px 16px;
    max-width: 80%;
    white-space: pre-wrap;
    font-size: 14px;
  }
  .msg.bot .avatar {
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    color: #0c0e12;
    flex-shrink: 0;
  }
  .body { flex: 1; min-width: 0; }
  .text p { margin: 0 0 10px; }
  .text ul { margin: 0 0 10px; padding-left: 20px; }
  .text code { background: var(--panel2); border: 1px solid var(--line); padding: 2px 6px; border-radius: 5px; font: 12.5px ui-monospace, Menlo, monospace; }

  /* Thinking trace */
  .trace {
    margin: 0 0 10px;
    border: 1px solid var(--line);
    border-radius: 9px;
    background: var(--panel);
    font-size: 12.5px;
  }
  .trace summary {
    cursor: pointer;
    padding: 7px 12px;
    color: var(--muted);
    list-style: none;
    display: flex;
    align-items: center;
    gap: 8px;
    user-select: none;
  }
  .trace summary::-webkit-details-marker { display: none; }
  .trace .steps { padding: 4px 12px 10px; border-top: 1px solid var(--line); }
  .step { margin-top: 8px; }
  .step b { color: var(--accent); font-weight: 600; }
  .step .res { color: var(--muted); font: 11.5px ui-monospace, Menlo, monospace; margin-top: 2px; word-break: break-all; }
  .spin { width: 12px; height: 12px; border: 2px solid var(--line); border-top-color: var(--accent); border-radius: 50%; animation: s .7s linear infinite; }
  @keyframes s { to { transform: rotate(360deg); } }
  .done { color: var(--ok); }
  .sources { display: flex; flex-wrap: wrap; gap: 6px; margin-top: 8px; }
  .src {
    font: 11.5px ui-monospace, Menlo, monospace;
    background: var(--panel2);
    border: 1px solid var(--line);
    color: var(--accent);
    padding: 2px 7px;
    border-radius: 6px;
  }
  .src.bug { color: #ff8f8f; border-color: rgba(255,143,143,0.3); }
  .src.chat { color: #7fe3b0; border-color: rgba(127,227,176,0.3); }
  .src.doc { color: #f5d27a; border-color: rgba(245,210,122,0.3); }
  .meta { color: var(--muted); font-size: 11.5px; margin-top: 8px; }
  .err { color: var(--red); }

  /* Composer Footer */
  #chatFooter {
    position: fixed;
    bottom: 0;
    left: 0;
    right: 0;
    padding: 10px 20px 16px;
    background: linear-gradient(to top, var(--bg) 80%, transparent);
    z-index: 10;
  }
  .composer {
    max-width: 860px;
    margin: 0 auto;
    display: flex;
    gap: 8px;
    align-items: flex-end;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 14px;
    padding: 8px 10px;
    box-shadow: 0 4px 20px rgba(0,0,0,0.4);
  }
  .composer:focus-within { border-color: var(--accent); }
  textarea {
    flex: 1;
    resize: none;
    background: transparent;
    border: 0;
    outline: 0;
    color: var(--text);
    font: inherit;
    max-height: 150px;
    padding: 6px 4px;
  }
  #send {
    background: linear-gradient(135deg, var(--accent), var(--accent2));
    border: 0;
    color: #0c0e12;
    font-weight: 700;
    border-radius: 10px;
    width: 38px;
    height: 38px;
    cursor: pointer;
    display: grid;
    place-items: center;
  }
  #send:disabled { opacity: .4; cursor: default; }

  /* ---------------- Heatmap View ---------------- */
  #heatmapView {
    flex: 1;
    display: none;
    flex-direction: column;
    overflow-y: auto;
    padding: 20px 28px 80px;
  }
  .heatmap-header {
    max-width: 1200px;
    width: 100%;
    margin: 0 auto 20px;
  }
  .stats-row {
    display: grid;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
    gap: 12px;
    margin-bottom: 20px;
  }
  .stat-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 14px 16px;
    display: flex;
    align-items: center;
    gap: 12px;
  }
  .stat-card .indicator {
    width: 14px;
    height: 14px;
    border-radius: 50%;
    flex-shrink: 0;
  }
  .stat-card .val { font-size: 22px; font-weight: 700; line-height: 1.1; }
  .stat-card .lbl { font-size: 12px; color: var(--muted); margin-top: 2px; }

  /* Filter toolbar */
  .toolbar {
    display: flex;
    flex-wrap: wrap;
    align-items: center;
    justify-content: space-between;
    gap: 12px;
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 12px 16px;
    margin-bottom: 20px;
  }
  .filter-pills { display: flex; gap: 6px; flex-wrap: wrap; }
  .filter-pill {
    background: var(--panel2);
    border: 1px solid var(--line);
    color: var(--muted);
    padding: 5px 12px;
    border-radius: 8px;
    font-size: 12.5px;
    cursor: pointer;
    font-weight: 500;
    transition: all .2s;
  }
  .filter-pill:hover, .filter-pill.active { color: #fff; border-color: var(--accent); }
  .filter-pill.active { background: rgba(124,156,255,0.15); color: var(--accent); }
  .filter-pill.red.active { background: rgba(255,77,109,0.15); color: var(--red); border-color: var(--red); }
  .filter-pill.orange.active { background: rgba(251,133,0,0.15); color: var(--orange); border-color: var(--orange); }
  .filter-pill.yellow.active { background: rgba(255,183,3,0.15); color: var(--warn); border-color: var(--warn); }
  .filter-pill.green.active { background: rgba(6,214,160,0.15); color: var(--ok); border-color: var(--ok); }

  .search-sort-box { display: flex; align-items: center; gap: 10px; }
  .search-input {
    background: var(--panel2);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 6px 12px;
    color: var(--text);
    font: inherit;
    font-size: 13px;
    outline: none;
    width: 220px;
  }
  .search-input:focus { border-color: var(--accent); }
  .select-input {
    background: var(--panel2);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 6px 10px;
    color: var(--text);
    font: inherit;
    font-size: 13px;
    outline: none;
  }

  /* Heatmap Cards Grid */
  .grid-container {
    max-width: 1200px;
    width: 100%;
    margin: 0 auto;
    display: grid;
    grid-template-columns: repeat(auto-fill, minmax(280px, 1fr));
    gap: 14px;
  }
  .heatmap-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 14px 16px;
    cursor: pointer;
    transition: all .2s;
    position: relative;
    overflow: hidden;
  }
  .heatmap-card:hover {
    transform: translateY(-3px);
    box-shadow: 0 8px 24px rgba(0,0,0,0.4);
    border-color: rgba(124,156,255,0.4);
  }
  .heatmap-card.red { border-left: 4px solid var(--red); }
  .heatmap-card.orange { border-left: 4px solid var(--orange); }
  .heatmap-card.yellow { border-left: 4px solid var(--warn); }
  .heatmap-card.green { border-left: 4px solid var(--ok); }

  .card-top { display: flex; justify-content: space-between; align-items: flex-start; margin-bottom: 8px; }
  .card-title { font-size: 14.5px; font-weight: 700; color: #fff; }
  .card-pkg { font-size: 11px; color: var(--muted); margin-bottom: 10px; }
  .score-badge {
    padding: 3px 8px;
    border-radius: 6px;
    font-size: 13px;
    font-weight: 800;
    line-height: 1;
  }
  .score-badge.red { background: rgba(255,77,109,0.18); color: var(--red); border: 1px solid rgba(255,77,109,0.4); }
  .score-badge.orange { background: rgba(251,133,0,0.18); color: var(--orange); border: 1px solid rgba(251,133,0,0.4); }
  .score-badge.yellow { background: rgba(255,183,3,0.18); color: var(--warn); border: 1px solid rgba(255,183,3,0.4); }
  .score-badge.green { background: rgba(6,214,160,0.18); color: var(--ok); border: 1px solid rgba(6,214,160,0.4); }

  .card-metrics {
    display: flex;
    gap: 8px;
    margin-bottom: 10px;
    font-size: 12px;
  }
  .metric-tag {
    background: var(--panel2);
    border: 1px solid var(--line);
    padding: 2px 7px;
    border-radius: 6px;
    color: var(--muted);
  }
  .card-driver {
    font-size: 11.5px;
    color: var(--muted);
    border-top: 1px solid var(--line);
    padding-top: 8px;
    white-space: nowrap;
    overflow: hidden;
    text-overflow: ellipsis;
  }

  /* File Detail Modal */
  .modal-overlay {
    position: fixed;
    inset: 0;
    background: rgba(8, 10, 14, 0.85);
    backdrop-filter: blur(8px);
    display: grid;
    place-items: center;
    z-index: 100;
  }
  .detail-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 18px;
    width: 90%;
    max-width: 760px;
    max-height: 88vh;
    display: flex;
    flex-direction: column;
    padding: 24px;
    box-shadow: var(--card-shadow);
  }
  .detail-card-body {
    overflow-y: auto;
    padding-right: 6px;
    margin-top: 14px;
  }
  .metrics-grid-2 {
    display: grid;
    grid-template-columns: 1fr 1fr;
    gap: 14px;
    margin: 16px 0;
  }
  .detail-block {
    background: var(--panel2);
    border: 1px solid var(--line);
    border-radius: 12px;
    padding: 14px;
  }
  .detail-block h4 {
    margin: 0 0 10px;
    font-size: 13px;
    text-transform: uppercase;
    color: var(--accent);
    display: flex;
    align-items: center;
    gap: 6px;
  }
  .detail-row {
    display: flex;
    justify-content: space-between;
    padding: 5px 0;
    font-size: 13px;
    border-bottom: 1px solid rgba(255,255,255,0.04);
  }
  .detail-row span:first-child { color: var(--muted); }
  .detail-row span:last-child { font-weight: 600; }

  /* Historical bugs list */
  .bug-item {
    background: var(--panel2);
    border: 1px solid var(--line);
    border-radius: 8px;
    padding: 10px 12px;
    margin-bottom: 8px;
    font-size: 12.5px;
  }
  .bug-item-top { display: flex; justify-content: space-between; margin-bottom: 4px; }
  .bug-id-pill {
    background: rgba(255,77,109,0.15);
    color: var(--red);
    border: 1px solid rgba(255,77,109,0.3);
    padding: 1px 6px;
    border-radius: 4px;
    font-weight: 700;
    font-family: ui-monospace, Menlo, monospace;
    font-size: 11px;
  }
  .bug-summary-txt { color: var(--text); line-height: 1.4; }

  /* Login Card */
  .auth-card {
    background: var(--panel);
    border: 1px solid var(--line);
    border-radius: 18px;
    width: 100%;
    max-width: 440px;
    padding: 30px;
    box-shadow: var(--card-shadow);
  }
  .auth-card h2 { font-size: 22px; margin: 0 0 6px; font-weight: 700; }
  .auth-card p.sub { color: var(--muted); font-size: 13.5px; margin: 0 0 20px; }
  .tab-buttons { display: flex; border-bottom: 1px solid var(--line); margin-bottom: 18px; }
  .tab-btn {
    flex: 1; background: transparent; border: 0; padding: 10px; color: var(--muted);
    font-weight: 600; font-size: 13.5px; cursor: pointer; border-bottom: 2px solid transparent;
  }
  .tab-btn.active { color: var(--accent); border-bottom-color: var(--accent); }
  .form-group { margin-bottom: 14px; text-align: left; }
  .form-group label { display: block; font-size: 12.5px; color: var(--muted); margin-bottom: 6px; font-weight: 500; }
  .form-group input, .form-group select {
    width: 100%; background: var(--panel2); border: 1px solid var(--line);
    border-radius: 8px; padding: 10px 12px; color: var(--text); font: inherit; font-size: 13.5px; outline: none;
  }
  .form-group input:focus, .form-group select:focus { border-color: var(--accent); }
  .quick-accounts { margin-top: 18px; padding-top: 16px; border-top: 1px solid var(--line); }
  .quick-accounts small { display: block; color: var(--muted); font-size: 11.5px; margin-bottom: 8px; font-weight: 600; text-transform: uppercase; }
  .quick-pills { display: grid; grid-template-columns: 1fr 1fr; gap: 6px; }
  .quick-pill {
    background: var(--panel2); border: 1px solid var(--line); color: var(--text); border-radius: 8px;
    padding: 6px 8px; font-size: 12px; cursor: pointer; text-align: left; transition: all .15s;
  }
  .quick-pill:hover { border-color: var(--accent); background: rgba(124,156,255,0.1); }
  .quick-pill b { display: block; color: var(--text); font-size: 12px; }
  .quick-pill span { font-size: 10.5px; color: var(--muted); }
  .modal-err { color: var(--red); font-size: 12.5px; margin-bottom: 12px; display: none; }
</style>
</head>
<body>

<!-- Header -->
<header>
  <div class="logo">K</div>
  <div class="title-group">
    <h1>Knowledge Extractor</h1>
    <small>Google Gson · Software Maintenance</small>
  </div>

  <!-- Navigation View Tabs -->
  <div class="nav-tabs">
    <button class="nav-tab active" id="tabChat" onclick="switchView('chat')">💬 Ask Codebase</button>
    <button class="nav-tab" id="tabHeatmap" onclick="switchView('heatmap')">🗺️ Risk Heatmap</button>
  </div>

  <button class="btn" id="toggleSidebar" title="Toggle chat history" style="margin-left:8px;">📁 History</button>
  <button class="btn" id="newChatBtn" title="Start new conversation">+ New Chat</button>

  <div class="spacer"></div>

  <div class="header-actions">
    <label style="display:flex;align-items:center;gap:6px;font-size:12px;color:var(--muted);cursor:pointer">
      <input type="checkbox" id="cache" checked> Fast Cache
    </label>

    <!-- User Badge -->
    <div id="userBadge" class="user-badge" style="display:none;" title="Logged-in Developer Profile">
      <div id="userAvatar" class="avatar">U</div>
      <div class="info">
        <span id="userName" class="name">Developer</span>
        <span id="userRole" class="role-tag">Role</span>
      </div>
    </div>

    <button id="logoutBtn" class="btn" style="display:none;" title="Sign out of developer portal">Logout</button>
    <button id="loginBtn" class="btn primary" style="display:none;">Sign In</button>
  </div>
</header>

<!-- Main Workspace -->
<div class="workspace">
  <!-- Sidebar for saved SQLite Chat Sessions -->
  <aside class="sidebar hidden" id="sidebar">
    <div class="sidebar-header">
      <h3>Saved Chats</h3>
      <button class="btn" id="closeSidebar" style="padding:2px 8px;font-size:12px">✕</button>
    </div>
    <div class="session-list" id="sessionList"></div>
  </aside>

  <!-- View 1: Chat View -->
  <main id="chatView">
    <div id="log"></div>
  </main>

  <!-- View 2: Risk Heatmap Dashboard View -->
  <section id="heatmapView">
    <div class="heatmap-header">
      <div style="display:flex;justify-content:space-between;align-items:flex-end;margin-bottom:16px;">
        <div>
          <h2 style="font-size:24px;margin:0 0 4px;font-weight:700;">Codebase Risk Heatmap</h2>
          <span style="color:var(--muted);font-size:13.5px;">Multi-signal fault-proneness scoring across 115 source files (65% Bug Likelihood + 35% Dependency Impact).</span>
        </div>
        <div style="text-align:right">
          <span style="font-size:12px;color:var(--muted);">Scored using:</span>
          <b style="color:var(--accent);display:block;font-size:13px;">Dependencies (Direct & Chain) + Real Bug Fixes</b>
        </div>
      </div>

      <!-- Stats Summary Row -->
      <div class="stats-row">
        <div class="stat-card">
          <div class="indicator" style="background:var(--red);box-shadow:0 0 10px var(--red);"></div>
          <div>
            <div class="val" id="statRed" style="color:var(--red);">14</div>
            <div class="lbl">High Risk (Score ≥ 70)</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="indicator" style="background:var(--orange);"></div>
          <div>
            <div class="val" id="statOrange" style="color:var(--orange);">55</div>
            <div class="lbl">Medium-High (40 - 69)</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="indicator" style="background:var(--warn);"></div>
          <div>
            <div class="val" id="statYellow" style="color:var(--warn);">36</div>
            <div class="lbl">Moderate (15 - 39)</div>
          </div>
        </div>
        <div class="stat-card">
          <div class="indicator" style="background:var(--ok);"></div>
          <div>
            <div class="val" id="statGreen" style="color:var(--ok);">10</div>
            <div class="lbl">Low Risk (&lt; 15)</div>
          </div>
        </div>
      </div>

      <!-- Filters & Search Toolbar -->
      <div class="toolbar">
        <div class="filter-pills">
          <button class="filter-pill active" onclick="setRiskFilter('all', this)">All (115)</button>
          <button class="filter-pill red" onclick="setRiskFilter('red', this)">🔴 Red (14)</button>
          <button class="filter-pill orange" onclick="setRiskFilter('orange', this)">🟠 Orange (55)</button>
          <button class="filter-pill yellow" onclick="setRiskFilter('yellow', this)">🟡 Yellow (36)</button>
          <button class="filter-pill green" onclick="setRiskFilter('green', this)">🟢 Green (10)</button>
        </div>

        <div class="search-sort-box">
          <input type="text" id="heatmapSearch" class="search-input" placeholder="Search file (e.g. JsonReader)..." oninput="renderHeatmap()">
          <select id="heatmapSort" class="select-input" onchange="renderHeatmap()">
            <option value="score_desc">Sort: Risk Score (High ➜ Low)</option>
            <option value="deps_desc">Sort: Dependencies Blast Radius</option>
            <option value="bugs_desc">Sort: Most Bug Fixes</option>
            <option value="complexity_desc">Sort: Code Complexity</option>
            <option value="name_asc">Sort: Name (A ➜ Z)</option>
          </select>
        </div>
      </div>
    </div>

    <!-- Heatmap Grid -->
    <div class="grid-container" id="heatmapGrid"></div>
  </section>
</div>

<!-- Chat Composer Footer -->
<footer id="chatFooter">
  <div class="composer">
    <textarea id="q" rows="1" dir="auto" placeholder="Ask about bugs, files, or follow up on previous answers..." autofocus></textarea>
    <button id="send" title="Send">&#10148;</button>
  </div>
</footer>

<!-- File Detail Deep-Dive Modal -->
<div class="modal-overlay" id="fileModal" style="display:none;" onclick="if(event.target===this) closeFileModal();">
  <div class="detail-card">
    <div style="display:flex;justify-content:space-between;align-items:flex-start;">
      <div>
        <div style="display:flex;align-items:center;gap:10px;">
          <h2 id="modalFileName" style="margin:0;font-size:22px;font-weight:700;">JsonReader.java</h2>
          <span id="modalFileScoreBadge" class="score-badge red">89 / 100</span>
        </div>
        <span id="modalFilePath" style="font-size:12px;color:var(--muted);font-family:ui-monospace,Menlo,monospace;">gson/src/main/java/com/google/gson/stream/JsonReader.java</span>
      </div>
      <button class="btn" onclick="closeFileModal()" style="padding:4px 10px;">✕</button>
    </div>

    <div class="detail-card-body">
      <!-- 2 Column Metrics Grid: Dependency Impact vs Bug Likelihood -->
      <div class="metrics-grid-2">
        <!-- Dependency Impact Block -->
        <div class="detail-block">
          <h4>🔗 Dependency Impact (35% Weight)</h4>
          <div class="detail-row">
            <span>Direct Callers (used_by):</span>
            <span id="modalUsedBy" style="color:var(--accent);">39 files</span>
          </div>
          <div class="detail-row">
            <span>Transitive Blast Radius (chains):</span>
            <span id="modalUsedChain" style="color:var(--accent2);">70 files</span>
          </div>
          <div class="detail-row">
            <span>Impact Percentile:</span>
            <span id="modalImpactPct">92.3%</span>
          </div>
          <p style="font-size:11.5px;color:var(--muted);margin:8px 0 0;">
            Any breaking change or regression in this class directly ripples through 70 dependent files in Gson.
          </p>
        </div>

        <!-- Bug Likelihood Block -->
        <div class="detail-block">
          <h4>🐛 Bug History & Churn (65% Weight)</h4>
          <div class="detail-row">
            <span>Historical Bug Fixes:</span>
            <span id="modalBugCount" style="color:var(--red);">15 bugs</span>
          </div>
          <div class="detail-row">
            <span>Commit Churn:</span>
            <span id="modalCommitCount">113 commits</span>
          </div>
          <div class="detail-row">
            <span>Cyclomatic Complexity:</span>
            <span id="modalBranches">351 branches</span>
          </div>
          <div class="detail-row">
            <span>Ownership Concentration:</span>
            <span id="modalKnowledge">34.5%</span>
          </div>
        </div>
      </div>

      <!-- Top Risk Drivers -->
      <div style="margin-bottom:18px;">
        <h4 style="font-size:13px;color:var(--muted);text-transform:uppercase;margin:0 0 8px;">Top Risk Drivers for this file:</h4>
        <div id="modalTopDrivers" style="display:flex;flex-wrap:wrap;gap:8px;"></div>
      </div>

      <!-- Historical Bugs List -->
      <div style="margin-bottom:18px;">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px;">
          <h4 style="font-size:13px;color:var(--muted);text-transform:uppercase;margin:0;">Historical Bug Fixes on this file:</h4>
          <span id="modalBugsTotalLabel" style="font-size:12px;color:var(--muted);">15 bugs</span>
        </div>
        <div id="modalBugsList" style="max-height:220px;overflow-y:auto;padding-right:4px;"></div>
      </div>

      <!-- Action buttons -->
      <div style="display:flex;justify-content:space-between;align-items:center;padding-top:14px;border-top:1px solid var(--line);">
        <button id="askAgentAboutFileBtn" class="btn primary" style="padding:8px 16px;">
          💬 Ask Agent: Why is this file risky?
        </button>
        <button class="btn" onclick="closeFileModal()">Close</button>
      </div>
    </div>
  </div>
</div>

<!-- Login / Register Modal -->
<div class="modal-overlay" id="authModal" style="display:none;">
  <div class="auth-card">
    <div style="display:flex;align-items:center;gap:10px;margin-bottom:12px;">
      <div class="logo">K</div>
      <div>
        <h2 style="margin:0">Developer Portal</h2>
        <span style="font-size:12px;color:var(--muted)">Knowledge Extractor · Auth</span>
      </div>
    </div>
    <p class="sub">Sign in to connect the AI Agent with your developer identity, permissions, and persistent chat database.</p>

    <div class="tab-buttons">
      <button class="tab-btn active" id="tabSignIn">Sign In</button>
      <button class="tab-btn" id="tabRegister">Register Developer</button>
    </div>

    <div id="modalErr" class="modal-err"></div>

    <!-- Sign In Form -->
    <form id="signInForm" onsubmit="event.preventDefault(); doLogin();">
      <div class="form-group">
        <label>Username</label>
        <input type="text" id="loginUser" required placeholder="e.g. nouran or junior_dev">
      </div>
      <div class="form-group">
        <label>Password</label>
        <input type="password" id="loginPass" required placeholder="••••••••">
      </div>
      <button type="submit" class="btn primary" style="width:100%;padding:10px;margin-top:6px;">Sign In</button>
    </form>

    <!-- Register Form -->
    <form id="registerForm" style="display:none;" onsubmit="event.preventDefault(); doRegister();">
      <div class="form-group">
        <label>Full Name</label>
        <input type="text" id="regFullName" required placeholder="e.g. Nouran Sherif">
      </div>
      <div class="form-group">
        <label>Username</label>
        <input type="text" id="regUser" required placeholder="e.g. nouran_dev">
      </div>
      <div class="form-group">
        <label>Developer Role</label>
        <select id="regRole">
          <option value="Tech Lead & Maintainer">Tech Lead & Maintainer</option>
          <option value="Senior Developer">Senior Developer</option>
          <option value="Junior Developer" selected>Junior Developer</option>
          <option value="QA & Reliability Engineer">QA & Reliability Engineer</option>
          <option value="Security Engineer">Security Engineer</option>
        </select>
      </div>
      <div class="form-group">
        <label>Password</label>
        <input type="password" id="regPass" required placeholder="••••••••">
      </div>
      <button type="submit" class="btn primary" style="width:100%;padding:10px;margin-top:6px;">Create Account</button>
    </form>

    <!-- Quick Demo Accounts -->
    <div class="quick-accounts" id="quickAccountsSection">
      <small>One-Click Demo Profiles (Pre-seeded in DB)</small>
      <div class="quick-pills">
        <div class="quick-pill" onclick="quickFill('nouran', 'admin123')">
          <b>Nouran Sherif</b>
          <span>Tech Lead & Maintainer</span>
        </div>
        <div class="quick-pill" onclick="quickFill('junior_dev', 'password123')">
          <b>Salma Ahmed</b>
          <span>Junior Developer</span>
        </div>
        <div class="quick-pill" onclick="quickFill('senior_dev', 'password123')">
          <b>Omar Khaled</b>
          <span>Senior Developer</span>
        </div>
        <div class="quick-pill" onclick="quickFill('qa_engineer', 'password123')">
          <b>Karim Mostafa</b>
          <span>QA Engineer</span>
        </div>
      </div>
    </div>
  </div>
</div>

<script>
let currentUser = null;
let currentSessionId = null;
let chatHistory = [];
let busy = false;
let currentView = 'chat';

// Heatmap Data State
let riskData = [];
let activeRiskFilter = 'all';

const log = document.getElementById('log'), chatView = document.getElementById('chatView');
const heatmapView = document.getElementById('heatmapView'), chatFooter = document.getElementById('chatFooter');
const q = document.getElementById('q'), sendBtn = document.getElementById('send');
const authModal = document.getElementById('authModal'), modalErr = document.getElementById('modalErr');
const userBadge = document.getElementById('userBadge'), loginBtn = document.getElementById('loginBtn'), logoutBtn = document.getElementById('logoutBtn');
const sidebar = document.getElementById('sidebar'), sessionList = document.getElementById('sessionList');
const fileModal = document.getElementById('fileModal');

const SUGGESTIONS = [
  "How many bugs are in the crash category?",
  "Which file is the buggiest one?",
  "Why does JsonReader have so many bugs?",
  "What is TypeAdapter for?",
  "ايه اكتر ملف فيه مشاكل؟",
  "bos ana developer gded lesa gy el sherka momkn te2oli eh aktr risky files akhod bali w ana bat3aml maaha"
];

const TOOL_LABELS = {
  count_bugs: "Counting bugs in DB",
  search_bugs: "Searching bug history",
  search_commits: "Searching commits",
  search_code: "Searching source code",
  search_docs: "Searching documentation",
  search_chats: "Reading chat threads",
  get_file_history: "Loading file history"
};

function esc(s){ return (s||'').replace(/[&<>]/g, c=>({'&':'&amp;','<':'&lt;','>':'&gt;'}[c])); }
function md(text){
  const inline = s => esc(s).replace(/`([^`]+)`/g,'<code>$1</code>').replace(/\*\*([^*]+)\*\*/g,'<b>$1</b>');
  const out = []; let list = [], para = [];
  const endList = () => { if(list.length){ out.push('<ul>'+list.map(x=>'<li>'+x+'</li>').join('')+'</ul>'); list=[]; } };
  const endPara = () => { if(para.length){ out.push('<p dir="auto">'+para.join('<br>')+'</p>'); para=[]; } };
  for(const raw of (text||'').split('\n')){
    const m = raw.match(/^\s*[-*\u2022]\s+(.*)$/);
    if(m){ endPara(); list.push(inline(m[1])); }
    else if(!raw.trim()){ endList(); endPara(); }
    else { endList(); para.push(inline(raw)); }
  }
  endList(); endPara();
  return out.join('');
}
function scrollDown(){ chatView.scrollTop = chatView.scrollHeight; }

function switchView(view){
  currentView = view;
  if(view === 'chat'){
    document.getElementById('tabChat').classList.add('active');
    document.getElementById('tabHeatmap').classList.remove('active');
    chatView.style.display = 'flex';
    chatFooter.style.display = 'block';
    heatmapView.style.display = 'none';
    scrollDown();
  } else {
    document.getElementById('tabHeatmap').classList.add('active');
    document.getElementById('tabChat').classList.remove('active');
    chatView.style.display = 'none';
    chatFooter.style.display = 'none';
    heatmapView.style.display = 'flex';
    if(!riskData.length) loadRiskScores();
  }
}

// ----------------------------------------------------------------------------
// Heatmap Rendering & Interaction
// ----------------------------------------------------------------------------
async function loadRiskScores(){
  try {
    const res = await fetch('/api/risk-scores');
    const data = await res.json();
    riskData = data.files || [];

    document.getElementById('statRed').textContent = data.colors.red;
    document.getElementById('statOrange').textContent = data.colors.orange;
    document.getElementById('statYellow').textContent = data.colors.yellow;
    document.getElementById('statGreen').textContent = data.colors.green;

    renderHeatmap();
  } catch(e) {
    console.error("Could not load risk scores:", e);
  }
}

function setRiskFilter(color, btn){
  activeRiskFilter = color;
  document.querySelectorAll('.filter-pill').forEach(b => b.classList.remove('active'));
  btn.classList.add('active');
  renderHeatmap();
}

function renderHeatmap(){
  const container = document.getElementById('heatmapGrid');
  const query = (document.getElementById('heatmapSearch').value || '').trim().toLowerCase();
  const sortMode = document.getElementById('heatmapSort').value;

  let filtered = riskData.filter(item => {
    if(activeRiskFilter !== 'all' && item.color !== activeRiskFilter) return false;
    if(query && !item.path.toLowerCase().includes(query)) return false;
    return true;
  });

  filtered.sort((a, b) => {
    if(sortMode === 'score_desc') return b.score - a.score;
    if(sortMode === 'deps_desc') return b.used_by_chain - a.used_by_chain;
    if(sortMode === 'bugs_desc') return b.bugs - a.bugs;
    if(sortMode === 'complexity_desc') return b.branches - a.branches;
    if(sortMode === 'name_asc') return a.path.split('/').pop().localeCompare(b.path.split('/').pop());
    return 0;
  });

  if(!filtered.length){
    container.innerHTML = `<div style="grid-column: 1 / -1; text-align: center; color: var(--muted); padding: 40px;">No files match your filter or search query.</div>`;
    return;
  }

  container.innerHTML = filtered.map(item => {
    const fileName = item.path.split('/').pop();
    const pkg = item.path.replace('gson/src/main/java/', '').replace('/' + fileName, '').replace(/\//g, '.');
    const topDriver = (item.top_factors && item.top_factors[0]) ? item.top_factors[0] : `used by ${item.used_by} files`;

    return `
      <div class="heatmap-card ${item.color}" onclick="openFileDetail('${esc(item.path)}')">
        <div class="card-top">
          <div class="card-title">${esc(fileName)}</div>
          <div class="score-badge ${item.color}">${item.score}</div>
        </div>
        <div class="card-pkg">${esc(pkg)}</div>
        <div class="card-metrics">
          <span class="metric-tag" title="Historical Bug Fixes">🐛 ${item.bugs} bugs</span>
          <span class="metric-tag" title="Direct Dependents (Blast Radius)">🔗 ${item.used_by} (${item.used_by_chain} chain)</span>
        </div>
        <div class="card-driver" title="${esc(topDriver)}">
          ⚡ ${esc(topDriver)}
        </div>
      </div>
    `;
  }).join('');
}

async function openFileDetail(filePath){
  const fileName = filePath.split('/').pop();
  try {
    const res = await fetch(`/api/risk-scores/file/${encodeURIComponent(filePath)}`);
    const data = await res.json();
    const item = data.file;
    const bugs = data.bugs || [];

    document.getElementById('modalFileName').textContent = fileName;
    document.getElementById('modalFilePath').textContent = item.path;

    const badge = document.getElementById('modalFileScoreBadge');
    badge.className = `score-badge ${item.color}`;
    badge.textContent = `${item.score} / 100 (${item.color.toUpperCase()})`;

    // Dependency metrics
    document.getElementById('modalUsedBy').textContent = `${item.used_by} files`;
    document.getElementById('modalUsedChain').textContent = `${item.used_by_chain} files`;
    document.getElementById('modalImpactPct').textContent = `${Math.round(item.impact * 100)}%`;

    // Bug metrics
    document.getElementById('modalBugCount').textContent = `${item.bugs} bugs`;
    document.getElementById('modalCommitCount').textContent = `${item.commits} commits`;
    document.getElementById('modalBranches').textContent = `${item.branches} branches`;
    document.getElementById('modalKnowledge').textContent = `${Math.round((item.knowledge||0) * 100)}%`;

    // Top drivers
    const driversBox = document.getElementById('modalTopDrivers');
    driversBox.innerHTML = (item.top_factors || []).map(f => `<span class="metric-tag" style="color:var(--text);border-color:var(--accent);">⚡ ${esc(f)}</span>`).join('') || '<span style="color:var(--muted);font-size:12px">None</span>';

    // Bugs list
    document.getElementById('modalBugsTotalLabel').textContent = `${bugs.length} recorded fixes`;
    const bugsBox = document.getElementById('modalBugsList');
    if(!bugs.length){
      bugsBox.innerHTML = '<div style="color:var(--muted);font-size:12px;padding:10px 0;">No direct bug fixes recorded for this file.</div>';
    } else {
      bugsBox.innerHTML = bugs.map(b => `
        <div class="bug-item">
          <div class="bug-item-top">
            <span class="bug-id-pill">${esc(b.id)}</span>
            <span style="font-size:11px;color:var(--muted);">${esc(b.date)} · ${esc(b.bug_type)}</span>
          </div>
          <div class="bug-summary-txt">${esc(b.summary || b.title)}</div>
        </div>
      `).join('');
    }

    // Ask Agent button
    const askBtn = document.getElementById('askAgentAboutFileBtn');
    askBtn.onclick = () => {
      closeFileModal();
      switchView('chat');
      ask(`Why does ${fileName} have a risk score of ${item.score} and what are its main dependencies and bugs?`);
    };

    fileModal.style.display = 'grid';
  } catch(e) {
    console.error("Could not load file detail:", e);
  }
}

function closeFileModal(){
  fileModal.style.display = 'none';
}

// ----------------------------------------------------------------------------
// Chat UI & Agent Logic
// ----------------------------------------------------------------------------
function showHero(){
  const name = currentUser ? currentUser.full_name : "Developer";
  const role = currentUser ? currentUser.role : "Gson Contributor";
  log.innerHTML = `
    <div class="hero">
      <div class="hero-badge">Logged In as ${esc(name)} · ${esc(role)}</div>
      <h2>What would you like to explore in Gson?</h2>
      <p>The codebase agent knows your role (${esc(role)}) and grounds answers in commits, code, docs, and chats.</p>
      <div class="chips" id="chips"></div>
    </div>`;
  const box = document.getElementById('chips');
  SUGGESTIONS.forEach(s => {
    const b = document.createElement('button');
    b.className = 'chip';
    b.textContent = s;
    b.onclick = () => ask(s);
    box.appendChild(b);
  });
}

function addUser(text){
  const d = document.createElement('div');
  d.className = 'msg user';
  d.innerHTML = '<div class="bubble" dir="auto"></div>';
  d.firstChild.textContent = text;
  log.appendChild(d);
  scrollDown();
}

function addBot(){
  const d = document.createElement('div');
  d.className = 'msg bot';
  d.innerHTML = `
    <div class="avatar">K</div>
    <div class="body">
      <details class="trace" open>
        <summary><span class="spin"></span><span class="label">Thinking...</span></summary>
        <div class="steps"></div>
      </details>
      <div class="text"></div>
      <div class="sources"></div>
      <div class="meta"></div>
    </div>`;
  log.appendChild(d);
  scrollDown();
  return {
    root: d, trace: d.querySelector('.trace'), label: d.querySelector('.label'),
    spin: d.querySelector('.spin'), steps: d.querySelector('.steps'),
    text: d.querySelector('.text'), sources: d.querySelector('.sources'), meta: d.querySelector('.meta')
  };
}

function typeOut(el, text, done){
  const tokens = (text||'').split(/(\s+)/);
  const perTick = Math.max(2, Math.ceil(tokens.length / 50));
  let i = 0;
  const tick = () => {
    i = Math.min(tokens.length, i + perTick);
    el.innerHTML = md(tokens.slice(0, i).join(''));
    scrollDown();
    if(i < tokens.length) setTimeout(tick, 16); else done && done();
  };
  tick();
}

function srcClass(s){
  return s.startsWith('BUG') ? 'bug' : s.startsWith('T-') ? 'chat' : s.startsWith('DOC') ? 'doc' : '';
}

async function ask(question){
  question = (question||'').trim();
  if(!question || busy) return;
  if(!currentUser){
    showAuthModal();
    return;
  }
  if(currentView !== 'chat') switchView('chat');

  busy = true; sendBtn.disabled = true;
  if(document.querySelector('.hero')) log.innerHTML = '';
  addUser(question);
  q.value = ''; q.style.height = 'auto';

  const bot = addBot();
  let toolCount = 0, last = null;

  const finishTrace = (secs) => {
    bot.spin.outerHTML = '<span class="done">&#10003;</span>';
    bot.label.textContent = toolCount ? `Used ${toolCount} tool${toolCount > 1 ? 's' : ''}` + (secs != null ? ` · ${secs}s` : '') : 'Done';
    bot.trace.open = false;
  };

  try {
    const historyPayload = chatHistory.slice(-10);
    const res = await fetch('/ask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        question,
        session_id: currentSessionId,
        history: historyPayload,
        use_cache: document.getElementById('cache').checked
      })
    });

    const reader = res.body.getReader(), dec = new TextDecoder();
    let buf = '';

    while(true){
      const {value, done} = await reader.read();
      if(done) break;
      buf += dec.decode(value, {stream: true});
      let idx;
      while((idx = buf.indexOf('\n\n')) >= 0){
        const chunk = buf.slice(0, idx);
        buf = buf.slice(idx + 2);
        if(!chunk.startsWith('data: ')) continue;
        const ev = JSON.parse(chunk.slice(6));

        if(ev.type === 'tool'){
          toolCount++;
          last = document.createElement('div');
          last.className = 'step';
          last.innerHTML = `<b>${esc(TOOL_LABELS[ev.tool] || ev.tool)}</b> <span style="color:var(--muted)">${esc(ev.arg)}</span><div class="res"></div>`;
          bot.steps.appendChild(last);
          bot.label.textContent = (TOOL_LABELS[ev.tool] || ev.tool) + '...';
        } else if(ev.type === 'result' && last){
          last.querySelector('.res').textContent = ev.text;
        } else if(ev.type === 'note'){
          const n = document.createElement('div');
          n.className = 'step note';
          n.textContent = ev.text;
          bot.steps.appendChild(n);
        } else if(ev.type === 'answering'){
          bot.label.textContent = 'Writing the answer...';
        } else if(ev.type === 'error'){
          finishTrace();
          bot.text.innerHTML = `<p class="err" dir="auto">${esc(ev.text)}</p>`;
        } else if(ev.type === 'final'){
          finishTrace(ev.cached ? null : ev.seconds);
          chatHistory.push({role: 'user', content: question});
          chatHistory.push({role: 'assistant', content: ev.answer});

          typeOut(bot.text, ev.answer, () => {
            (ev.sources || []).forEach(s => {
              const c = document.createElement('span');
              c.className = 'src ' + srcClass(s);
              c.textContent = s;
              bot.sources.appendChild(c);
            });
            const bits = [];
            bits.push(ev.cached ? 'instant (cached)' : ev.seconds + ' s');
            if(ev.calls != null) bits.push(ev.calls + ' API call' + (ev.calls === 1 ? '' : 's'));
            bot.meta.textContent = bits.join(' · ');
            scrollDown();
          });
          loadSessions();
        }
        scrollDown();
      }
    }
  } catch(e) {
    finishTrace();
    bot.text.innerHTML = `<p class="err">Connection problem: ${esc(String(e))}</p>`;
  }
  busy = false;
  sendBtn.disabled = false;
  q.focus();
}

// ----------------------------------------------------------------------------
// Auth & Sessions Management
// ----------------------------------------------------------------------------
async function checkAuth(){
  try {
    const res = await fetch('/api/me');
    const data = await res.json();
    if(data.authenticated){
      setCurrentUser(data.user);
      loadSessions();
    } else {
      showAuthModal();
    }
  } catch(e) {
    showAuthModal();
  }
}

function setCurrentUser(user){
  currentUser = user;
  authModal.style.display = 'none';
  userBadge.style.display = 'flex';
  logoutBtn.style.display = 'inline-flex';
  loginBtn.style.display = 'none';

  document.getElementById('userName').textContent = user.full_name;
  document.getElementById('userRole').textContent = user.role;
  const initials = user.full_name.split(' ').map(w=>w[0]).join('').slice(0, 2).toUpperCase();
  const av = document.getElementById('userAvatar');
  av.textContent = initials;
  av.style.background = user.avatar_color || 'var(--accent)';

  showHero();
}

function showAuthModal(){
  authModal.style.display = 'grid';
  userBadge.style.display = 'none';
  logoutBtn.style.display = 'none';
  loginBtn.style.display = 'inline-flex';
  modalErr.style.display = 'none';
}

function quickFill(u, p){
  document.getElementById('loginUser').value = u;
  document.getElementById('loginPass').value = p;
  doLogin();
}

async function doLogin(){
  const username = document.getElementById('loginUser').value.trim();
  const password = document.getElementById('loginPass').value.trim();
  modalErr.style.display = 'none';
  try {
    const res = await fetch('/api/login', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ username, password })
    });
    const data = await res.json();
    if(!res.ok){
      modalErr.textContent = data.detail || "Login failed";
      modalErr.style.display = 'block';
      return;
    }
    setCurrentUser(data.user);
    loadSessions();
  } catch(e) {
    modalErr.textContent = "Error connecting to server";
    modalErr.style.display = 'block';
  }
}

async function doRegister(){
  const full_name = document.getElementById('regFullName').value.trim();
  const username = document.getElementById('regUser').value.trim();
  const role = document.getElementById('regRole').value;
  const password = document.getElementById('regPass').value.trim();
  modalErr.style.display = 'none';
  try {
    const res = await fetch('/api/register', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ full_name, username, role, password })
    });
    const data = await res.json();
    if(!res.ok){
      modalErr.textContent = data.detail || "Registration failed";
      modalErr.style.display = 'block';
      return;
    }
    setCurrentUser(data.user);
    loadSessions();
  } catch(e) {
    modalErr.textContent = "Error connecting to server";
    modalErr.style.display = 'block';
  }
}

async function doLogout(){
  await fetch('/api/logout', { method: 'POST' });
  currentUser = null;
  currentSessionId = null;
  chatHistory = [];
  log.innerHTML = '';
  showAuthModal();
}

async function loadSessions(){
  if(!currentUser) return;
  try {
    const res = await fetch('/api/sessions');
    const data = await res.json();
    sessionList.innerHTML = '';
    (data.sessions || []).forEach(s => {
      const el = document.createElement('div');
      el.className = 'session-item' + (s.id === currentSessionId ? ' active' : '');
      el.innerHTML = `
        <span class="txt" title="${esc(s.title)}">${esc(s.title || 'Conversation')}</span>
        <span class="del" title="Delete">✕</span>`;
      el.onclick = (e) => {
        if(e.target.classList.contains('del')){
          e.stopPropagation();
          deleteSession(s.id);
        } else {
          switchSession(s.id);
        }
      };
      sessionList.appendChild(el);
    });
  } catch(e){}
}

async function switchSession(sid){
  currentSessionId = sid;
  chatHistory = [];
  log.innerHTML = '';
  switchView('chat');
  loadSessions();
  try {
    const res = await fetch(`/api/sessions/${sid}`);
    const data = await res.json();
    if(!data.messages || !data.messages.length){
      showHero();
      return;
    }
    data.messages.forEach(m => {
      chatHistory.push({ role: m.role, content: m.content });
      if(m.role === 'user'){
        addUser(m.content);
      } else {
        const bot = addBot();
        bot.trace.style.display = 'none';
        bot.text.innerHTML = md(m.content);
        (m.sources || []).forEach(s => {
          const c = document.createElement('span');
          c.className = 'src ' + srcClass(s);
          c.textContent = s;
          bot.sources.appendChild(c);
        });
      }
    });
    scrollDown();
  } catch(e){}
}

async function deleteSession(sid){
  await fetch(`/api/sessions/${sid}`, { method: 'DELETE' });
  if(currentSessionId === sid){
    currentSessionId = null;
    chatHistory = [];
    showHero();
  }
  loadSessions();
}

async function startNewChat(){
  currentSessionId = null;
  chatHistory = [];
  switchView('chat');
  showHero();
  loadSessions();
}

// Tabs
document.getElementById('tabSignIn').onclick = () => {
  document.getElementById('tabSignIn').classList.add('active');
  document.getElementById('tabRegister').classList.remove('active');
  document.getElementById('signInForm').style.display = 'block';
  document.getElementById('registerForm').style.display = 'none';
  document.getElementById('quickAccountsSection').style.display = 'block';
};
document.getElementById('tabRegister').onclick = () => {
  document.getElementById('tabRegister').classList.add('active');
  document.getElementById('tabSignIn').classList.remove('active');
  document.getElementById('signInForm').style.display = 'none';
  document.getElementById('registerForm').style.display = 'block';
  document.getElementById('quickAccountsSection').style.display = 'none';
};

// Controls
logoutBtn.onclick = doLogout;
loginBtn.onclick = showAuthModal;
document.getElementById('newChatBtn').onclick = startNewChat;
document.getElementById('toggleSidebar').onclick = () => sidebar.classList.toggle('hidden');
document.getElementById('closeSidebar').onclick = () => sidebar.classList.add('hidden');

sendBtn.onclick = () => ask(q.value);
q.addEventListener('keydown', e => { if(e.key === 'Enter' && !e.shiftKey){ e.preventDefault(); ask(q.value); }});
q.addEventListener('input', () => { q.style.height = 'auto'; q.style.height = Math.min(q.scrollHeight, 150) + 'px'; });

// Initialize
checkAuth();
</script>
</body>
</html>
"""

if __name__ == "__main__":
    import uvicorn

    uvicorn.run(
        "ui_server:app",
        host="127.0.0.1",
        port=8000,
        reload=True,
        reload_dirs=[str(APP_DIR)],
        app_dir=str(APP_DIR),
    )

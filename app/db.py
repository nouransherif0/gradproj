"""
Database module for the AI-Powered Software Maintenance Knowledge Extractor.
Stores developers (users, roles), chat sessions, chat messages, and developer notes in SQLite.
"""

import hashlib
import json
import os
import secrets
import sqlite3
import uuid
from datetime import datetime
from pathlib import Path

DB_PATH = Path(__file__).resolve().parent.parent / "data" / "knowledge_extractor.db"


def get_db():
    """Get a database connection with dict-like row access."""
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(DB_PATH), check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON;")
    conn.execute("PRAGMA journal_mode = WAL;")
    return conn


def hash_password(password: str, salt: str = None) -> tuple[str, str]:
    """Hash password using PBKDF2-HMAC-SHA256."""
    if not salt:
        salt = secrets.token_hex(16)
    pw_hash = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 100000
    ).hex()
    return pw_hash, salt


def verify_password(password: str, pw_hash: str, salt: str) -> bool:
    """Verify password against hash and salt."""
    computed, _ = hash_password(password, salt)
    return secrets.compare_digest(computed, pw_hash)


def init_db():
    """Create all tables and seed default developers if empty."""
    conn = get_db()
    with conn:
        conn.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            username TEXT UNIQUE NOT NULL,
            password_hash TEXT NOT NULL,
            salt TEXT NOT NULL,
            full_name TEXT NOT NULL,
            email TEXT,
            role TEXT NOT NULL,
            avatar_color TEXT DEFAULT '#7c9cff',
            bio TEXT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chat_sessions (
            id TEXT PRIMARY KEY,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            title TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS chat_messages (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            session_id TEXT NOT NULL REFERENCES chat_sessions(id) ON DELETE CASCADE,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            role TEXT NOT NULL,
            content TEXT NOT NULL,
            sources_json TEXT DEFAULT '[]',
            tool_calls_json TEXT DEFAULT '[]',
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );

        CREATE TABLE IF NOT EXISTS developer_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER NOT NULL REFERENCES users(id) ON DELETE CASCADE,
            file_path TEXT NOT NULL,
            note TEXT NOT NULL,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        );
        """)

        # Check if users already exist
        cur = conn.cursor()
        cur.execute("SELECT COUNT(*) as cnt FROM users")
        if cur.fetchone()["cnt"] == 0:
            default_users = [
                (
                    "nouran",
                    "admin123",
                    "Nouran Sherif",
                    "Tech Lead & Maintainer",
                    "nouran@example.com",
                    "#a78bfa",
                    "Project Lead, Gson Maintainer & Core Architect",
                ),
                (
                    "junior_dev",
                    "password123",
                    "Salma Ahmed",
                    "Junior Developer",
                    "salma@example.com",
                    "#3ecf8e",
                    "New team member exploring Gson codebase",
                ),
                (
                    "senior_dev",
                    "password123",
                    "Omar Khaled",
                    "Senior Backend Developer",
                    "omar@example.com",
                    "#7c9cff",
                    "Specializes in TypeAdapters and Serialization",
                ),
                (
                    "qa_engineer",
                    "password123",
                    "Karim Mostafa",
                    "QA & Reliability Engineer",
                    "karim@example.com",
                    "#f5b04c",
                    "Focuses on bug reproduction, regressions, and crash analysis",
                ),
            ]
            for uname, pwd, fname, role, email, color, bio in default_users:
                p_hash, salt = hash_password(pwd)
                conn.execute(
                    """
                    INSERT INTO users (username, password_hash, salt, full_name, email, role, avatar_color, bio)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (uname, p_hash, salt, fname, email, role, color, bio),
                )
            print("Database initialized with default developer accounts.")
    conn.close()


# ----------------------------------------------------------------------------
# User operations
# ----------------------------------------------------------------------------
def create_user(username, password, full_name, role, email=None, bio=None, avatar_color=None):
    """Create a new developer account."""
    if not avatar_color:
        colors = ["#7c9cff", "#a78bfa", "#3ecf8e", "#f5b04c", "#f43f5e", "#06b6d4"]
        avatar_color = colors[len(username) % len(colors)]
    pw_hash, salt = hash_password(password)
    conn = get_db()
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO users (username, password_hash, salt, full_name, email, role, avatar_color, bio)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (username.strip().lower(), pw_hash, salt, full_name.strip(), email, role, avatar_color, bio),
            )
            user_id = cur.lastrowid
        return get_user_by_id(user_id)
    except sqlite3.IntegrityError:
        return None
    finally:
        conn.close()


def authenticate_user(username, password):
    """Verify username and password, return user dict if correct."""
    conn = get_db()
    try:
        user = conn.execute(
            "SELECT * FROM users WHERE username = ?", (username.strip().lower(),)
        ).fetchone()
        if not user:
            return None
        if verify_password(password, user["password_hash"], user["salt"]):
            return dict(user)
        return None
    finally:
        conn.close()


def get_user_by_id(user_id):
    """Fetch user by id."""
    conn = get_db()
    try:
        user = conn.execute("SELECT id, username, full_name, email, role, avatar_color, bio, created_at FROM users WHERE id = ?", (user_id,)).fetchone()
        return dict(user) if user else None
    finally:
        conn.close()


def get_user_by_username(username):
    """Fetch user by username."""
    conn = get_db()
    try:
        user = conn.execute("SELECT id, username, full_name, email, role, avatar_color, bio, created_at FROM users WHERE username = ?", (username.strip().lower(),)).fetchone()
        return dict(user) if user else None
    finally:
        conn.close()


def list_users():
    """List all registered developers."""
    conn = get_db()
    try:
        rows = conn.execute("SELECT id, username, full_name, email, role, avatar_color, bio, created_at FROM users ORDER BY id ASC").fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


# ----------------------------------------------------------------------------
# Session and message operations
# ----------------------------------------------------------------------------
def create_session(user_id, title="New Conversation"):
    """Create a new chat session for a user."""
    session_id = f"s_{uuid.uuid4().hex[:12]}"
    conn = get_db()
    try:
        with conn:
            conn.execute(
                """
                INSERT INTO chat_sessions (id, user_id, title)
                VALUES (?, ?, ?)
                """,
                (session_id, user_id, title),
            )
        return session_id
    finally:
        conn.close()


def get_or_create_session(user_id, session_id=None, title="New Conversation"):
    """Ensure session exists or create one."""
    conn = get_db()
    try:
        if session_id:
            row = conn.execute(
                "SELECT * FROM chat_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            ).fetchone()
            if row:
                return dict(row)
        new_id = create_session(user_id, title)
        row = conn.execute("SELECT * FROM chat_sessions WHERE id = ?", (new_id,)).fetchone()
        return dict(row)
    finally:
        conn.close()


def list_sessions_for_user(user_id):
    """List all chat sessions for a specific user, sorted by last update."""
    conn = get_db()
    try:
        rows = conn.execute(
            """
            SELECT s.*, COUNT(m.id) as message_count
            FROM chat_sessions s
            LEFT JOIN chat_messages m ON s.id = m.session_id
            WHERE s.user_id = ?
            GROUP BY s.id
            ORDER BY s.updated_at DESC
            """,
            (user_id,),
        ).fetchall()
        return [dict(r) for r in rows]
    finally:
        conn.close()


def update_session_title(session_id, user_id, title):
    """Update title and timestamp of a session."""
    conn = get_db()
    try:
        with conn:
            conn.execute(
                """
                UPDATE chat_sessions
                SET title = ?, updated_at = CURRENT_TIMESTAMP
                WHERE id = ? AND user_id = ?
                """,
                (title, session_id, user_id),
            )
    finally:
        conn.close()


def delete_session(session_id, user_id):
    """Delete a chat session and all its messages."""
    conn = get_db()
    try:
        with conn:
            conn.execute(
                "DELETE FROM chat_sessions WHERE id = ? AND user_id = ?",
                (session_id, user_id),
            )
        return True
    finally:
        conn.close()


def add_message(session_id, user_id, role, content, sources=None, tool_calls=None):
    """Save a message (from user or agent) to the database."""
    conn = get_db()
    sources_json = json.dumps(sources or [], ensure_ascii=False)
    tool_calls_json = json.dumps(tool_calls or [], ensure_ascii=False)
    try:
        with conn:
            cur = conn.execute(
                """
                INSERT INTO chat_messages (session_id, user_id, role, content, sources_json, tool_calls_json)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (session_id, user_id, role, content, sources_json, tool_calls_json),
            )
            # Update session's updated_at timestamp
            conn.execute(
                "UPDATE chat_sessions SET updated_at = CURRENT_TIMESTAMP WHERE id = ?",
                (session_id,),
            )
            return cur.lastrowid
    finally:
        conn.close()


def get_session_messages(session_id, user_id=None):
    """Get all messages for a session in chronological order."""
    conn = get_db()
    try:
        if user_id:
            query = """
            SELECT m.* FROM chat_messages m
            JOIN chat_sessions s ON m.session_id = s.id
            WHERE m.session_id = ? AND s.user_id = ?
            ORDER BY m.id ASC
            """
            rows = conn.execute(query, (session_id, user_id)).fetchall()
        else:
            query = "SELECT * FROM chat_messages WHERE session_id = ? ORDER BY id ASC"
            rows = conn.execute(query, (session_id,)).fetchall()

        messages = []
        for r in rows:
            d = dict(r)
            try:
                d["sources"] = json.loads(d["sources_json"]) if d.get("sources_json") else []
            except Exception:
                d["sources"] = []
            try:
                d["tool_calls"] = json.loads(d["tool_calls_json"]) if d.get("tool_calls_json") else []
            except Exception:
                d["tool_calls"] = []
            messages.append(d)
        return messages
    finally:
        conn.close()


if __name__ == "__main__":
    init_db()
    print("Users currently in DB:")
    for u in list_users():
        print(f"  [{u['id']}] {u['username']} ({u['full_name']}) - Role: {u['role']}")

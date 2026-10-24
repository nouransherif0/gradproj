"""
Export all Database Tables and Domain Data for MongoDB (and Desktop folder).
Creates clean, mongoimport-ready JSON files, schema definitions, and a setup guide.
"""

import json
import shutil
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DB_PATH = ROOT / "data" / "knowledge_extractor.db"
PROCESSED = ROOT / "data" / "processed"

WORKSPACE_EXPORT = ROOT / "db_export"
DESKTOP_EXPORT = Path.home() / "Desktop" / "knowledge_extractor_db"


def export_all():
    WORKSPACE_EXPORT.mkdir(parents=True, exist_ok=True)
    DESKTOP_EXPORT.mkdir(parents=True, exist_ok=True)

    # -------------------------------------------------------------------------
    # 1. Export SQLite Tables (Users, Sessions, Messages, Notes)
    # -------------------------------------------------------------------------
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row

    # Users
    users = [dict(r) for r in conn.execute("SELECT * FROM users ORDER BY id ASC").fetchall()]
    for u in users:
        u["_id"] = u["id"]
        # keep id and _id for mongo compatibility

    # Sessions
    sessions = [dict(r) for r in conn.execute("SELECT * FROM chat_sessions ORDER BY created_at ASC").fetchall()]
    for s in sessions:
        s["_id"] = s["id"]

    # Messages
    raw_msgs = conn.execute("SELECT * FROM chat_messages ORDER BY id ASC").fetchall()
    messages = []
    for m in raw_msgs:
        d = dict(m)
        d["_id"] = d["id"]
        try:
            d["sources"] = json.loads(d["sources_json"]) if d.get("sources_json") else []
        except Exception:
            d["sources"] = []
        try:
            d["tool_calls"] = json.loads(d["tool_calls_json"]) if d.get("tool_calls_json") else []
        except Exception:
            d["tool_calls"] = []
        d.pop("sources_json", None)
        d.pop("tool_calls_json", None)
        messages.append(d)

    # Notes
    notes = [dict(r) for r in conn.execute("SELECT * FROM developer_notes ORDER BY id ASC").fetchall()]
    for n in notes:
        n["_id"] = n["id"]

    conn.close()

    # -------------------------------------------------------------------------
    # 2. Export Domain Knowledge Tables (Bugs, File Risks, Slack Threads)
    # -------------------------------------------------------------------------
    # Classified Bugs
    bugs = []
    if (PROCESSED / "bugs_classified.json").exists():
        raw_bugs = json.loads((PROCESSED / "bugs_classified.json").read_text(encoding="utf-8"))
        for b in raw_bugs:
            b_copy = dict(b)
            b_copy["_id"] = b["id"]
            bugs.append(b_copy)

    # Risk Scores
    risk_scores = []
    if (PROCESSED / "risk_scores.json").exists():
        raw_risks = json.loads((PROCESSED / "risk_scores.json").read_text(encoding="utf-8"))
        for r in raw_risks:
            r_copy = dict(r)
            r_copy["_id"] = r["path"]
            risk_scores.append(r_copy)

    # Slack Threads (grouped by thread with truth links)
    threads = []
    if (PROCESSED / "slack_messages.json").exists() and (PROCESSED / "slack_truth.json").exists():
        msgs = json.loads((PROCESSED / "slack_messages.json").read_text(encoding="utf-8"))
        truths = json.loads((PROCESSED / "slack_truth.json").read_text(encoding="utf-8"))
        truth_map = {t["thread_id"]: t for t in truths}

        grouped = {}
        for m in msgs:
            tid = m["thread_id"]
            grouped.setdefault(tid, []).append({
                "user": m.get("user"),
                "text": m.get("text"),
                "ts": m.get("ts"),
                "synthetic": m.get("synthetic", True),
                "channel": m.get("channel")
            })

        for tid, tmsgs in grouped.items():
            t_meta = truth_map.get(tid, {})
            threads.append({
                "_id": tid,
                "thread_id": tid,
                "bug_id": t_meta.get("bug_id"),
                "files": t_meta.get("files", []),
                "link_style": t_meta.get("link_style"),
                "pr_number": t_meta.get("pr_number"),
                "issue_numbers": t_meta.get("issue_numbers"),
                "message_count": len(tmsgs),
                "messages": tmsgs
            })

    # -------------------------------------------------------------------------
    # 3. Write JSON collections to files
    # -------------------------------------------------------------------------
    collections = {
        "users.json": users,
        "chat_sessions.json": sessions,
        "chat_messages.json": messages,
        "developer_notes.json": notes,
        "bugs.json": bugs,
        "file_risks.json": risk_scores,
        "slack_threads.json": threads,
    }

    for name, data in collections.items():
        content = json.dumps(data, indent=2, ensure_ascii=False)
        (WORKSPACE_EXPORT / name).write_text(content, encoding="utf-8")
        (DESKTOP_EXPORT / name).write_text(content, encoding="utf-8")

    # -------------------------------------------------------------------------
    # 4. SQL DDL Schema (schema.sql)
    # -------------------------------------------------------------------------
    sql_ddl = """-- =====================================================================
-- Software Maintenance Knowledge Extractor - Relational SQL Schema
-- Compatible with MySQL / PostgreSQL / SQLite
-- =====================================================================

-- 1. Users / Developers
CREATE TABLE users (
    id VARCHAR(64) PRIMARY KEY,
    username VARCHAR(64) UNIQUE NOT NULL,
    password_hash VARCHAR(128) NOT NULL,
    salt VARCHAR(64) NOT NULL,
    full_name VARCHAR(128) NOT NULL,
    email VARCHAR(128),
    role VARCHAR(64) NOT NULL, -- 'Tech Lead & Maintainer', 'Senior Developer', 'Junior Developer', 'QA Engineer'
    avatar_color VARCHAR(16) DEFAULT '#7c9cff',
    bio TEXT,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
);

-- 2. Chat Sessions
CREATE TABLE chat_sessions (
    id VARCHAR(64) PRIMARY KEY,
    user_id VARCHAR(64) NOT NULL,
    title VARCHAR(255) NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 3. Chat Messages (Persisted Conversations)
CREATE TABLE chat_messages (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    session_id VARCHAR(64) NOT NULL,
    user_id VARCHAR(64) NOT NULL,
    role VARCHAR(16) NOT NULL, -- 'user' or 'assistant'
    content TEXT NOT NULL,
    sources_json TEXT, -- JSON array of cited IDs (e.g. BUG-012, T-012)
    tool_calls_json TEXT, -- JSON array of tool calls
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (session_id) REFERENCES chat_sessions(id) ON DELETE CASCADE,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 4. Developer Notes & File Bookmarks
CREATE TABLE developer_notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    user_id VARCHAR(64) NOT NULL,
    file_path VARCHAR(255) NOT NULL,
    note TEXT NOT NULL,
    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (user_id) REFERENCES users(id) ON DELETE CASCADE
);

-- 5. Domain: Classified Bugs
CREATE TABLE bugs (
    id VARCHAR(32) PRIMARY KEY, -- 'BUG-001'
    commit_hash VARCHAR(64) NOT NULL,
    date DATE NOT NULL,
    author VARCHAR(128),
    title TEXT NOT NULL,
    description TEXT,
    pr_number INTEGER,
    bug_type VARCHAR(32) NOT NULL, -- 'crash', 'wrong_output', 'parsing', 'concurrency', 'security', 'other'
    summary TEXT
);

-- 6. Domain: File Risk Scores
CREATE TABLE file_risks (
    path VARCHAR(255) PRIMARY KEY,
    score INTEGER NOT NULL, -- 0 to 100
    color VARCHAR(16) NOT NULL, -- 'red', 'orange', 'yellow', 'green'
    likelihood FLOAT NOT NULL,
    impact FLOAT NOT NULL,
    bugs INTEGER DEFAULT 0,
    commits INTEGER DEFAULT 0,
    chats INTEGER DEFAULT 0,
    branches INTEGER DEFAULT 0,
    knowledge FLOAT DEFAULT 0.0,
    used_by INTEGER DEFAULT 0,
    used_by_chain INTEGER DEFAULT 0
);
"""
    (WORKSPACE_EXPORT / "schema.sql").write_text(sql_ddl, encoding="utf-8")
    (DESKTOP_EXPORT / "schema.sql").write_text(sql_ddl, encoding="utf-8")

    # -------------------------------------------------------------------------
    # 5. Mongoose Schemas for MongoDB (mongoose_schemas.js)
    # -------------------------------------------------------------------------
    mongoose_js = """/**
 * Mongoose Schemas for MongoDB
 * Project: AI-Powered Software Maintenance Knowledge Extractor
 * Ready for Node.js / Express / Nest.js / MongoDB
 */

const mongoose = require('mongoose');
const { Schema } = mongoose;

// 1. User / Developer Schema
const UserSchema = new Schema({
  username: { type: String, required: true, unique: true, index: true },
  password_hash: { type: String, required: true },
  salt: { type: String, required: true },
  full_name: { type: String, required: true },
  email: { type: String },
  role: {
    type: String,
    enum: ['Tech Lead & Maintainer', 'Senior Developer', 'Junior Developer', 'QA & Reliability Engineer', 'Security Engineer'],
    required: true,
    default: 'Junior Developer'
  },
  avatar_color: { type: String, default: '#7c9cff' },
  bio: { type: String },
  created_at: { type: Date, default: Date.now }
});

// 2. Chat Session Schema
const ChatSessionSchema = new Schema({
  _id: { type: String, required: true }, // e.g. 's_881d789e'
  user_id: { type: Schema.Types.Mixed, ref: 'User', required: true, index: true },
  title: { type: String, default: 'New Conversation' },
  created_at: { type: Date, default: Date.now },
  updated_at: { type: Date, default: Date.now }
});

// 3. Chat Message Schema
const ChatMessageSchema = new Schema({
  session_id: { type: String, ref: 'ChatSession', required: true, index: true },
  user_id: { type: Schema.Types.Mixed, ref: 'User', required: true, index: true },
  role: { type: String, enum: ['user', 'assistant'], required: true },
  content: { type: String, required: true },
  sources: [{ type: String }], // e.g. ['BUG-018', 'T-139', 'CODE-TypeAdapters-L51']
  tool_calls: [{
    round: Number,
    tool: String,
    arg: String
  }],
  created_at: { type: Date, default: Date.now }
});

// 4. Classified Bug Schema
const BugSchema = new Schema({
  _id: { type: String, required: true }, // 'BUG-001'
  id: { type: String, required: true, unique: true },
  commit: { type: String, required: true },
  date: { type: String },
  author: { type: String },
  title: { type: String, required: true },
  description: { type: String },
  pr_number: { type: Number },
  issue_numbers: [{ type: Number }],
  files: [{ type: String }],
  bug_type: {
    type: String,
    enum: ['crash', 'wrong_output', 'parsing', 'concurrency', 'security', 'other'],
    required: true
  },
  summary: { type: String }
});

// 5. File Risk Score Schema
const FileRiskSchema = new Schema({
  _id: { type: String, required: true }, // file path
  path: { type: String, required: true, unique: true },
  score: { type: Number, required: true, min: 0, max: 100 },
  color: { type: String, enum: ['red', 'orange', 'yellow', 'green'], required: true },
  likelihood: { type: Number, required: true },
  impact: { type: Number, required: true },
  bugs: { type: Number, default: 0 },
  commits: { type: Number, default: 0 },
  chats: { type: Number, default: 0 },
  branches: { type: Number, default: 0 },
  knowledge: { type: Number, default: 0 },
  used_by: { type: Number, default: 0 },
  used_by_chain: { type: Number, default: 0 },
  top_factors: [{ type: String }]
});

// 6. Slack Thread Schema
const SlackThreadSchema = new Schema({
  _id: { type: String, required: true }, // 'T-001'
  thread_id: { type: String, required: true, unique: true },
  bug_id: { type: String, ref: 'Bug' },
  files: [{ type: String }],
  link_style: { type: String, enum: ['pr', 'issue', 'file', 'vague'] },
  pr_number: { type: Number },
  issue_numbers: [{ type: Number }],
  message_count: { type: Number },
  messages: [{
    user: { type: String },
    text: { type: String },
    ts: { type: String },
    synthetic: { type: Boolean, default: true },
    channel: { type: String }
  }]
});

module.exports = {
  User: mongoose.model('User', UserSchema),
  ChatSession: mongoose.model('ChatSession', ChatSessionSchema),
  ChatMessage: mongoose.model('ChatMessage', ChatMessageSchema),
  Bug: mongoose.model('Bug', BugSchema),
  FileRisk: mongoose.model('FileRisk', FileRiskSchema),
  SlackThread: mongoose.model('SlackThread', SlackThreadSchema)
};
"""
    (WORKSPACE_EXPORT / "mongoose_schemas.js").write_text(mongoose_js, encoding="utf-8")
    (DESKTOP_EXPORT / "mongoose_schemas.js").write_text(mongoose_js, encoding="utf-8")

    # -------------------------------------------------------------------------
    # 6. Comprehensive README for MongoDB & Importing
    # -------------------------------------------------------------------------
    readme = """# AI-Powered Software Maintenance Knowledge Extractor - Database Export

This folder contains the complete database collections ready to import into **MongoDB** (or any relational SQL database).

---

## 📁 Files Included

| File | MongoDB Collection | Description | Record Count |
| :--- | :--- | :--- | :--- |
| `users.json` | `users` | Developers, roles, password hashes, bios | 4 seeded developers |
| `chat_sessions.json` | `chat_sessions` | Chat conversation threads per user | All active sessions |
| `chat_messages.json` | `chat_messages` | Full persisted chat history, citations, tools | All saved messages |
| `bugs.json` | `bugs` | Classified bug fixes (type, root cause, summary) | 126 real bugs |
| `file_risks.json` | `file_risks` | Scored source files (likelihood, impact, color) | 115 files |
| `slack_threads.json` | `slack_threads` | Developer Slack discussions & linking styles | 126 threads (655 msgs) |
| `developer_notes.json` | `developer_notes` | File notes and bookmarks | Optional notes |
| `mongoose_schemas.js` | — | Ready-to-use Mongoose schemas for Node.js | All models |
| `schema.sql` | — | Relational SQL schema for MySQL / PostgreSQL | DDL tables |

---

## 🚀 How to Import into MongoDB

### Option A: Using `mongoimport` (Command Line)
If you have MongoDB installed locally or on MongoDB Atlas:

```bash
# Connect to your local MongoDB:
DB="knowledge_extractor"

mongoimport --db $DB --collection users --file users.json --jsonArray
mongoimport --db $DB --collection chat_sessions --file chat_sessions.json --jsonArray
mongoimport --db $DB --collection chat_messages --file chat_messages.json --jsonArray
mongoimport --db $DB --collection bugs --file bugs.json --jsonArray
mongoimport --db $DB --collection file_risks --file file_risks.json --jsonArray
mongoimport --db $DB --collection slack_threads --file slack_threads.json --jsonArray
```

### Option B: Using MongoDB Compass (GUI)
1. Open **MongoDB Compass**.
2. Connect to your cluster or local MongoDB instance (`mongodb://localhost:27017`).
3. Create a Database named `knowledge_extractor`.
4. Create the collections: `users`, `chat_sessions`, `chat_messages`, `bugs`, `file_risks`, `slack_threads`.
5. In each collection, click **"Add Data" -> "Import File"** and select the corresponding `.json` file!

### Option C: Using Python (`pymongo`)
```python
import json
from pymongo import MongoClient

client = MongoClient("mongodb://localhost:27017/")
db = client["knowledge_extractor"]

files = ["users", "chat_sessions", "chat_messages", "bugs", "file_risks", "slack_threads"]
for name in files:
    with open(f"{name}.json", "r", encoding="utf-8") as f:
        data = json.load(f)
        if data:
            db[name].insert_many(data)
            print(f"Imported {len(data)} documents into collection '{name}'")
```

---

## 👤 Seeded Developer Accounts

| Username | Password | Full Name | Role |
| :--- | :--- | :--- | :--- |
| `nouran` | `admin123` | Nouran Sherif | Tech Lead & Maintainer |
| `junior_dev` | `password123` | Salma Ahmed | Junior Developer |
| `senior_dev` | `password123` | Omar Khaled | Senior Backend Developer |
| `qa_engineer` | `password123` | Karim Mostafa | QA & Reliability Engineer |

---
Generated on October 2026 for Graduation Project Defense.
"""
    (WORKSPACE_EXPORT / "README.md").write_text(readme, encoding="utf-8")
    (DESKTOP_EXPORT / "README.md").write_text(readme, encoding="utf-8")

    print(f"Successfully exported all DB tables and domain data to:")
    print(f"  1. Desktop: {DESKTOP_EXPORT}")
    print(f"  2. Workspace: {WORKSPACE_EXPORT}")
    print(f"\nCollections exported:")
    for k, v in collections.items():
        print(f"  - {k} ({len(v)} records)")


if __name__ == "__main__":
    export_all()

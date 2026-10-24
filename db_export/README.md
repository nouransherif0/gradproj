# AI-Powered Software Maintenance Knowledge Extractor - Database Export

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

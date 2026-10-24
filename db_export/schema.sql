-- =====================================================================
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

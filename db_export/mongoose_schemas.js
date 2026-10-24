/**
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

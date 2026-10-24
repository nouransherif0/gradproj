"""The mock agent, now fed with the whole repository."""

import json
import re
from pathlib import Path

from rank_bm25 import BM25Okapi

import mock_agent as base

CORPUS = json.loads(Path("data/processed/corpus.json").read_text(encoding="utf-8"))


def tok(text):
    out = []
    for word in re.findall(r"[A-Za-z0-9]+", text):
        out.append(word.lower())
        parts = re.findall(r"[A-Z]?[a-z]+|[A-Z]+(?![a-z])|\d+", word)
        if len(parts) > 1:
            out.extend(p.lower() for p in parts)
    return out


def make_search(kind, top, limit):
    items = [c for c in CORPUS if c["kind"] == kind]
    index = BM25Okapi([tok(c["text"]) for c in items]) if items else None

    def search(query):
        if not index:
            return "NO RESULTS (nothing loaded)"
        scores = index.get_scores(tok(query))
        best = sorted(range(len(items)), key=lambda i: -scores[i])[:top]
        parts = []
        for i in best:
            if scores[i] <= 0:
                continue
            c = items[i]
            parts.append(f"{c['id']} | {c['source']}\n{c['show'][:limit]}")
        return "\n---\n".join(parts) or "NO RESULTS"

    return search


base.TOOLS.update({
    "search_commits": make_search("commit", 5, 300),
    "search_code": make_search("code", 3, 1200),
    "search_docs": make_search("doc", 3, 700),
})

base.SYSTEM += """
More tools are now available:
- search_commits(text): search ALL commit messages (full text and changed files) of the repository history. Best for 'why was this changed'.
- search_code(text): search the current source code. Best for 'what does this code do today'. Describe code in words; never write new or changed code.
- search_docs(text): search the README and other documentation.
Use search_commits for reasons and history, search_code for current behavior, search_bugs and count_bugs for classified bug fixes and counts.
Cite ids exactly as shown, for example BUG-005, C-1a2b3c4, CODE-JsonReader-L51, DOC-README-L1.
"""
base.SYSTEM += """
If the tools find partly relevant evidence (for example a commit title but no explanation), answer with what the evidence says and state clearly what is missing. Say 'I don't know based on the project history.' only when nothing relevant is found.
"""
base.SYSTEM += """
For any question that starts with 'why' or asks what the developers discussed, decided, or said, you MUST call search_chats at least once before answering, in addition to search_commits or search_bugs. Use the chat threads to explain the reasoning behind the change, and cite them (T-xxx). If the chats and the commits say different things, point it out.
"""
base.SYSTEM += """
For 'why' questions you must make BOTH a chat search and a history search (search_commits or search_bugs) before answering. Cite the real commit or bug AND the chat thread. When you use a chat thread, say it is a team discussion; if the commit message gives no reason, say so.
"""
base.MAX_ROUNDS = 5

# --- multilingual rules ---
base.SYSTEM += """
LANGUAGE RULES:
- The user may write in English, Arabic, Franco-Arabic (Arabic in Latin letters, like 'leh feeh bugs kteer'), or casual slang, with typos and no fixed format. Work out what they mean.
- The project data (bugs, commits, chats, code, docs) is ALL in English. So the INPUT of every tool call must ALWAYS be short English technical words, even when the question is in Arabic. Translate first. Examples: 'ملف' = file, 'مشاكل' = bugs, 'كراش' = crash, 'اكتر' or 'aktar' = most, so use count_bugs.
- Write the "text" of your final answer in the SAME language the user used. For an Arabic question answer in Arabic. If the user asks in Franco-Arabic or requests Franco-Arabic (e.g. 'jawbny franco', 'answer in franco'), answer in Franco-Arabic. Keep file names, class names and ids (BUG-xxx, T-xxx) in English.
- If you have no evidence, say so in the user's language. English: I don't know based on the project history. Arabic: لا أعرف بناءً على تاريخ المشروع. Franco: Ma3rafsh bna2an 3la tareekh el mashrou3.
- A casual or vague question is still a real question: search with your best interpretation. Say you don't know only if the tools return nothing relevant.
"""

if __name__ == "__main__":
    kinds = {}
    for c in CORPUS:
        kinds[c["kind"]] = kinds.get(c["kind"], 0) + 1
    print(f"Repository fed to the agent: {kinds}")
    base.main()

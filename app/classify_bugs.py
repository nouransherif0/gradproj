"""
Step 4: Ask the LLM which of the extracted commits are REAL bugs.

Input : data/processed/bug_fixes.json        (from Step 2)
Output: data/processed/bugs_classified.json  (real bugs only, with a summary)

Answers are cached in data/processed/classify_cache.json, so if the
script stops (rate limit, internet), just run it again: it continues.
"""

import json
import re
import time
from pathlib import Path

from llm import ask_llm

# ---------- Settings ----------
INPUT = Path("data/processed/bug_fixes.json")
CACHE = Path("data/processed/classify_cache.json")
OUTPUT = Path("data/processed/bugs_classified.json")
BATCH_SIZE = 25      # commits per LLM call
PAUSE_SECONDS = 5     # wait between calls

SYSTEM = (
    "You are a senior Java developer reviewing commits of the Gson library. "
    "Answer with JSON only, no extra text."
)

BUG_TYPES = "crash, wrong_output, parsing, security, concurrency, performance, other"


def build_prompt(batch):
    """Write the question for one batch of commits."""
    lines = []
    for bug in batch:
        file_names = [f.split("/")[-1] for f in bug["files"]]
        lines.append(
            f"ID: {bug['id']}\n"
            f"Title: {bug['title']}\n"
            f"Description: {bug['description'][:400]}\n"
            f"Files: {', '.join(file_names)}\n"
        )
    commits_text = "\n---\n".join(lines)

    return f"""For each commit below, decide if it fixes a REAL BUG.

is_real_bug = true only if the commit fixes wrong behavior: a crash, an
exception that should not happen, wrong output, or a security problem.
is_real_bug = false for refactoring, new features, cleanup, API changes,
build changes, or documentation.

For each commit return:
- id: the same ID as given
- is_real_bug: true or false
- bug_type: one of [{BUG_TYPES}]
- summary: ONE plain-English sentence: what was wrong and what the fix does

Return ONLY a JSON array, like:
[{{"id": "BUG-001", "is_real_bug": true, "bug_type": "parsing", "summary": "..."}}]

Commits:
{commits_text}"""


def parse_answer(text):
    """Turn the model's text into a Python list (ignores ``` fences)."""
    start = text.find("[")
    end = text.rfind("]")
    if start == -1 or end == -1:
        return []
    try:
        return json.loads(text[start:end + 1])
    except json.JSONDecodeError:
        return []


def load_cache():
    if CACHE.exists():
        return json.loads(CACHE.read_text(encoding="utf-8"))
    return {}


def main():
    bugs = json.loads(INPUT.read_text(encoding="utf-8"))
    cache = load_cache()

    todo = [bug for bug in bugs if bug["id"] not in cache]
    print(f"{len(bugs)} commits total, {len(cache)} already done, {len(todo)} to do")

    for i in range(0, len(todo), BATCH_SIZE):
        batch = todo[i:i + BATCH_SIZE]
        answer = ask_llm(build_prompt(batch), system=SYSTEM, temperature=0.0)

        for item in parse_answer(answer):
            if isinstance(item, dict) and "id" in item and "is_real_bug" in item:
                cache[item["id"]] = item

        CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  batch {i // BATCH_SIZE + 1}: cache now has {len(cache)} answers")
        time.sleep(PAUSE_SECONDS)

    # Keep only real bugs and add the LLM's summary
    real_bugs = []
    for bug in bugs:
        info = cache.get(bug["id"])
        if info and info["is_real_bug"] is True:
            bug["bug_type"] = info.get("bug_type", "other")
            bug["summary"] = info.get("summary", "")
            real_bugs.append(bug)

    OUTPUT.write_text(json.dumps(real_bugs, indent=2, ensure_ascii=False), encoding="utf-8")

    missing = [b["id"] for b in bugs if b["id"] not in cache]
    types = {}
    for bug in real_bugs:
        types[bug["bug_type"]] = types.get(bug["bug_type"], 0) + 1

    print(f"\nReal bugs: {len(real_bugs)} out of {len(bugs)} commits")
    print(f"Bug types: {types}")
    print(f"With a PR number: {sum(1 for b in real_bugs if b['pr_number'])}")
    print(f"With issue numbers: {sum(1 for b in real_bugs if b['issue_numbers'])}")
    if missing:
        print(f"\n{len(missing)} commits got no answer. Run the script again to retry them.")
    print(f"Saved to {OUTPUT}")


if __name__ == "__main__":
    main()

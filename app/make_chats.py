"""Step 5: write fake Slack threads for the real bugs, plus an answer key."""

import json
import random
import re
from datetime import datetime, timedelta
from pathlib import Path

from llm import ask_llm

BUGS_FILE = Path("data/processed/bugs_classified.json")
CACHE = Path("data/processed/chat_cache.json")
OUT_MESSAGES = Path("data/processed/slack_messages.json")
OUT_TRUTH = Path("data/processed/slack_truth.json")

BATCH_SIZE = 8
CHANNELS = ["#dev", "#bugs", "#backend", "#help"]
USERS = ["rana", "omar", "salma", "youssef", "laila", "karim", "mona", "hassan"]

SYSTEM = (
    "You write realistic, short Slack conversations between software "
    "developers about bugs. You output valid JSON only."
)


def stem(path):
    return path.split("/")[-1].replace(".java", "")


def pick_style(index, bug):
    """Decide how the thread will mention the code (30/20/30/20 split)."""
    slot = index % 10
    if not bug.get("files"):
        return "vague"
    if slot <= 2 and bug.get("pr_number"):
        return "pr"
    if slot <= 4 and bug.get("issue_numbers"):
        return "issue"
    if slot <= 7:
        return "file"
    return "vague"


def rule_for(bug, style):
    if style == "pr":
        return f"Someone writes 'PR #{bug['pr_number']}' exactly once. Do not name files."
    if style == "issue":
        number = bug["issue_numbers"][0]
        return f"Someone writes 'issue #{number}' exactly once. Do not name files."
    if style == "file":
        return (
            f"Someone mentions {stem(bug['files'][0])} in plain text. "
            "Do not write any numbers like #123."
        )
    return "Describe symptoms only. Do NOT mention any number, file name, or class name."


def build_prompt(batch):
    lines = []
    for bug, style in batch:
        lines.append(
            f"- id: {bug['id']}\n"
            f"  what was fixed: {bug['title']}\n"
            f"  type: {bug.get('bug_type', 'unknown')}\n"
            f"  MENTION RULE: {rule_for(bug, style)}"
        )
    bug_text = "\n".join(lines)
    users = ", ".join(USERS)
    return f"""For each bug below, write a realistic Slack thread of 4 to 7 messages
between 2 or 3 developers, BEFORE the bug was fixed: someone reports the symptom,
others ask questions, someone finds the cause, someone says they will fix it.
Casual tone, lowercase is fine, no code blocks.

Use only these usernames: {users}.
Follow each bug's MENTION RULE exactly.

Return ONLY a JSON array, no other text, in this shape:
[{{"id": "BUG-001", "messages": [{{"user": "rana", "text": "..."}}]}}]

BUGS:
{bug_text}"""


def parse_answer(text):
    start, end = text.find("["), text.rfind("]")
    if start == -1 or end == -1:
        raise ValueError("no JSON array found")
    return json.loads(text[start:end + 1], strict=False)


def main():
    bugs = json.loads(BUGS_FILE.read_text(encoding="utf-8"))
    cache = json.loads(CACHE.read_text()) if CACHE.exists() else {}

    items = [(bug, pick_style(i, bug)) for i, bug in enumerate(bugs)]
    todo = [item for item in items if item[0]["id"] not in cache]
    print(f"{len(bugs)} real bugs, {len(cache)} threads already done, {len(todo)} to do")

    for n in range(0, len(todo), BATCH_SIZE):
        batch = todo[n:n + BATCH_SIZE]
        answer = ask_llm(build_prompt(batch), system=SYSTEM, temperature=0.7)
        try:
            for entry in parse_answer(answer):
                if isinstance(entry, dict) and entry.get("messages"):
                    cache[entry["id"]] = entry["messages"]
        except (ValueError, TypeError, AttributeError, KeyError):
            print(f"  batch {n // BATCH_SIZE + 1}: could not read the answer, will retry on next run")
        CACHE.write_text(json.dumps(cache, indent=2, ensure_ascii=False), encoding="utf-8")
        print(f"  batch {n // BATCH_SIZE + 1}: cache now has {len(cache)} threads")

    # ---------- Build final Slack-style files ----------
    rng = random.Random(42)
    messages, truth = [], []
    style_counts = {}
    for bug, style in items:
        thread = cache.get(bug["id"])
        if not thread:
            continue
        thread_id = "T-" + bug["id"].split("-")[-1]
        try:
            start = datetime.fromisoformat(str(bug["date"])[:10])
        except ValueError:
            start = datetime(2026, 1, 1)
        moment = start - timedelta(days=rng.randint(1, 3), hours=-rng.randint(9, 16))
        channel = rng.choice(CHANNELS)
        for msg in thread:
            moment += timedelta(minutes=rng.randint(1, 25))
            messages.append({
                "thread_id": thread_id,
                "channel": channel,
                "ts": moment.isoformat(),
                "user": msg.get("user", "unknown"),
                "text": msg.get("text", ""),
                "synthetic": True,
            })
        truth.append({
            "thread_id": thread_id,
            "bug_id": bug["id"],
            "files": bug["files"],
            "pr_number": bug.get("pr_number"),
            "issue_numbers": bug.get("issue_numbers"),
            "link_style": style,
        })
        style_counts[style] = style_counts.get(style, 0) + 1

    OUT_MESSAGES.write_text(json.dumps(messages, indent=2, ensure_ascii=False), encoding="utf-8")
    OUT_TRUTH.write_text(json.dumps(truth, indent=2, ensure_ascii=False), encoding="utf-8")

    print(f"\nThreads: {len(truth)} | Messages: {len(messages)}")
    print(f"Mention styles: {style_counts}")
    if len(truth) < len(bugs):
        print(f"{len(bugs) - len(truth)} bugs have no thread yet. Run the script again.")
    if messages:
        first = messages[0]["thread_id"]
        print(f"\nSample thread {first}:")
        for m in messages:
            if m["thread_id"] == first:
                print(f"  {m['user']}: {m['text']}")
    print(f"\nSaved to {OUT_MESSAGES} and {OUT_TRUTH}")


if __name__ == "__main__":
    main()

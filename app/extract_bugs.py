"""
Step 2: Extract real bug-fix commits from the Gson git history.

Output: data/processed/bug_fixes.json
Each record = one bug fix: commit, date, author, title, description,
PR number, issue numbers mentioned, and the source files it changed.
"""

import json
import re
import subprocess
from pathlib import Path

# ---------- Settings ----------
REPO = Path("data/raw/gson")
OUTPUT = Path("data/processed/bug_fixes.json")
SINCE = "2008-01-01"   # whole history (Gson started in 2008)

# A commit title must contain one of these words to count as a bug fix
BUG_WORDS = re.compile(
    r"\b(fix|fixes|fixed|bug|regression|crash|npe|exception|incorrect|incorrectly|wrong|"
    r"fail|fails|failure|error|broken|properly|correctly|avoid|prevent|handle|issue|leak|"
    r"overflow|unexpected|reject|throw)\b",
    re.IGNORECASE,
)

# ...and must NOT look like a small non-bug change
SKIP_WORDS = re.compile(
    r"\b(typo|typos|grammar|spelling|javadoc|docs?|documentation|comment|comments|"
    r"readme|bump|dependabot|refactor|format|formatting|checkstyle|lint|"
    r"workflow|github actions|ci)\b",
    re.IGNORECASE,
)

PR_PATTERN = re.compile(r"\(#(\d+)\)\s*$")   # "... (#3116)" at end of title
ISSUE_PATTERN = re.compile(r"(?:#|\bissues?\s+)(\d+)", re.IGNORECASE)


def run_git(*args):
    """Run a git command inside the Gson repo and return its text output."""
    result = subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=True,
    )
    return result.stdout


def is_main_source(path):
    """True for real source files (not tests, not docs)."""
    return path.endswith(".java") and "/src/main/" in path


def get_changed_files(commit_hash):
    """Return the list of files a commit changed."""
    out = run_git("diff-tree", "--no-commit-id", "--name-only", "-r", commit_hash)
    return [line for line in out.splitlines() if line.strip()]


def main():
    if not REPO.exists():
        raise SystemExit("Gson repo not found. Run this from ~/knowledge-extractor")

    # One record per commit, fields split by \x1f, records split by \x1e
    log_format = "%H%x1f%an%x1f%cs%x1f%s%x1f%b%x1e"
    raw = run_git("log", "--no-merges", f"--since={SINCE}", f"--format={log_format}")

    bug_fixes = []
    for record in raw.split("\x1e"):
        record = record.strip("\n")   # keep \x1f, it separates the fields
        if not record:
            continue

        parts = record.split("\x1f", 4)
        if len(parts) < 4:
            continue
        commit_hash, author, date, title = parts[:4]
        body = parts[4] if len(parts) > 4 else ""

        # 1) Is the title a bug fix?
        if not BUG_WORDS.search(title) or SKIP_WORDS.search(title):
            continue

        # 2) Which real source files did it change?
        files = [f for f in get_changed_files(commit_hash) if is_main_source(f)]
        if not files:
            continue

        # 3) PR number and issue numbers
        pr_match = PR_PATTERN.search(title)
        pr_number = int(pr_match.group(1)) if pr_match else None
        issue_numbers = sorted({int(n) for n in ISSUE_PATTERN.findall(title + "\n" + body)})
        if pr_number in issue_numbers:
            issue_numbers.remove(pr_number)

        bug_fixes.append({
            "id": f"BUG-{len(bug_fixes) + 1:03d}",
            "commit": commit_hash,
            "date": date,
            "author": author,
            "title": title.strip(),
            "description": body.strip(),
            "pr_number": pr_number,
            "issue_numbers": issue_numbers,
            "files": files,
        })

    OUTPUT.parent.mkdir(parents=True, exist_ok=True)
    OUTPUT.write_text(json.dumps(bug_fixes, indent=2, ensure_ascii=False), encoding="utf-8")

    # ---------- Summary ----------
    file_counts = {}
    for bug in bug_fixes:
        for f in bug["files"]:
            file_counts[f] = file_counts.get(f, 0) + 1

    print(f"Saved {len(bug_fixes)} bug fixes to {OUTPUT}")
    print(f"Distinct source files touched: {len(file_counts)}")
    print("\nTop 8 files by number of bug fixes:")
    for path, count in sorted(file_counts.items(), key=lambda x: -x[1])[:8]:
        print(f"  {count:3d}  {path.split('/')[-1]}")
    print("\nFirst 5 bug fixes:")
    for bug in bug_fixes[:5]:
        print(f"  {bug['id']} {bug['date']}  {bug['title'][:70]}")


if __name__ == "__main__":
    main()

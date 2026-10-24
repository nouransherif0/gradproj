"""Step 7: risk score per file (likelihood + impact). No AI, no API quota.

Usage:
  python app/risk_scores.py              all history
  python app/risk_scores.py 2026-01-01   only history before that date (for Step 8)
"""

import json
import re
import subprocess
import sys
from collections import Counter
from pathlib import Path

REPO = Path("data/raw/gson")
BUGS = Path("data/processed/bugs_classified.json")
LINKS = Path("data/processed/links.json")
TRUTH = Path("data/processed/slack_truth.json")
CUTOFF = sys.argv[1] if len(sys.argv) > 1 else None
SUFFIX = f"_before_{CUTOFF}" if CUTOFF else ""
OUT = Path(f"data/processed/risk_scores{SUFFIX}.json")

W_LIKELIHOOD, W_IMPACT = 0.65, 0.35
SKIP = {"package-info.java", "module-info.java"}


def base(path):
    return path.split("/")[-1]


def strip_comments(text):
    text = re.sub(r"/\*.*?\*/", " ", text, flags=re.S)
    return re.sub(r"//[^\n]*", " ", text)


def rank01(values):
    """Turn raw numbers into 0-1 scores by ranking files against each other."""
    n = len(values)
    out = []
    for v in values:
        if v <= 0 or n < 2:
            out.append(0.0)
            continue
        less = sum(1 for x in values if x < v)
        equal = sum(1 for x in values if x == v)
        out.append((less + 0.5 * (equal - 1)) / (n - 1))
    return out


def color_for(score):
    if score >= 70:
        return "red"
    if score >= 40:
        return "orange"
    if score >= 15:
        return "yellow"
    return "green"


def load_history():
    fmt = "%x1e%an%x1f%ad%x1f"
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", f"--pretty=format:{fmt}",
         "--date=short", "--name-only"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    commits = []
    for record in out.split("\x1e"):
        parts = record.split("\x1f")
        if len(parts) < 3:
            continue
        author, date, flist = parts[0], parts[1], parts[2]
        if CUTOFF and date >= CUTOFF:
            continue
        names = {base(f.strip()) for f in flist.split("\n") if f.strip().endswith(".java")}
        commits.append((author, names))
    return commits


def main():
    files = sorted(
        p.relative_to(REPO).as_posix()
        for p in REPO.glob("**/src/main/**/*.java")
        if p.name not in SKIP
    )
    names_now = {base(f) for f in files}

    # ---- signals from git history ----
    changes, authors = Counter(), {}
    commits = load_history()
    for author, names in commits:
        for name in names:
            changes[name] += 1
            authors.setdefault(name, Counter())[author] += 1

    # ---- signals from bugs and chats ----
    bugs = json.loads(BUGS.read_text(encoding="utf-8"))
    bug_date = {b["id"]: str(b["date"])[:10] for b in bugs}
    bug_count, used_bugs, orphan_bugs = Counter(), 0, 0
    for b in bugs:
        if CUTOFF and bug_date[b["id"]] >= CUTOFF:
            continue
        hit = False
        for name in {base(f) for f in b["files"]}:
            if name in names_now:
                bug_count[name] += 1
                hit = True
        used_bugs += 1
        orphan_bugs += 0 if hit else 1

    thread_bug = {r["thread_id"]: r["bug_id"] for r in json.loads(TRUTH.read_text(encoding="utf-8"))}
    chat_count = Counter()
    for link in json.loads(LINKS.read_text(encoding="utf-8")):
        bug_id = thread_bug.get(link["thread_id"])
        if CUTOFF and bug_id and bug_date.get(bug_id, "") >= CUTOFF:
            continue
        for name in link["files"]:
            chat_count[name] += 1

    # ---- signals from the code itself ----
    texts = {f: strip_comments((REPO / f).read_text(encoding="utf-8", errors="replace")) for f in files}
    branches = {
        f: len(re.findall(r"\b(?:if|for|while|case|catch)\b|&&|\|\|", t))
        for f, t in texts.items()
    }

    # ---- who uses whom (impact) ----
    stems = {f: Path(f).stem for f in files}
    used_by = {f: set() for f in files}
    for f, text in texts.items():
        words = set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", text))
        for g in files:
            if g != f and stems[g] in words:
                used_by[g].add(f)

    def reach(start):
        seen, stack = set(), list(used_by[start])
        while stack:
            x = stack.pop()
            if x in seen:
                continue
            seen.add(x)
            stack.extend(used_by[x])
        return len(seen - {start})

    direct = {f: len(used_by[f]) for f in files}
    total = {f: reach(f) for f in files}

    def knowledge(f):
        counter = authors.get(base(f))
        n = sum(counter.values()) if counter else 0
        return counter.most_common(1)[0][1] / n if n >= 3 else 0.0

    # ---- combine ----
    raw = {
        "changes": [changes[base(f)] for f in files],
        "bugs": [bug_count[base(f)] for f in files],
        "complexity": [branches[f] for f in files],
        "chat": [chat_count[base(f)] for f in files],
        "knowledge": [knowledge(f) for f in files],
    }
    ranked = {k: rank01(v) for k, v in raw.items()}
    rank_direct, rank_total = rank01([direct[f] for f in files]), rank01([total[f] for f in files])

    rows = []
    for i, f in enumerate(files):
        likelihood = sum(ranked[k][i] for k in ranked) / len(ranked)
        impact = (rank_direct[i] + rank_total[i]) / 2
        score = round(100 * (W_LIKELIHOOD * likelihood + W_IMPACT * impact))
        parts = {
            f"{raw['bugs'][i]} bug fixes": W_LIKELIHOOD / 5 * ranked["bugs"][i],
            f"{raw['changes'][i]} commits": W_LIKELIHOOD / 5 * ranked["changes"][i],
            f"{raw['complexity'][i]} branches (complex)": W_LIKELIHOOD / 5 * ranked["complexity"][i],
            f"{raw['chat'][i]} problem chats": W_LIKELIHOOD / 5 * ranked["chat"][i],
            f"{raw['knowledge'][i]:.0%} of commits by one author": W_LIKELIHOOD / 5 * ranked["knowledge"][i],
            f"used by {direct[f]} files ({total[f]} through chains)": W_IMPACT * impact,
        }
        top = [k for k, v in sorted(parts.items(), key=lambda x: -x[1])[:2] if v > 0]
        rows.append({
            "path": f, "score": score, "color": color_for(score),
            "likelihood": round(likelihood, 3), "impact": round(impact, 3),
            "bugs": raw["bugs"][i], "commits": raw["changes"][i], "chats": raw["chat"][i],
            "branches": raw["complexity"][i], "knowledge": round(raw["knowledge"][i], 3),
            "used_by": direct[f], "used_by_chain": total[f], "top_factors": top,
        })
    rows.sort(key=lambda r: -r["score"])
    OUT.write_text(json.dumps(rows, indent=2), encoding="utf-8")

    colors = Counter(r["color"] for r in rows)
    scope = f"history before {CUTOFF}" if CUTOFF else "all history"
    print(f"Scope: {scope} ({len(commits)} commits)")
    print(f"Files scored: {len(rows)}")
    print(f"Colors: red={colors['red']} orange={colors['orange']} yellow={colors['yellow']} green={colors['green']}")
    print(f"Bug fixes counted: {used_bugs} | on files that no longer exist: {orphan_bugs}\n")
    print(f"{'score':>5} {'color':<7}{'bugs':>5}{'commits':>8}{'used by':>8}  file")
    for r in rows[:10]:
        print(f"{r['score']:>5} {r['color']:<7}{r['bugs']:>5}{r['commits']:>8}{r['used_by']:>8}  {r['path'].split('/')[-1]}")
    print(f"\nSaved to {OUT}")


if __name__ == "__main__":
    main()

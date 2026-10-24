"""Step 8b: is the result stable across cutoffs, and which part of the score helps?"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np

from evaluate import BUGS, REPO, base, metrics
from risk_scores import rank01

CUTOFFS = ["2021-01-01", "2022-01-01", "2023-01-01"]
rng = np.random.default_rng(42)


def run(cutoff):
    subprocess.run([sys.executable, "app/risk_scores.py", cutoff],
                   check=True, capture_output=True)
    path = Path(f"data/processed/risk_scores_before_{cutoff}.json")
    rows = [r for r in json.loads(path.read_text()) if r["commits"] > 0]
    n = len(rows)
    by_name = {}
    for i, r in enumerate(rows):
        by_name.setdefault(base(r["path"]), []).append(i)
    future, total = np.zeros(n), 0
    for b in json.loads(BUGS.read_text(encoding="utf-8")):
        if str(b["date"])[:10] < cutoff:
            continue
        total += 1
        for name in {base(f) for f in b["files"]}:
            for i in by_name.get(name, []):
                future[i] += 1
    y = (future > 0).astype(int)
    if y.sum() == 0 or y.sum() == n:
        return None

    def rk(key):
        return np.array(rank01([r[key] for r in rows]))

    commits, bugs, branches, knowledge = rk("commits"), rk("bugs"), rk("branches"), rk("knowledge")
    loc = np.array([
        len((REPO / r["path"]).read_text(encoding="utf-8", errors="replace").splitlines())
        for r in rows
    ], float)
    methods = {
        "Largest files": loc,
        "Code-only hotspots": commits * branches,
        "Ours: likelihood part only": np.array([r["likelihood"] for r in rows]),
        "Ours: impact part only": np.array([r["impact"] for r in rows]),
        "Ours without chat/dependencies": (commits + bugs + branches + knowledge) / 4,
        "Ours full score": np.array([r["score"] for r in rows], float),
    }
    auc = {name: metrics(score, y, future, rng)[3] for name, score in methods.items()}
    return n, int(y.sum()), total, auc


results = {c: run(c) for c in CUTOFFS}
print(f"{'':<34}" + "".join(f"{c:>13}" for c in CUTOFFS))
for label, idx in [("Files in test", 0), ("Files that got a bug", 1), ("Bugs in hidden period", 2)]:
    print(f"{label:<34}" + "".join(f"{(results[c][idx] if results[c] else '-'):>13}" for c in CUTOFFS))
print("\nAUC (0.5 = coin flip, 1.0 = perfect)")
names = next(r for r in results.values() if r)[3].keys()
for name in names:
    print(f"{name:<34}" + "".join(
        f"{results[c][3][name]:>13.2f}" if results[c] else f"{'-':>13}" for c in CUTOFFS))

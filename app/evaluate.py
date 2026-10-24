"""Step 8: hidden-months accuracy test for the risk score (slides 26 to 28).

Usage: python app/evaluate.py [cutoff-date]     default 2022-01-01
"""

import json
import subprocess
import sys
from pathlib import Path

import numpy as np
from scipy.stats import spearmanr
from sklearn.metrics import roc_auc_score

from risk_scores import rank01

CUTOFF = sys.argv[1] if len(sys.argv) > 1 else "2022-01-01"
REPO = Path("data/raw/gson")
BUGS = Path("data/processed/bugs_classified.json")
SCORES = Path(f"data/processed/risk_scores_before_{CUTOFF}.json")
OUT = Path(f"data/processed/evaluation_{CUTOFF}.json")


def base(path):
    return path.split("/")[-1]


def metrics(score, y, future, rng):
    n = len(score)
    order = np.argsort(-(score + rng.random(n) * 1e-9))
    k10, k20 = max(1, round(0.10 * n)), max(1, round(0.20 * n))
    top10 = y[order[:k10]].mean()
    share20 = future[order[:k20]].sum() / future.sum()
    rho = spearmanr(score, future)[0] if np.ptp(score) > 0 else 0.0
    auc = roc_auc_score(y, score + rng.random(n) * 1e-9)
    return top10, share20, rho, auc


def auc_range(score, y, rng, rounds=500):
    n, values = len(score), []
    for _ in range(rounds):
        idx = rng.integers(0, n, n)
        if 0 < y[idx].sum() < n:
            values.append(roc_auc_score(y[idx], score[idx]))
    return tuple(np.percentile(values, [2.5, 97.5])) if values else (float("nan"),) * 2


def main():
    rng = np.random.default_rng(42)
    subprocess.run([sys.executable, "app/risk_scores.py", CUTOFF],
                   check=True, capture_output=True)
    rows = [r for r in json.loads(SCORES.read_text()) if r["commits"] > 0]
    n = len(rows)
    by_name = {}
    for i, r in enumerate(rows):
        by_name.setdefault(base(r["path"]), []).append(i)

    future = np.zeros(n)
    total_future = dropped = 0
    for b in json.loads(BUGS.read_text(encoding="utf-8")):
        if str(b["date"])[:10] < CUTOFF:
            continue
        total_future += 1
        hit = False
        for name in {base(f) for f in b["files"]}:
            for i in by_name.get(name, []):
                future[i] += 1
                hit = True
        dropped += 0 if hit else 1
    y = (future > 0).astype(int)
    if y.sum() == 0 or y.sum() == n:
        raise SystemExit("Not enough variety: pick another cutoff date.")

    loc = np.array([
        len((REPO / r["path"]).read_text(encoding="utf-8", errors="replace").splitlines())
        for r in rows
    ], float)

    def rk(key):
        return np.array(rank01([r[key] for r in rows]))

    commits, bugs, branches, knowledge = rk("commits"), rk("bugs"), rk("branches"), rk("knowledge")
    methods = {
        "Largest files": loc,
        "Most-edited files": np.array([r["commits"] for r in rows], float),
        "Code-only hotspots": commits * branches,
        "Ours, no chat/dependencies": (commits + bugs + branches + knowledge) / 4,
        "Ours, with chat/dependencies": np.array([r["score"] for r in rows], float),
    }

    results = {}
    runs = [metrics(rng.random(n), y, future, rng) for _ in range(200)]
    results["Random guessing"] = (*np.mean(runs, axis=0), None)
    for name, score in methods.items():
        results[name] = (*metrics(score, y, future, rng), auc_range(score, y, rng))

    print(f"Hidden period: {CUTOFF} and later")
    print(f"Files scored (existed before cutoff): {n}")
    print(f"Files that really got a bug afterwards: {int(y.sum())} ({y.mean():.0%})")
    print(f"Bugs after cutoff: {total_future} | on files outside the test: {dropped}\n")
    print(f"{'method':<30}{'top 10%':>8}{'bugs in 20%':>12}{'rank match':>11}{'AUC':>6}  AUC 95% range")
    for name, (t10, s20, rho, auc, rng95) in results.items():
        span = f"{rng95[0]:.2f} to {rng95[1]:.2f}" if rng95 else "-"
        print(f"{name:<30}{t10:>8.0%}{s20:>12.0%}{rho:>11.2f}{auc:>6.2f}  {span}")
    print(f"\nFor comparison: picking files at random gets {y.mean():.0%} in the top 10%.")

    OUT.write_text(json.dumps({
        "cutoff": CUTOFF, "files": n, "buggy_files": int(y.sum()),
        "results": {k: {"top10": v[0], "bugs_in_top20": v[1], "spearman": v[2], "auc": v[3]}
                    for k, v in results.items()},
    }, indent=2), encoding="utf-8")
    print(f"Saved to {OUT}")


if __name__ == "__main__":
    main()

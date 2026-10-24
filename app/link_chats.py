"""Step 6: link chat threads to files with rules only (no AI), then score them."""

import json
import re
import subprocess
from pathlib import Path

REPO = Path("data/raw/gson")
MSGS = Path("data/processed/slack_messages.json")
TRUTH = Path("data/processed/slack_truth.json")
OUT = Path("data/processed/links.json")


def is_test(path):
    return "/test/" in path or path.endswith("Test.java")


def load_history():
    fmt = "%x1e%H%x1f%s%x1f%b%x1f"
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", f"--pretty=format:{fmt}", "--name-only"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    commits = []
    for record in out.split("\x1e"):
        fields = record.split("\x1f")
        if len(fields) < 4:
            continue
        _, subject, body, files = fields[:4]
        files = [f.strip() for f in files.split("\n") if f.strip().endswith(".java")]
        files = [f for f in files if not is_test(f)]
        commits.append({"text": subject + "\n" + body, "files": files})
    return commits


def base(path):
    return path.split("/")[-1]


def numbers_in(text):
    """Find '#123' and also 'issue 123' / 'bug 123' written without a hash."""
    found = set()
    for a, b in re.findall(r"#(\d+)|\b(?:issues?|bugs?)\s+#?(\d+)", text, flags=re.I):
        found.add(a or b)
    return found


def main():
    commits = load_history()
    number_to_files = {}
    known_names = {}
    for c in commits:
        for n in numbers_in(c["text"]):
            number_to_files.setdefault(n, set()).update(base(f) for f in c["files"])
        for f in c["files"]:
            known_names[base(f)[:-5]] = base(f)

    threads = {}
    for m in json.loads(MSGS.read_text(encoding="utf-8")):
        threads.setdefault(m["thread_id"], []).append(m["text"])
    truth_rows = json.loads(TRUTH.read_text(encoding="utf-8"))
    truth = {r["thread_id"]: {base(f) for f in r["files"]} for r in truth_rows}
    style = {r["thread_id"]: r["link_style"] for r in truth_rows}

    id_pred, name_pred, final = {}, {}, {}
    links = []
    for tid, texts in threads.items():
        text = " ".join(texts)
        ids = set()
        for n in numbers_in(text):
            ids |= number_to_files.get(n, set())
        words = set(re.findall(r"[A-Za-z_$][A-Za-z0-9_$]*", text))
        names = {known_names[w] for w in words if len(w) >= 4 and w in known_names}
        id_pred[tid], name_pred[tid] = ids, names
        if ids:
            final[tid], method, conf = ids, "id_link", "high"
        elif names:
            final[tid], method, conf = names, "file_name", "medium"
        else:
            final[tid], method, conf = set(), "none", "none"
        links.append({"thread_id": tid, "method": method, "confidence": conf,
                      "files": sorted(final[tid])})

    OUT.write_text(json.dumps(links, indent=2), encoding="utf-8")

    def score(preds, subset=None):
        keys = [t for t in preds if subset is None or t in subset]
        covered = [t for t in keys if preds[t]]
        if not keys or not covered:
            return 0.0, 0.0, 0.0, 0.0
        cov = len(covered) / len(keys)
        prec = sum(len(preds[t] & truth[t]) / len(preds[t]) for t in covered) / len(covered)
        rec = sum(len(preds[t] & truth[t]) / len(truth[t]) for t in covered if truth[t]) / len(covered)
        hit = sum(1 for t in covered if preds[t] & truth[t]) / len(covered)
        return cov, prec, rec, hit

    print(f"Threads: {len(threads)}\n")
    print(f"{'method':<22}{'coverage':>9}{'precision':>10}{'recall':>8}{'hit rate':>9}")
    for label, preds in [("1. ID link", id_pred), ("2. file name", name_pred),
                         ("cascade (ID, then name)", final)]:
        cov, prec, rec, hit = score(preds)
        print(f"{label:<22}{cov:>9.0%}{prec:>10.0%}{rec:>8.0%}{hit:>9.0%}")

    print("\nCascade by how the fake chat mentions the code:")
    for s in ["pr", "issue", "file", "vague"]:
        subset = {t for t in threads if style.get(t) == s}
        cov, prec, rec, hit = score(final, subset)
        print(f"  {s:<6} threads={len(subset):<4} coverage={cov:.0%}  hit rate={hit:.0%}")
    print(f"\nSaved to {OUT}")


if __name__ == "__main__":
    main()

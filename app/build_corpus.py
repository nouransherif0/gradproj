"""Turn the whole Gson repository into one searchable corpus."""

import json
import subprocess
from pathlib import Path

REPO = Path("data/raw/gson")
OUT = Path("data/processed/corpus.json")
CODE_LINES = 50
DOC_LINES = 40


def chunk_lines(text, size):
    lines = text.splitlines()
    for start in range(0, len(lines), size):
        part = "\n".join(lines[start:start + size]).strip()
        if part:
            yield start + 1, part


def unique(base, used):
    name, n = base, 2
    while name in used:
        name = f"{base}-{n}"
        n += 1
    used.add(name)
    return name


def read_commits():
    fmt = "%x1e%H%x1f%an%x1f%ad%x1f%s%x1f%b%x1f"
    out = subprocess.run(
        ["git", "-C", str(REPO), "log", f"--pretty=format:{fmt}",
         "--date=short", "--name-only"],
        capture_output=True, text=True, encoding="utf-8", errors="replace", check=True,
    ).stdout
    items = []
    for record in out.split("\x1e"):
        fields = record.split("\x1f")
        if len(fields) < 6:
            continue
        sha, author, date, subject, body, files = fields[:6]
        files = [f.strip() for f in files.split("\n") if f.strip()]
        names = ", ".join(f.split("/")[-1] for f in files[:4])
        items.append({
            "id": f"C-{sha[:7]}",
            "kind": "commit",
            "source": f"{date} | {author} | {subject} | files: {names}",
            "text": f"{subject}\n{body}\n{' '.join(files)}",
            "show": body.strip(),
        })
    return items


def read_code(used):
    items = []
    for path in sorted(REPO.glob("**/src/main/**/*.java")):
        rel = path.relative_to(REPO)
        text = path.read_text(encoding="utf-8", errors="replace")
        for start, part in chunk_lines(text, CODE_LINES):
            items.append({
                "id": unique(f"CODE-{path.stem}-L{start}", used),
                "kind": "code",
                "source": f"{rel}:{start}",
                "text": f"{path.stem} {part}",
                "show": part,
            })
    return items


def read_docs(used):
    items = []
    for path in sorted(REPO.glob("**/*.md")):
        rel = path.relative_to(REPO)
        if ".git" in rel.parts or "node_modules" in rel.parts:
            continue
        text = path.read_text(encoding="utf-8", errors="replace")
        for start, part in chunk_lines(text, DOC_LINES):
            items.append({
                "id": unique(f"DOC-{path.stem}-L{start}", used),
                "kind": "doc",
                "source": f"{rel}:{start}",
                "text": part,
                "show": part,
            })
    return items


def main():
    used = set()
    corpus = read_commits() + read_code(used) + read_docs(used)
    OUT.write_text(json.dumps(corpus, ensure_ascii=False), encoding="utf-8")
    counts = {}
    for item in corpus:
        counts[item["kind"]] = counts.get(item["kind"], 0) + 1
    print(f"Corpus saved to {OUT}")
    print(f"Items by kind: {counts}")


if __name__ == "__main__":
    main()

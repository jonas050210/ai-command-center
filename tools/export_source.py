"""Export the complete project source as a single markdown document.

Usage (from the repo root):
    python tools/export_source.py                  # → FULLSOURCE.md
    python tools/export_source.py --output out.md  # custom path

Every git-tracked file is inlined verbatim in a labeled, language-fenced
section with a file index table (# · file · lines · bytes). The output is a
SNAPSHOT ARTIFACT (gitignored) — the files on disk are always the truth;
re-run this after code changes instead of editing the output by hand.
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
LANG = {
    ".py": "python", ".tsx": "tsx", ".ts": "typescript", ".css": "css",
    ".html": "html", ".json": "json", ".md": "markdown", ".yml": "yaml",
    ".yaml": "yaml", ".iss": "ini", ".spec": "python", ".txt": "text",
    ".ini": "ini",
}
NAME_LANG = {"ROADMAP": "text", ".gitignore": "text", ".env.example": "bash"}
AREA_ORDER = [".", "backend", "frontend", "desktop", "tools", "tests", ".github"]


def is_binary(raw: bytes) -> bool:
    return b"\x00" in raw[:8192]


def lang_for(path: str) -> str:
    name = Path(path).name
    return NAME_LANG.get(name, LANG.get(Path(path).suffix, "text"))


def area_key(path: str) -> tuple[int, str]:
    top = path.split("/", 1)[0]
    if "/" not in path:
        return (0, path.lower())
    try:
        return (AREA_ORDER.index(top), path.lower())
    except ValueError:
        return (len(AREA_ORDER), path.lower())


def fence_for(content: str) -> str:
    longest = 0
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("`"):
            longest = max(longest, len(stripped) - len(stripped.lstrip("`")))
    return "`" * max(4, longest + 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--output", default=str(ROOT / "FULLSOURCE.md"),
                        help="output markdown path (default: FULLSOURCE.md)")
    parser.add_argument("--include", metavar="GLOB", action="append",
                        help="extra git-tracked path filter (repeatable)")
    args = parser.parse_args()

    output = Path(args.output).resolve()
    files = subprocess.run(
        ["git", "ls-files"], cwd=ROOT, check=True, capture_output=True,
        text=True).stdout.split()
    out_rel = str(output.relative_to(ROOT)) if output.is_relative_to(ROOT) else None
    files = sorted((f for f in files if f != out_rel), key=area_key)
    if args.include:
        import fnmatch
        files = [f for f in files
                 if any(fnmatch.fnmatch(f, g) for g in args.include)]

    commit = subprocess.run(
        ["git", "rev-parse", "--short", "HEAD"], cwd=ROOT, check=True,
        capture_output=True, text=True).stdout.strip()

    entries: list[tuple[str, int, int]] = []
    sections: list[str] = []
    total_lines = 0
    for rel in files:
        raw = (ROOT / rel).read_bytes()
        entries.append((rel, 0, len(raw)))
        if is_binary(raw):
            sections.append(f"### `{rel}` — binary file\n\n"
                            f"*Binary content ({len(raw):,} bytes) — not inlined.*\n")
            continue
        content = raw.decode("utf-8", errors="replace")
        lines = content.count("\n") + (
            0 if content.endswith("\n") or not content else 1)
        total_lines += lines
        entries[-1] = (rel, lines, len(raw))
        body = content if content.endswith("\n") or not content else content + "\n"
        fence = fence_for(content)
        sections.append(f"### `{rel}` — {lines} lines\n\n"
                        f"{fence}{lang_for(rel)}\n{body}{fence}\n")

    parts: list[str] = [
        "# FULLSOURCE.md — complete source snapshot\n\n",
        "**Every git-tracked file of AI Command Center, inlined verbatim.** ",
        f"{len(files)} files · {total_lines:,} lines · ",
        f"{sum(b for _, _, b in entries):,} bytes · commit `{commit}`.\n\n",
        "Regenerate after code changes: `python tools/export_source.py`.\n",
        "This is a snapshot artifact — the files on disk are the source of truth.\n\n",
        "## File index\n\n",
        "| # | File | Lines | Bytes |\n|---|---|---|---|\n",
    ]
    for i, (rel, lines, nbytes) in enumerate(entries, 1):
        shown = "binary" if lines == 0 and nbytes > 0 else str(lines)
        parts.append(f"| {i} | `{rel}` | {shown} | {nbytes:,} |\n")
    parts.append(f"| **Σ** | **{len(files)} files** | **{total_lines:,}** | "
                 f"**{sum(b for _, _, b in entries):,}** |\n\n")
    parts.extend(s + "\n" for s in sections)

    output.write_text("".join(parts), encoding="utf-8")
    print(f"wrote {output}: {len(files)} files, {total_lines:,} lines, "
          f"{output.stat().st_size:,} bytes @ commit {commit}")
    return 0


if __name__ == "__main__":
    sys.exit(main())

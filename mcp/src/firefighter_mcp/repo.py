"""Read-only access to the firefighter-platform checkout.

Everything here reads the repo live, so the server's answers track whatever
branch is checked out instead of a copy that goes stale. Nothing writes.
"""

from __future__ import annotations

import os
import re
import subprocess
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

# lang/mcp/src/firefighter_mcp/repo.py -> /Volumes/.../firefighter-platform
_DEFAULT_REPO = Path(__file__).resolve().parents[4] / "firefighter-platform"

MAX_CHARS = 24_000  # keep a single tool result well inside an agent's context


class RepoError(Exception):
    """A request the repo cannot answer (missing file, path outside the repo)."""


def repo_root() -> Path:
    root = Path(os.environ.get("FIREFIGHTER_REPO", _DEFAULT_REPO)).expanduser().resolve()
    if not (root / "CLAUDE.md").is_file():
        raise RepoError(
            f"firefighter-platform not found at {root}; set FIREFIGHTER_REPO to the checkout"
        )
    return root


def resolve(rel: str) -> Path:
    """A repo-relative path, refused if it escapes the checkout."""
    root = repo_root()
    path = (root / rel).resolve()
    if not path.is_relative_to(root):
        raise RepoError(f"{rel!r} is outside the repository")
    return path


def read_text(rel: str) -> str:
    path = resolve(rel)
    if not path.is_file():
        raise RepoError(f"no such file: {rel}")
    return path.read_text(encoding="utf-8", errors="replace")


def truncate(text: str, limit: int = MAX_CHARS) -> str:
    if len(text) <= limit:
        return text
    return text[:limit] + f"\n\n[... truncated {len(text) - limit} chars; request a section to read the rest]"


def git_info() -> dict[str, str]:
    """Branch and HEAD of the checkout, so a caller knows which state it read."""

    def git(*args: str) -> str:
        try:
            out = subprocess.run(
                ["git", "-C", str(repo_root()), *args],
                capture_output=True, text=True, timeout=5, check=True,
            )
            return out.stdout.strip()
        except (subprocess.SubprocessError, OSError):
            return "unknown"

    return {
        "branch": git("branch", "--show-current"),
        "head": git("log", "-1", "--format=%h %cs %s"),
    }



# --- source code (tracked files only) ---------------------------------------

def _git(*args: str, timeout: int = 20) -> subprocess.CompletedProcess:
    return subprocess.run(["git", "-C", str(repo_root()), *args],
                          capture_output=True, text=True, timeout=timeout)


def tracked_files(pathspec: str | None = None) -> list[str]:
    """Files git tracks, so secrets (.env*, amplify_outputs*) and node_modules never show."""
    out = _git("ls-files", "--", *( [pathspec] if pathspec else [] ))
    return out.stdout.splitlines()


def read_source(rel: str, start: int = 1, end: int | None = None) -> str:
    rel = rel.strip().removeprefix("./")
    resolve(rel)  # refuses paths outside the checkout
    if rel not in set(tracked_files(rel)):
        raise RepoError(f"{rel} is not a tracked file; use list_files to find the path")
    lines = read_text(rel).splitlines()
    end = min(end or len(lines), len(lines))
    if start < 1 or start > max(end, 1):
        raise RepoError(f"{rel} has {len(lines)} lines; start {start} is out of range")
    width = len(str(end))
    body = "\n".join(f"{i:>{width}}  {lines[i - 1]}" for i in range(start, end + 1))
    return truncate(f"{rel} (lines {start}-{end} of {len(lines)})\n{body}")


def grep(pattern: str, pathspec: str | None = None, limit: int = 60) -> list[dict]:
    """`git grep` over tracked files: extended regex, case-insensitive."""
    args = ["grep", "-n", "-I", "-i", "-E", "--no-color", "-e", pattern]
    if pathspec:
        args += ["--", pathspec]
    out = _git(*args)
    if out.returncode > 1:
        raise RepoError(f"bad pattern or pathspec: {out.stderr.strip()}")
    hits = []
    for line in out.stdout.splitlines()[:limit]:
        path, lineno, text = line.split(":", 2)
        hits.append({"path": path, "line": int(lineno), "text": text.strip()[:240]})
    return hits

# --- markdown ---------------------------------------------------------------

_HEADING = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")


@dataclass
class Section:
    level: int
    title: str
    start_line: int  # 1-based line of the heading
    lines: list[str] = field(default_factory=list)

    @property
    def body(self) -> str:
        return "\n".join(self.lines).strip()


def sections(text: str) -> list[Section]:
    """Split markdown into heading sections, ignoring `#` inside code fences.

    Each section's body runs to the next heading of any level; text before the
    first heading becomes a level-0 section titled "".
    """
    out = [Section(0, "", 1)]
    fenced = False
    for i, line in enumerate(text.splitlines(), start=1):
        if line.lstrip().startswith(("```", "~~~")):
            fenced = not fenced
        m = None if fenced else _HEADING.match(line)
        if m:
            out.append(Section(len(m.group(1)), m.group(2), i))
        else:
            out[-1].lines.append(line)
    return [s for s in out if s.title or s.body]


def section_with_children(text: str, title: str) -> str | None:
    """The named section and its subsections (case-insensitive, prefix match)."""
    secs = sections(text)
    want = title.strip().lower().lstrip("#").strip()
    for i, s in enumerate(secs):
        if s.title.lower() == want or (want and s.title.lower().startswith(want)):
            parts = [f"{'#' * s.level} {s.title}\n\n{s.body}"]
            for child in secs[i + 1:]:
                if child.level <= s.level:
                    break
                parts.append(f"{'#' * child.level} {child.title}\n\n{child.body}")
            return "\n\n".join(parts)
    return None


def bullets(body: str) -> list[str]:
    """Top-level `- ` list items, each joined with its continuation lines."""
    items: list[str] = []
    for line in body.splitlines():
        if line.startswith("- "):
            items.append(line[2:].strip())
        elif items and line.startswith((" ", "\t")) and line.strip():
            items[-1] += " " + line.strip()
        elif items and not line.strip():
            items.append("")  # paragraph break ends the item
    return [i for i in items if i]


def first_paragraph(text: str, limit: int = 300) -> str:
    for s in sections(text):
        for para in re.split(r"\n\s*\n", s.body):
            para = " ".join(para.split())
            if para and not para.startswith(("|", "```", "<", "!")):
                return para if len(para) <= limit else para[: limit - 3] + "..."
    return ""


# --- the doc set ------------------------------------------------------------

_ROOT_DOCS = ("CLAUDE.md", "README.md", "CONTRIBUTING.md", "UPSTREAMING.md",
              "CDK_WIRING_DEPLOY.md", "SES_CONFIGURATION.md")


@lru_cache(maxsize=1)
def _doc_paths_cached(root: str) -> tuple[str, ...]:
    base = Path(root)
    found = [p for p in _ROOT_DOCS if (base / p).is_file()]
    found += sorted(
        str(p.relative_to(base)) for p in (base / "docs").rglob("*.md")
    )
    return tuple(found)


def doc_paths() -> list[str]:
    return list(_doc_paths_cached(str(repo_root())))


def doc_category(path: str) -> str:
    name = Path(path).name
    if "/" not in path:
        return "root"
    if path.startswith("docs/playbooks/"):
        return "playbook"
    if path.startswith("docs/developer-package-v1/"):
        return "founder-package"
    if path.startswith("docs/prototypes/"):
        return "prototype"
    if name.endswith("-changelog.md"):
        return "changelog"
    if name.endswith("-module.md"):
        return "module-spec"
    return "design"


_WORD = re.compile(r"[a-z0-9_]+")


def search(query: str, paths: list[str], limit: int = 10) -> list[dict]:
    """Rank markdown sections by how many query terms they hold.

    Plain term scoring: a heading hit counts triple, every term found adds a
    bonus so sections matching all terms rise above ones that repeat one.
    """
    terms = [t for t in _WORD.findall(query.lower()) if len(t) > 1]
    if not terms:
        return []
    hits = []
    for path in paths:
        try:
            text = read_text(path)
        except RepoError:
            continue
        for s in sections(text):
            title, body = s.title.lower(), s.body.lower()
            counts = {t: body.count(t) + 3 * title.count(t) for t in terms}
            matched = sum(1 for c in counts.values() if c)
            if not matched:
                continue
            score = sum(min(c, 10) for c in counts.values()) + 10 * matched
            hits.append((score, path, s, terms))
    hits.sort(key=lambda h: -h[0])
    return [
        {"path": path, "section": s.title or "(top)", "line": s.start_line,
         "score": score, "snippet": _snippet(s.body, terms)}
        for score, path, s, terms in hits[:limit]
    ]


def _snippet(body: str, terms: list[str], width: int = 280) -> str:
    low = body.lower()
    pos = min((low.find(t) for t in terms if t in low), default=0)
    start = max(0, pos - width // 3)
    text = " ".join(body[start:start + width].split())
    return ("..." if start else "") + text + ("..." if start + width < len(body) else "")

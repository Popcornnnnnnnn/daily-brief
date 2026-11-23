#!/usr/bin/env python3
"""Zero-tolerance privacy & artifact gate for the public daily-brief repo.

Scans tracked/publishable files before any commit or deployment and fails
closed with file:line diagnostics if any secret, local path, Notion workspace
identifier, or excluded personal section appears.

Optional private local patterns (e.g. exact personal usernames, social IDs,
private repo names, or workspace UUID prefixes) can be supplied outside Git via
`--extra-terms <file>` or `DAILY_BRIEF_PRIVATE_TERMS` (or automatically from
`~/.config/daily-brief-publisher/private-patterns.txt` when present locally).
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import re
import sys
from typing import Iterable

SKIP_DIRS = {
    ".git",
    "node_modules",
    "dist",
    ".astro",
    "__pycache__",
    ".pytest_cache",
    "state",
    "exports",
    "bridge-repo",
}

BINARY_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".webp",
    ".gif",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".otf",
    ".pdf",
    ".gz",
    ".zip",
}

NOTION_DOMAIN_PARSER_FILES = {
    "collectors/brief_feedback.py",
    "tests/test_brief_feedback.py",
}

FORBIDDEN_FILE_PATTERNS = (
    re.compile(r"^collectors/[^/]+/config\.json$"),
    re.compile(r"^publisher/(?!config\.example\.json$)[^/]+\.json$"),
    re.compile(r".*\.(sqlite|sqlite-wal|sqlite-shm|db)$"),
    re.compile(r"(^|/)\.env(\..+)?$"),
)

SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("notion_token", re.compile(r"\b(?:ntn|secret)_[A-Za-z0-9]{16,}\b")),
    ("github_token", re.compile(r"\b(?:ghp|gho|ghu|ghs)_[A-Za-z0-9]{20,}\b")),
    ("github_pat", re.compile(r"\bgithub_pat_[A-Za-z0-9_]{20,}\b")),
    ("api_key_sk", re.compile(r"\bsk-[A-Za-z0-9_-]{24,}\b")),
)

LOCAL_PATH_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("local_macos_home", re.compile(r"/Users/(?!example\b|user\b|yourname\b|runner\b)[A-Za-z0-9._-]{2,}")),
    ("internal_uri", re.compile(r"\b(?:collection://|codex://(?!threads/test-thread\b))")),
)

NOTION_LINK_PATTERN = re.compile(
    r"(?:https?://)?(?:www\.|app\.)?(?:" + "notion\\.so|app\\.notion\\.com" + r")/",
    re.I,
)

PUBLISHED_CONTENT_TERMS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("favorites_recordings_cn", re.compile("收藏与" + "录音")),
    ("default_favorites_cn", re.compile("默认" + "收藏夹")),
    ("apple_voice_memos", re.compile(r"Apple\s+Voice\s+Memos", re.I)),
    ("xiaohongshu", re.compile("Xiaohongshu", re.I)),
    ("bilibili_default", re.compile(r"Bilibili\s+Default", re.I)),
    ("favorites_and_recordings", re.compile(r"\bFavorites\s*(?:&|and)\s*recordings?\b", re.I)),
    ("saved_items_section", re.compile(r"(?:^#{1,4}\s*Saved\s+items(?:\s*(?:&|and)\s*recordings?)?\b|\bSaved\s+items\s*(?:&|and)\s*recordings?\b)", re.I)),
    ("shanghai_events_or_weekend", re.compile(r"\bShanghai\s+(?:events?|weekend)\b|上海" + "周末", re.I)),
    ("reading_queue", re.compile(r"\bReading\s+(?:Queue|Inbox)\b")),
    ("private_notes_field", re.compile(r"\bMyNotes\b")),
    ("migrated_feedback_note", re.compile(r"Historical\s+feedback\s*\(preserved\s+during\s+migration", re.I)),
    ("inline_useful_checkbox", re.compile(r"👍\s*\[Useful\]")),
)

CONTENT_SCOPES = ("editions/", "site/", "specpack/", "README.md", "AGENTS.md")


def load_extra_patterns(extra_file: Path | None = None) -> list[tuple[str, re.Pattern[str]]]:
    candidate = extra_file
    if candidate is None:
        env_path = os.environ.get("DAILY_BRIEF_PRIVATE_TERMS")
        if env_path:
            candidate = Path(os.path.expanduser(env_path))
        else:
            default_local = Path.home() / ".config" / "daily-brief-publisher" / "private-patterns.txt"
            if default_local.exists():
                candidate = default_local
    if candidate is None or not candidate.exists():
        return []

    patterns: list[tuple[str, re.Pattern[str]]] = []
    for idx, raw in enumerate(candidate.read_text(encoding="utf-8").splitlines(), 1):
        term = raw.strip()
        if not term or term.startswith("#"):
            continue
        if term.startswith("regex:"):
            patterns.append((f"extra_term_{idx}", re.compile(term[6:], re.I)))
        else:
            patterns.append((f"extra_term_{idx}", re.compile(re.escape(term), re.I)))
    return patterns


def line_has_private_content(line: str, extra_patterns: list[tuple[str, re.Pattern[str]]] | None = None) -> bool:
    for _, regex in SECRET_PATTERNS:
        if regex.search(line):
            return True
    for _, regex in LOCAL_PATH_PATTERNS:
        if regex.search(line):
            return True
    for _, regex in PUBLISHED_CONTENT_TERMS:
        if regex.search(line):
            return True
    for _, regex in (extra_patterns or []):
        if regex.search(line):
            return True
    return False


def iter_files(root: Path) -> Iterable[Path]:
    for path in sorted(root.rglob("*")):
        if path.is_dir():
            continue
        rel = path.relative_to(root)
        if any(part in SKIP_DIRS for part in rel.parts):
            continue
        yield path


def scan_repository(root: Path, extra_terms_file: Path | None = None) -> list[str]:
    root = root.resolve()
    extra_patterns = load_extra_patterns(extra_terms_file)
    violations: list[str] = []

    for path in iter_files(root):
        rel = path.relative_to(root).as_posix()

        for pattern in FORBIDDEN_FILE_PATTERNS:
            if pattern.match(rel):
                violations.append(f"{rel}:1: forbidden runtime or secret file present in workspace/repo")

        if path.suffix.lower() in BINARY_EXTENSIONS:
            continue
        if rel == "publisher/privacy_gate.py":
            continue

        try:
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue

        in_published_scope = rel in CONTENT_SCOPES or any(rel.startswith(p) for p in CONTENT_SCOPES if p.endswith("/"))
        allow_parser_literal = rel in NOTION_DOMAIN_PARSER_FILES

        for lineno, line in enumerate(text.splitlines(), 1):
            for name, regex in SECRET_PATTERNS:
                if regex.search(line):
                    violations.append(f"{rel}:{lineno}: secret pattern matched ({name})")

            for name, regex in LOCAL_PATH_PATTERNS:
                if allow_parser_literal and name == "internal_uri" and "collection://" in line:
                    continue
                if regex.search(line):
                    violations.append(f"{rel}:{lineno}: private path or internal URI matched ({name})")

            for name, regex in extra_patterns:
                if regex.search(line):
                    violations.append(f"{rel}:{lineno}: private configured term matched ({name})")

            if not allow_parser_literal and NOTION_LINK_PATTERN.search(line):
                violations.append(f"{rel}:{lineno}: private Notion URL leaked")

            if in_published_scope:
                for name, regex in PUBLISHED_CONTENT_TERMS:
                    if regex.search(line):
                        violations.append(f"{rel}:{lineno}: forbidden personal/private content matched ({name})")

    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify repository is clean of secrets and private user data.")
    parser.add_argument("root", nargs="?", default=".", help="Repository root to scan (default: .)")
    parser.add_argument("--extra-terms", type=Path, help="Optional local private-patterns file outside Git")
    args = parser.parse_args()

    violations = scan_repository(Path(args.root), extra_terms_file=args.extra_terms)
    if violations:
        print(f"PRIVACY GATE FAILED ({len(violations)} violation(s)):", file=sys.stderr)
        for item in violations:
            print(f"  - {item}", file=sys.stderr)
        return 1
    print("PRIVACY GATE PASSED: 0 violations.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

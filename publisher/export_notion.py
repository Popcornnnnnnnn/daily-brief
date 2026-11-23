#!/usr/bin/env python3
"""Deterministic Notion-to-Markdown exporter for public Daily Brief editions.

Reads published Daily Brief database rows via the Notion REST API (read-only),
normalizes headings and rich text, localizes expiring Notion-hosted images,
strips any private sections/URLs/legacy inline controls, optionally attaches
counter-only provenance from private receipts, and writes:
  - editions/YYYY-MM-DD.md
  - editions/index.json
  - site/public/editions/YYYY-MM-DD.md
  - site/public/llms.txt
"""
from __future__ import annotations

import argparse
from concurrent.futures import ThreadPoolExecutor
import datetime as dt
import hashlib
import json
import mimetypes
import os
from pathlib import Path
import re
import sys
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request

from privacy_gate import line_has_private_content, load_extra_patterns
from receipt_stats import fetch_sanitized_provenance

NOTION_API_BASE = "https://api.notion.com/v1"
NOTION_VERSION = "2022-06-28"
USER_AGENT = "DailyBriefPublisher/1.0"
MAX_MEDIA_BYTES = 8 * 1024 * 1024

EXCLUDED_SECTION_PATTERNS = (
    re.compile(r"收藏与" + r"录音"),
    re.compile(r"\bfavorites\s*(?:&|and)\s*recordings?\b", re.I),
    re.compile(r"\bsaved\s*(?:items)?\s*(?:&|and)?\s*recordings?\b", re.I),
    re.compile(r"^\s*saved\s+items\s*$", re.I),
    re.compile(r"\bvoice\s*memos?\b", re.I),
    re.compile(r"\bapple\s+voice\s+memos\b", re.I),
    re.compile(r"\breading\s+(?:queue|inbox)\b", re.I),
    re.compile(r"\bshanghai\s+(?:events?|weekend)\b|上海" + r"周末", re.I),
    re.compile(r"\boutcome\s+check\b|本周结果", re.I),
    re.compile(r"\bexecution\s+audit\b", re.I),
)

WRAPPER_HEADING_PATTERNS = (
    re.compile(r"^\s*english\s*$", re.I),
    re.compile(r"^\s*daily\s+ai\s*&\s*developer\s+brief\b", re.I),
)

HISTORICAL_FEEDBACK_START_RE = re.compile(r"Historical\s+feedback\s*\(preserved\s+during\s+migration", re.I)
HISTORICAL_FEEDBACK_END_RE = re.compile(r"This\s+preserves\s+historical\s+feedback\s+only", re.I)
HISTORICAL_FEEDBACK_LINE_RE = re.compile(
    r"(?:Historical\s+feedback\s*\(preserved\s+during\s+migration|Original\s+author:|Original\s+comment,\s+unchanged:|English\s+translation:|Old-page\s+reference:|This\s+preserves\s+historical\s+feedback\s+only)",
    re.I,
)

_EXTRA_PRIVATE_PATTERNS = load_extra_patterns()

NOTION_URL_RE = re.compile(
    r"https?://(?:www\.|app\.)?(?:" + r"notion\.so|app\.notion\.com" + r")/[^\s)>\]\"]+",
    re.I,
)
NOTION_ID_RE = re.compile(r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})", re.I)

TAG_RULES: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("Frontier Models", ("gpt-", "claude ", "gemini", "qwen", "llama", "deepseek", "mistral", "kimi", "perplexity", "weights", "pretraining", "model spec", "reasoning")),
    ("Security & CVEs", ("cve-", "advisory", "rce", "vulnerability", "leak", "traversal", "exploit", "sandbox", "supply-chain", "credential", "escape", "full disk access", "backdoor", "patch")),
    ("Agent Systems", ("agent", "mcp", "codex", "claude code", "openclaw", "harness", "worktree", "orchestr", "browser-use", "subagent", "multi-agent")),
    ("Systems & Infra", ("kernel", "wasm", "compiler", "c++", "postgres", "gpu", "chip", "rubin", "tpu", "maia", "inference", "router", "storage", "linux", "rust", "offload")),
    ("Math & Research", ("arxiv", "proof", "lean", "riemann", "math", "alphaproof", "medchembench", "benchmark")),
    ("Business & Policy", ("ipo", "business", "policy", "copyright", "export control", "datacenter", "financing", "legislation", "public company")),
)

CONTENT_TYPE_EXT = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/jpg": ".jpg",
    "image/webp": ".webp",
    "image/gif": ".gif",
    "image/svg+xml": ".svg",
}


class NotionClient:
    def __init__(self, token: str) -> None:
        self.token = token.strip()

    def request_json(self, method: str, path: str, body: dict[str, Any] | None = None) -> dict[str, Any]:
        url = f"{NOTION_API_BASE}{path}"
        data = json.dumps(body).encode("utf-8") if body is not None else None
        headers = {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "User-Agent": USER_AGENT,
        }
        if data is not None:
            headers["Content-Type"] = "application/json"

        last_err: Exception | None = None
        for attempt in range(5):
            req = urllib.request.Request(url, data=data, headers=headers, method=method)
            try:
                with urllib.request.urlopen(req, timeout=30) as resp:
                    return json.loads(resp.read().decode("utf-8"))
            except urllib.error.HTTPError as exc:
                last_err = exc
                if exc.code == 429:
                    retry_after = float(exc.headers.get("Retry-After") or (1.5 * (attempt + 1)))
                    time.sleep(min(retry_after, 15.0))
                    continue
                if exc.code in {500, 502, 503, 504} and attempt < 4:
                    time.sleep(1.2 * (attempt + 1))
                    continue
                detail = exc.read().decode("utf-8", "replace")[:500]
                raise RuntimeError(f"Notion API {method} {path} HTTP {exc.code}: {detail}") from exc
            except (urllib.error.URLError, TimeoutError) as exc:
                last_err = exc
                if attempt < 4:
                    time.sleep(1.5 * (attempt + 1))
                    continue
                raise
        raise last_err or RuntimeError(f"Notion API request failed: {path}")

    def query_database(self, database_id: str, recent_days: int | None = None) -> list[dict[str, Any]]:
        results: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            payload: dict[str, Any] = {
                "page_size": 100,
                "sorts": [{"property": "Date", "direction": "descending"}],
            }
            if recent_days is not None:
                cutoff = (dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date() - dt.timedelta(days=recent_days)).isoformat()
                payload["filter"] = {
                    "and": [
                        {"property": "Appendix", "checkbox": {"equals": False}},
                        {"property": "Date", "date": {"on_or_after": cutoff}},
                    ]
                }
            if cursor:
                payload["start_cursor"] = cursor
            res = self.request_json("POST", f"/databases/{database_id}/query", payload)
            results.extend(res.get("results", []))
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
        return results

    def list_block_children(self, block_id: str) -> list[dict[str, Any]]:
        children: list[dict[str, Any]] = []
        cursor: str | None = None
        while True:
            qs = "?page_size=100" + (f"&start_cursor={urllib.parse.quote(cursor)}" if cursor else "")
            res = self.request_json("GET", f"/blocks/{block_id}/children{qs}")
            batch = res.get("results", [])
            for block in batch:
                if block.get("has_children"):
                    block["_children"] = self.list_block_children(block["id"])
                else:
                    block["_children"] = []
                children.append(block)
            if not res.get("has_more"):
                break
            cursor = res.get("next_cursor")
        return children


def normalize_notion_id(value: str) -> str | None:
    match = NOTION_ID_RE.search(value)
    if not match:
        return None
    return match.group(1).replace("-", "").lower()


def clean_heading_text(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


def is_excluded_section(heading_text: str) -> bool:
    cleaned = clean_heading_text(heading_text)
    return any(p.search(cleaned) for p in EXCLUDED_SECTION_PATTERNS)


def is_wrapper_heading(heading_text: str) -> bool:
    cleaned = clean_heading_text(heading_text)
    return any(p.search(cleaned) for p in WRAPPER_HEADING_PATTERNS)


def should_drop_line(line: str) -> bool:
    return line_has_private_content(line, _EXTRA_PRIVATE_PATTERNS) or bool(HISTORICAL_FEEDBACK_LINE_RE.search(line))


def sanitize_url_or_text(url: str, page_date_map: dict[str, str]) -> str | None:
    """Return safe public URL, mapped /issues/<date>/ URL, or None if private."""
    if NOTION_URL_RE.search(url):
        pid = normalize_notion_id(url)
        if pid and pid in page_date_map:
            return f"/issues/{page_date_map[pid]}/"
        return None
    if url.startswith(("https://", "http://", "/media/", "/issues/")):
        return url
    return None


def render_rich_text(
    rich_text: list[dict[str, Any]],
    page_date_map: dict[str, str],
    *,
    allow_formatting: bool = True,
) -> str:
    parts: list[str] = []
    for seg in rich_text:
        kind = seg.get("type")
        if kind == "equation":
            text = seg.get("equation", {}).get("expression", "")
        elif kind == "mention":
            mention = seg.get("mention", {})
            mtype = mention.get("type")
            if mtype == "date":
                d = mention.get("date", {})
                start = d.get("start", "")
                end = d.get("end")
                text = f"{start} to {end}" if end else start
            elif mtype in {"page", "link_mention"}:
                text = seg.get("plain_text", "")
            else:
                text = seg.get("plain_text", "")
        else:
            text = seg.get("plain_text", "")

        if not text:
            continue

        href = seg.get("href") or (seg.get("text", {}).get("link") or {}).get("url")
        if href:
            safe_href = sanitize_url_or_text(href, page_date_map)
            if safe_href:
                href = safe_href
            else:
                # Private Notion link not mapped to a public edition -> strip link target.
                href = None

        ann = seg.get("annotations") or {}
        if not allow_formatting:
            parts.append(f"[{text}]({href})" if href else text)
            continue

        lead = text[: len(text) - len(text.lstrip())]
        trail = text[len(text.rstrip()) :]
        core = text.strip()
        if not core:
            parts.append(text)
            continue

        if ann.get("code"):
            formatted = f"`{core}`"
        else:
            formatted = core
            if ann.get("bold"):
                formatted = f"**{formatted}**"
            if ann.get("italic"):
                formatted = f"*{formatted}*"
            if ann.get("strikethrough"):
                formatted = f"~~{formatted}~~"

        if href:
            formatted = f"[{formatted}]({href})"
        parts.append(f"{lead}{formatted}{trail}")
    return "".join(parts)


def localize_media_file(
    source_url: str,
    report_date: str,
    media_dir: Path,
    *,
    fetcher: Any = None,
) -> str | None:
    """Download expiring Notion S3 file locally into site/public/media/<date>/."""
    try:
        if fetcher is not None:
            data, content_type = fetcher(source_url)
        else:
            req = urllib.request.Request(source_url, headers={"User-Agent": USER_AGENT})
            with urllib.request.urlopen(req, timeout=30) as resp:
                content_type = (resp.headers.get("Content-Type") or "").split(";")[0].strip().lower()
                data = resp.read(MAX_MEDIA_BYTES + 1)
        if len(data) > MAX_MEDIA_BYTES or not data:
            return None
    except Exception:
        return None

    ext = CONTENT_TYPE_EXT.get(content_type)
    if not ext:
        parsed_path = urllib.parse.urlsplit(source_url).path
        guess = Path(parsed_path).suffix.lower()
        ext = guess if guess in {".png", ".jpg", ".jpeg", ".webp", ".gif", ".svg"} else None
    if not ext:
        guessed, _ = mimetypes.guess_type(source_url)
        ext = CONTENT_TYPE_EXT.get(guessed or "")
    if not ext:
        return None

    digest = hashlib.sha256(data).hexdigest()[:12]
    rel_path = f"/media/{report_date}/{digest}{ext}"
    out_path = media_dir / report_date / f"{digest}{ext}"
    out_path.parent.mkdir(parents=True, exist_ok=True)
    if not out_path.exists():
        out_path.write_bytes(data)
    return rel_path


def _heading_level(block: dict[str, Any]) -> int | None:
    btype = block.get("type")
    if btype == "heading_1":
        return 1
    if btype == "heading_2":
        return 2
    if btype == "heading_3":
        return 3
    return None


def _compute_heading_shift(blocks: list[dict[str, Any]], page_date_map: dict[str, str]) -> int:
    levels = []
    for b in blocks:
        lvl = _heading_level(b)
        if lvl is None:
            continue
        btype = b["type"]
        title = clean_heading_text(render_rich_text(b[btype].get("rich_text", []), page_date_map, allow_formatting=False))
        if is_wrapper_heading(title) or is_excluded_section(title):
            continue
        levels.append(lvl)
    if not levels:
        return 0
    min_level = min(levels)
    # Normalize top-level editorial sections to H2 (##).
    return 2 - min_level


def _render_block_list(
    blocks: list[dict[str, Any]],
    *,
    report_date: str,
    page_date_map: dict[str, str],
    media_dir: Path,
    heading_shift: int,
    indent: int = 0,
    media_fetcher: Any = None,
) -> list[str]:
    lines: list[str] = []
    skip_until_level: int | None = None
    in_historical_feedback = False
    numbered_counter = 0

    for block in blocks:
        btype = block.get("type", "")
        lvl = _heading_level(block)
        if lvl is not None:
            in_historical_feedback = False
            data = block[btype]
            title = clean_heading_text(render_rich_text(data.get("rich_text", []), page_date_map, allow_formatting=False))
            if is_wrapper_heading(title):
                if block.get("_children"):
                    lines.extend(
                        _render_block_list(
                            block["_children"],
                            report_date=report_date,
                            page_date_map=page_date_map,
                            media_dir=media_dir,
                            heading_shift=heading_shift,
                            indent=indent,
                            media_fetcher=media_fetcher,
                        )
                    )
                continue

            norm_lvl = max(2, min(4, lvl + heading_shift))

            if skip_until_level is not None and norm_lvl <= skip_until_level:
                skip_until_level = None

            if is_excluded_section(title):
                skip_until_level = norm_lvl
                continue

            if skip_until_level is not None:
                continue

            numbered_counter = 0
            lines.append("")
            lines.append(f"{'#' * norm_lvl} {title}")
            lines.append("")
            if block.get("_children"):
                lines.extend(
                    _render_block_list(
                        block["_children"],
                        report_date=report_date,
                        page_date_map=page_date_map,
                        media_dir=media_dir,
                        heading_shift=heading_shift,
                        indent=indent,
                        media_fetcher=media_fetcher,
                    )
                )
            continue

        if skip_until_level is not None:
            continue

        # Check block raw text for migrated historical feedback spans
        if btype in {"paragraph", "callout", "quote"}:
            raw_plain = "".join(seg.get("plain_text", "") for seg in block[btype].get("rich_text", []))
            if HISTORICAL_FEEDBACK_START_RE.search(raw_plain):
                if not HISTORICAL_FEEDBACK_END_RE.search(raw_plain):
                    in_historical_feedback = True
                continue
            if in_historical_feedback:
                if HISTORICAL_FEEDBACK_END_RE.search(raw_plain):
                    in_historical_feedback = False
                continue

        if btype != "numbered_list_item":
            numbered_counter = 0

        prefix = "  " * indent

        if btype == "paragraph":
            text = render_rich_text(block["paragraph"].get("rich_text", []), page_date_map).strip()
            if text and not should_drop_line(text):
                lines.append(f"{prefix}{text}")
                lines.append("")
            elif block.get("_children") and text and not should_drop_line(text):
                pass
            if block.get("_children") and not should_drop_line(text):
                lines.extend(
                    _render_block_list(
                        block["_children"],
                        report_date=report_date,
                        page_date_map=page_date_map,
                        media_dir=media_dir,
                        heading_shift=heading_shift,
                        indent=indent,
                        media_fetcher=media_fetcher,
                    )
                )

        elif btype == "bulleted_list_item":
            text = render_rich_text(block["bulleted_list_item"].get("rich_text", []), page_date_map).strip()
            if text and not should_drop_line(text):
                lines.append(f"{prefix}- {text}")
                if block.get("_children"):
                    lines.extend(
                        _render_block_list(
                            block["_children"],
                            report_date=report_date,
                            page_date_map=page_date_map,
                            media_dir=media_dir,
                            heading_shift=heading_shift,
                            indent=indent + 1,
                            media_fetcher=media_fetcher,
                        )
                    )
                if indent == 0:
                    lines.append("")

        elif btype == "numbered_list_item":
            numbered_counter += 1
            text = render_rich_text(block["numbered_list_item"].get("rich_text", []), page_date_map).strip()
            if text and not should_drop_line(text):
                lines.append(f"{prefix}{numbered_counter}. {text}")
                if block.get("_children"):
                    lines.extend(
                        _render_block_list(
                            block["_children"],
                            report_date=report_date,
                            page_date_map=page_date_map,
                            media_dir=media_dir,
                            heading_shift=heading_shift,
                            indent=indent + 1,
                            media_fetcher=media_fetcher,
                        )
                    )
                if indent == 0:
                    lines.append("")

        elif btype == "to_do":
            # Drop legacy 👍 [Useful] checkboxes and any private controls completely.
            text = render_rich_text(block["to_do"].get("rich_text", []), page_date_map).strip()
            if "👍" in text or "Useful" in text or should_drop_line(text):
                continue
            checked = "x" if block["to_do"].get("checked") else " "
            if text:
                lines.append(f"{prefix}- [{checked}] {text}")
                if indent == 0:
                    lines.append("")

        elif btype == "quote":
            text = render_rich_text(block["quote"].get("rich_text", []), page_date_map).strip()
            if text and not should_drop_line(text):
                for qline in text.splitlines():
                    lines.append(f"{prefix}> {qline}")
                lines.append("")

        elif btype == "callout":
            text = render_rich_text(block["callout"].get("rich_text", []), page_date_map).strip()
            if text and not should_drop_line(text):
                lines.append(f"{prefix}> {text}")
                if block.get("_children"):
                    child_lines = _render_block_list(
                        block["_children"],
                        report_date=report_date,
                        page_date_map=page_date_map,
                        media_dir=media_dir,
                        heading_shift=heading_shift,
                        indent=0,
                        media_fetcher=media_fetcher,
                    )
                    for cl in child_lines:
                        if cl.strip():
                            lines.append(f"{prefix}> {cl}")
                lines.append("")

        elif btype == "code":
            lang = (block["code"].get("language") or "").strip()
            code_txt = "".join(seg.get("plain_text", "") for seg in block["code"].get("rich_text", []))
            if code_txt and not should_drop_line(code_txt):
                lines.append(f"```{lang}")
                lines.append(code_txt.rstrip())
                lines.append("```")
                lines.append("")

        elif btype == "divider":
            lines.append("---")
            lines.append("")

        elif btype == "toggle":
            summary = render_rich_text(block["toggle"].get("rich_text", []), page_date_map, allow_formatting=False).strip()
            if not summary or is_excluded_section(summary) or should_drop_line(summary):
                continue
            child_lines = _render_block_list(
                block.get("_children", []),
                report_date=report_date,
                page_date_map=page_date_map,
                media_dir=media_dir,
                heading_shift=heading_shift,
                indent=0,
                media_fetcher=media_fetcher,
            )
            inner = "\n".join(child_lines).strip()
            if inner:
                lines.append("<details>")
                lines.append(f"<summary>{summary}</summary>")
                lines.append("")
                lines.append(inner)
                lines.append("")
                lines.append("</details>")
                lines.append("")

        elif btype == "bookmark":
            url = (block["bookmark"].get("url") or "").strip()
            safe_url = sanitize_url_or_text(url, page_date_map)
            caption = render_rich_text(block["bookmark"].get("caption", []), page_date_map, allow_formatting=False).strip()
            if safe_url and not should_drop_line(caption):
                label = caption or urllib.parse.urlsplit(safe_url).netloc
                lines.append(f"> **Source:** [{label}]({safe_url})")
                lines.append("")

        elif btype == "image":
            img = block["image"]
            caption = render_rich_text(img.get("caption", []), page_date_map, allow_formatting=False).strip()
            if should_drop_line(caption):
                caption = ""
            img_type = img.get("type")
            final_url: str | None = None
            if img_type == "external":
                candidate = (img.get("external") or {}).get("url", "").strip()
                final_url = sanitize_url_or_text(candidate, page_date_map)
            elif img_type == "file":
                raw_url = (img.get("file") or {}).get("url", "").strip()
                if raw_url:
                    final_url = localize_media_file(raw_url, report_date, media_dir, fetcher=media_fetcher)
            if final_url:
                safe_alt = caption.replace("[", "(").replace("]", ")") or "Edition illustration"
                lines.append(f"![{safe_alt}]({final_url})")
                lines.append("")

        elif btype in {"video", "pdf", "embed"}:
            media = block[btype]
            if media.get("type") == "external":
                url = (media.get("external") or {}).get("url", "").strip()
                safe_url = sanitize_url_or_text(url, page_date_map)
                if safe_url:
                    caption = render_rich_text(media.get("caption", []), page_date_map, allow_formatting=False).strip()
                    label = caption or f"{btype.upper()} reference"
                    lines.append(f"> **Media ({btype.upper()}):** [{label}]({safe_url})")
                    lines.append("")
            elif btype == "embed":
                url = (media.get("url") or "").strip()
                safe_url = sanitize_url_or_text(url, page_date_map)
                if safe_url:
                    lines.append(f"> **Embed:** [{urllib.parse.urlsplit(safe_url).netloc or 'Link'}]({safe_url})")
                    lines.append("")

        elif btype == "table":
            rows = block.get("_children", [])
            rendered_rows: list[list[str]] = []
            for r in rows:
                if r.get("type") != "table_row":
                    continue
                cells = [
                    render_rich_text(cell, page_date_map).replace("|", "\\|").replace("\n", " ").strip()
                    for cell in r["table_row"].get("cells", [])
                ]
                if any(should_drop_line(c) for c in cells):
                    continue
                rendered_rows.append(cells)
            if rendered_rows:
                width = max(len(r) for r in rendered_rows)
                norm = [r + [""] * (width - len(r)) for r in rendered_rows]
                lines.append("| " + " | ".join(norm[0]) + " |")
                lines.append("| " + " | ".join("---" for _ in range(width)) + " |")
                for r in norm[1:]:
                    lines.append("| " + " | ".join(r) + " |")
                lines.append("")

        elif btype in {"column_list", "column", "synced_block"} and block.get("_children"):
            lines.extend(
                _render_block_list(
                    block["_children"],
                    report_date=report_date,
                    page_date_map=page_date_map,
                    media_dir=media_dir,
                    heading_shift=heading_shift,
                    indent=indent,
                    media_fetcher=media_fetcher,
                )
            )

    return lines


def _strip_markdown(text: str) -> str:
    text = re.sub(r"!\[([^\]]*)\]\([^)]+\)", "", text)
    text = re.sub(r"\[([^\]]+)\]\(([^)]+)\)", r"\1", text)
    text = re.sub(r"`([^`]+)`", r"\1", text)
    text = re.sub(r"\*\*([^*]+)\*\*", r"\1", text)
    text = re.sub(r"\*([^*]+)\*", r"\1", text)
    return re.sub(r"\s+", " ", text).strip()


def extract_metadata(markdown_body: str, title: str, report_date: str) -> dict[str, Any]:
    lines = markdown_body.splitlines()
    window = ""
    filtered_lines: list[str] = []
    found_first_section = False
    in_callout_brief = False
    for line in lines:
        # Normalize opening summary heading to H2 (##) even if wrapped earlier
        if re.match(r"^###\s+(?:Today in 90 seconds|In 90 seconds|In 30 seconds|30-second summary|Key points|Today at a glance|Highlights|Summary)\s*$", line, re.I):
            line = line[1:]
        if not found_first_section and re.match(r"^>\s*\*\*(?:The\s+)?90-second\s+(?:brief|overview)\*\*\s*$", line, re.I):
            in_callout_brief = True
            found_first_section = True
            filtered_lines.append("## Today in 90 seconds")
            filtered_lines.append("")
            continue
        if in_callout_brief:
            if line.startswith(">"):
                unquoted = re.sub(r"^>\s?", "", line)
                if unquoted.strip():
                    filtered_lines.append(unquoted)
                continue
            in_callout_brief = False
        if line.startswith("## "):
            found_first_section = True
        if not found_first_section and not window and re.search(r"(window|报告窗口|时间窗)", line, re.I):
            cleaned = _strip_markdown(line).lstrip("> ").strip()
            cleaned = re.sub(r"^(?:report\s+)?(?:news\s+)?(?:reporting\s+)?window:\s*", "", cleaned, flags=re.I).strip()
            window = cleaned
            continue
        if not found_first_section and re.match(r"^(?:\*\*)?(?:Project discovery|报道窗口|Daily AI & Developer Brief\s*[—|-])", line, re.I):
            continue
        filtered_lines.append(line)

    body = "\n".join(filtered_lines).strip() + "\n"
    body = re.sub(r"\n{3,}", "\n\n", body)
    # Remove any lingering private Notion URLs defensively
    body = NOTION_URL_RE.sub("", body)

    sections = [m.group(1).strip() for m in re.finditer(r"^##\s+(.+)$", body, re.M)]
    story_count = len(re.findall(r"^###\s+", body, re.M))

    # Extract 90-second takeaways from first summary section
    takeaways: list[str] = []
    first_section_match = re.search(
        r"^##\s+(?:Today in 90 seconds|In 90 seconds|In 30 seconds|30-second summary|Key points|Today at a glance|Highlights|Summary)\s*\n+(.*?)(?=^##\s+|\Z)",
        body,
        re.M | re.S | re.I,
    )
    if first_section_match:
        sec_text = first_section_match.group(1)
        for m in re.finditer(r"^(?:-|\d+\.)\s+(.+)$", sec_text, re.M):
            cleaned = _strip_markdown(m.group(1))
            if cleaned:
                takeaways.append(cleaned)
            if len(takeaways) >= 4:
                break

    if not takeaways:
        for para in re.split(r"\n\n+", body):
            p = para.strip()
            if p and not p.startswith(("#", ">", "!", "```", "<details", "---")):
                takeaways.append(_strip_markdown(p)[:240])
                break

    summary = takeaways[0] if takeaways else title
    if len(summary) > 260:
        summary = summary[:257].rstrip() + "..."

    # Lead image (prefer non-SVG raster/clear architecture image if available, else first valid image)
    lead_image: dict[str, str] | None = None
    for m in re.finditer(r"!\[([^\]]*)\]\(([^)]+)\)", body):
        alt, src = m.group(1).strip(), m.group(2).strip()
        if src.startswith(("/media/", "https://", "http://")):
            lead_image = {"src": src, "alt": alt or title}
            break

    # Auto-classify topic tags from title and ### story headlines (not generic section headers)
    story_headings = re.findall(r"^###\s+(.+)$", body, re.M)
    haystack = (title + "\n" + "\n".join(story_headings)).lower()
    tags: list[str] = []
    for tag, keywords in TAG_RULES:
        if any(kw in haystack for kw in keywords):
            tags.append(tag)
    if not tags:
        tags = ["Agent Systems", "Systems & Infra"]
    tags = tags[:4]

    source_urls = {
        u.rstrip(").,;\"'")
        for u in re.findall(r"https?://[^\s)>\]\"]+", body)
    }
    word_count = len(re.findall(r"\S+", _strip_markdown(body)))
    reading_time_min = max(1, round(word_count / 230))

    return {
        "body": body,
        "window": window or f"Asia/Shanghai [previous day 04:00, {report_date} 04:00)",
        "summary": summary,
        "takeaways": takeaways,
        "sections": sections,
        "tags": tags,
        "lead_image": lead_image,
        "story_count": story_count,
        "source_count": len(source_urls),
        "reading_time_min": reading_time_min,
    }


def dump_yaml_frontmatter(meta: dict[str, Any]) -> str:
    lines = ["---"]
    ordered_keys = [
        "issue_number",
        "date",
        "title",
        "window",
        "summary",
        "takeaways",
        "sections",
        "tags",
        "lead_image",
        "story_count",
        "source_count",
        "reading_time_min",
        "provenance",
    ]
    for key in ordered_keys:
        if key not in meta or meta[key] is None:
            continue
        val = meta[key]
        lines.append(f"{key}: {json.dumps(val, ensure_ascii=False)}")
    lines.append("---")
    return "\n".join(lines) + "\n\n"


def convert_notion_page(
    *,
    page: dict[str, Any],
    blocks: list[dict[str, Any]],
    page_date_map: dict[str, str],
    media_dir: Path,
    issue_number: int = 1,
    provenance: dict[str, Any] | None = None,
    media_fetcher: Any = None,
) -> tuple[dict[str, Any], str]:
    props = page.get("properties", {})
    report_date = (props.get("Date") or {}).get("date", {}).get("start", "").strip()
    title_segs = (props.get("Topic") or {}).get("title", [])
    title = "".join(seg.get("plain_text", "") for seg in title_segs).strip() or f"Daily Brief — {report_date}"

    heading_shift = _compute_heading_shift(blocks, page_date_map)
    raw_lines = _render_block_list(
        blocks,
        report_date=report_date,
        page_date_map=page_date_map,
        media_dir=media_dir,
        heading_shift=heading_shift,
        media_fetcher=media_fetcher,
    )
    raw_md = "\n".join(raw_lines)
    extracted = extract_metadata(raw_md, title, report_date)

    meta: dict[str, Any] = {
        "issue_number": issue_number,
        "date": report_date,
        "title": title,
        "window": extracted["window"],
        "summary": extracted["summary"],
        "takeaways": extracted["takeaways"],
        "sections": extracted["sections"],
        "tags": extracted["tags"],
        "lead_image": extracted["lead_image"],
        "story_count": extracted["story_count"],
        "source_count": extracted["source_count"],
        "reading_time_min": extracted["reading_time_min"],
        "provenance": provenance,
    }
    full_markdown = dump_yaml_frontmatter(meta) + extracted["body"]
    return meta, full_markdown


def load_frontmatter_only(path: Path) -> dict[str, Any] | None:
    text = path.read_text(encoding="utf-8")
    if not text.startswith("---\n"):
        return None
    end = text.find("\n---\n", 4)
    if end == -1:
        return None
    meta: dict[str, Any] = {}
    for line in text[4:end].splitlines():
        if ":" not in line:
            continue
        key, raw_val = line.split(":", 1)
        key = key.strip()
        raw_val = raw_val.strip()
        try:
            meta[key] = json.loads(raw_val)
        except Exception:
            meta[key] = raw_val
    return meta


def rebuild_indexes(
    *,
    editions_dir: Path,
    public_editions_dir: Path,
    llms_txt_path: Path,
    site_base_url: str,
) -> list[dict[str, Any]]:
    edition_files = sorted(editions_dir.glob("????-??-??.md"))
    records: list[tuple[Path, dict[str, Any], str]] = []
    for f in edition_files:
        text = f.read_text(encoding="utf-8")
        meta = load_frontmatter_only(f)
        if not meta or not meta.get("date"):
            continue
        end = text.find("\n---\n", 4)
        body = text[end + 5 :] if end != -1 else text
        records.append((f, meta, body))

    # Assign sequential issue_number chronologically (1..N) and rewrite only if changed
    records.sort(key=lambda row: str(row[1]["date"]))
    index_entries: list[dict[str, Any]] = []
    public_editions_dir.mkdir(parents=True, exist_ok=True)

    for idx, (path, meta, body) in enumerate(records, 1):
        if meta.get("issue_number") != idx:
            meta["issue_number"] = idx
            updated = dump_yaml_frontmatter(meta) + body.lstrip("\n")
            path.write_text(updated, encoding="utf-8")
            text_for_public = updated
        else:
            text_for_public = path.read_text(encoding="utf-8")

        pub_path = public_editions_dir / path.name
        if not pub_path.exists() or pub_path.read_text(encoding="utf-8") != text_for_public:
            pub_path.write_text(text_for_public, encoding="utf-8")

        index_entries.append(
            {
                "issue_number": idx,
                "date": meta["date"],
                "title": meta["title"],
                "window": meta.get("window", ""),
                "summary": meta.get("summary", ""),
                "takeaways": meta.get("takeaways", []),
                "sections": meta.get("sections", []),
                "tags": meta.get("tags", []),
                "lead_image": meta.get("lead_image"),
                "story_count": meta.get("story_count", 0),
                "source_count": meta.get("source_count", 0),
                "reading_time_min": meta.get("reading_time_min", 1),
                "provenance": meta.get("provenance"),
            }
        )

    index_desc = list(reversed(index_entries))
    index_path = editions_dir / "index.json"
    index_path.write_text(json.dumps(index_desc, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    base = site_base_url.rstrip("/")
    llms_lines = [
        "# Daily Brief",
        "",
        "> Daily AI, security, and open-source developer-tools briefing with commit-pinned collector inputs, verified primary sources, and explicit coverage notes.",
        "",
        "## Feeds & Endpoints",
        f"- RSS 2.0: {base}/rss.xml",
        f"- JSON Feed 1.1: {base}/feed.json",
        f"- Archive index: {base}/archive/",
        f"- How it works & SpecPack: {base}/how-it-works/",
        "",
        "## Recent Editions (Markdown)",
    ]
    for entry in index_desc[:14]:
        llms_lines.append(
            f"- [{entry['date']} — {entry['title']}]({base}/editions/{entry['date']}.md): {entry['summary']}"
        )
    llms_txt_path.parent.mkdir(parents=True, exist_ok=True)
    llms_txt_path.write_text("\n".join(llms_lines).rstrip() + "\n", encoding="utf-8")
    return index_desc


def load_config(config_path: Path | None) -> dict[str, Any]:
    cfg: dict[str, Any] = {}
    if config_path and config_path.exists():
        cfg = json.loads(config_path.read_text(encoding="utf-8"))

    token = os.environ.get("NOTION_TOKEN")
    if not token:
        token_file = Path(os.path.expanduser(cfg.get("notion_token_file") or os.environ.get("NOTION_TOKEN_FILE", "~/.config/notion-trash/token")))
        if not token_file.exists():
            raise RuntimeError(f"Notion token file not found: {token_file}")
        token = token_file.read_text(encoding="utf-8").strip()

    db_id = os.environ.get("NOTION_DATABASE_ID") or cfg.get("notion_database_id")
    if not db_id:
        raise RuntimeError("notion_database_id or NOTION_DATABASE_ID is required")

    repo_path = Path(os.path.expanduser(cfg.get("repo_path") or ".")).resolve()
    return {
        "token": token,
        "database_id": db_id,
        "receipts_repo": os.environ.get("RECEIPTS_REPO") or cfg.get("receipts_repo", ""),
        "repo_path": repo_path,
        "editions_dir": repo_path / cfg.get("editions_dir", "editions"),
        "public_editions_dir": repo_path / cfg.get("public_editions_dir", "site/public/editions"),
        "media_dir": repo_path / cfg.get("media_dir", "site/public/media"),
        "llms_txt_path": repo_path / cfg.get("llms_txt_path", "site/public/llms.txt"),
        "site_base_url": cfg.get("site_base_url", "https://daily-brief.pages.dev"),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Export sanitized Daily Brief editions from Notion to Markdown.")
    parser.add_argument("--config", type=Path, help="Path to private publisher config JSON")
    parser.add_argument("--backfill", action="store_true", help="Export all non-appendix editions in the database")
    parser.add_argument("--recent-days", type=int, default=3, help="Export editions within the last N days (default: 3)")
    args = parser.parse_args()

    cfg_path = args.config or (Path(os.environ["DAILY_BRIEF_PUBLISHER_CONFIG"]) if "DAILY_BRIEF_PUBLISHER_CONFIG" in os.environ else None)
    cfg = load_config(cfg_path)
    client = NotionClient(cfg["token"])

    # Always query full DB metadata once so cross-edition Notion links map accurately to /issues/YYYY-MM-DD/.
    all_pages = client.query_database(cfg["database_id"], recent_days=None)
    non_appendix = []
    page_date_map: dict[str, str] = {}
    for p in all_pages:
        props = p.get("properties", {})
        if props.get("Appendix", {}).get("checkbox"):
            continue
        date_val = (props.get("Date") or {}).get("date", {}).get("start", "").strip()
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", date_val):
            continue
        pid = normalize_notion_id(p["id"])
        if pid:
            page_date_map[pid] = date_val
        non_appendix.append((date_val, p))

    if not args.backfill:
        cutoff = (dt.datetime.now(dt.timezone(dt.timedelta(hours=8))).date() - dt.timedelta(days=args.recent_days)).isoformat()
        targets = [(d, p) for d, p in non_appendix if d >= cutoff]
    else:
        targets = non_appendix

    targets.sort(key=lambda row: row[0])
    editions_dir: Path = cfg["editions_dir"]
    editions_dir.mkdir(parents=True, exist_ok=True)
    media_dir: Path = cfg["media_dir"]

    def process_one(row: tuple[str, dict[str, Any]]) -> str:
        date_val, page = row
        blocks = client.list_block_children(page["id"])
        prov = fetch_sanitized_provenance(cfg["receipts_repo"], date_val) if cfg["receipts_repo"] else None
        _, markdown = convert_notion_page(
            page=page,
            blocks=blocks,
            page_date_map=page_date_map,
            media_dir=media_dir,
            issue_number=1,
            provenance=prov,
        )
        out_path = editions_dir / f"{date_val}.md"
        if not out_path.exists() or out_path.read_text(encoding="utf-8") != markdown:
            out_path.write_text(markdown, encoding="utf-8")
            return f"wrote {date_val}"
        return f"unchanged {date_val}"

    statuses: list[str] = []
    with ThreadPoolExecutor(max_workers=3) as pool:
        for res in pool.map(process_one, targets):
            statuses.append(res)

    index = rebuild_indexes(
        editions_dir=editions_dir,
        public_editions_dir=cfg["public_editions_dir"],
        llms_txt_path=cfg["llms_txt_path"],
        site_base_url=cfg["site_base_url"],
    )
    print(json.dumps({"exported": len(targets), "total_editions": len(index), "details": statuses[:10]}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    sys.exit(main())

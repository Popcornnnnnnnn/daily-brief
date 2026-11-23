#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import fcntl
import gzip
import hashlib
import html
from html.parser import HTMLParser
import json
import os
from pathlib import Path
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from typing import Any
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

ROOT = Path(__file__).resolve().parent
sys.path.insert(0, str(ROOT.parent))
from brief_workflow import write_input_manifest, publish_inputs, sync_bridge, push_bridge
CONFIG_PATH = ROOT / "config.json"
STATE_DIR = ROOT / "state"
RAW_DIR = STATE_DIR / "raw"
DB_PATH = STATE_DIR / "collector.sqlite"
EXPORT_DIR = ROOT / "exports"
BRIDGE_REPO = Path(os.environ.get("DAILY_BRIEF_BRIDGE_REPO", str(ROOT / "bridge-repo")))
BRIDGE_LOCK = Path(os.environ.get("DAILY_BRIEF_BRIDGE_LOCK", str(ROOT.parent / "github-bridge.lock")))
LATEST_MD = EXPORT_DIR / "Tech AI Local Collector - Latest.md"
LATEST_JSON = EXPORT_DIR / "Tech AI Local Collector - Latest.json"
USER_AGENT = "TechAICollector/1.0 (local evidence collector)"
GH = Path(os.environ.get("GH_BIN") or shutil.which("gh") or "/opt/homebrew/bin/gh")
GIT = shutil.which("git") or "/usr/bin/git"
TARGET_ARXIV_CATEGORIES = {"cs.AI", "cs.CL", "cs.LG", "cs.SE", "cs.CR", "cs.HC"}


def now_utc() -> dt.datetime:
    return dt.datetime.now(dt.timezone.utc)


def iso(value: dt.datetime | None) -> str | None:
    return value.astimezone(dt.timezone.utc).isoformat().replace("+00:00", "Z") if value else None


def parse_date(value: str | None) -> tuple[str | None, str]:
    if not value:
        return None, "unknown"
    value = value.strip()
    try:
        parsed = email.utils.parsedate_to_datetime(value)
        if parsed:
            if parsed.tzinfo is None:
                parsed = parsed.replace(tzinfo=dt.timezone.utc)
            return iso(parsed), "datetime"
    except (TypeError, ValueError, OverflowError):
        pass
    normalized = value.replace("Z", "+00:00")
    try:
        parsed = dt.datetime.fromisoformat(normalized)
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        precision = "date" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else "datetime"
        return iso(parsed), precision
    except ValueError:
        return None, "unknown"


def clean_text(value: str | None, limit: int = 1400) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", html.unescape(value))
    value = re.sub(r"\s+", " ", value).strip()
    return value[:limit]


def canonical_url(value: str) -> str:
    try:
        parts = urllib.parse.urlsplit(value)
        query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
        query = [(k, v) for k, v in query if not k.lower().startswith("utm_") and k.lower() not in {"ref", "source", "fbclid", "gclid"}]
        path = re.sub(r"/{2,}", "/", parts.path)
        return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), path.rstrip("/") or "/", urllib.parse.urlencode(query), ""))
    except Exception:
        return value


class LinkParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.links: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag == "a":
            self._href = dict(attrs).get("href")
            self._text = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag == "a" and self._href is not None:
            self.links.append((self._href, clean_text(" ".join(self._text), 300)))
            self._href = None
            self._text = []


def db_connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        PRAGMA journal_mode=WAL;
        CREATE TABLE IF NOT EXISTS runs (
          run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
          status TEXT NOT NULL, item_count INTEGER NOT NULL DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS sources (
          source_id TEXT PRIMARY KEY, name TEXT NOT NULL, family TEXT NOT NULL,
          source_type TEXT NOT NULL, last_status TEXT, last_checked_at TEXT,
          last_success_at TEXT, last_error TEXT, last_method TEXT
        );
        CREATE TABLE IF NOT EXISTS coverage (
          run_id TEXT NOT NULL, source_id TEXT NOT NULL, status TEXT NOT NULL,
          method TEXT, item_count INTEGER NOT NULL DEFAULT 0, detail TEXT,
          PRIMARY KEY (run_id, source_id)
        );
        CREATE TABLE IF NOT EXISTS items (
          item_key TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_item_id TEXT,
          title TEXT NOT NULL, url TEXT NOT NULL, canonical_url TEXT NOT NULL,
          published_at TEXT, published_precision TEXT NOT NULL,
          first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL,
          author TEXT, summary TEXT, raw_run_id TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS idx_items_published ON items(published_at);
        CREATE INDEX IF NOT EXISTS idx_items_seen ON items(first_seen_at);
        CREATE INDEX IF NOT EXISTS idx_items_source ON items(source_id);
        """
    )
    return conn


def http_get(url: str, timeout: int, accept: str = "application/json, application/atom+xml, application/rss+xml, text/xml, text/html;q=0.8, */*;q=0.5") -> tuple[bytes, str, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": accept})
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(8 * 1024 * 1024), response.headers.get("content-type", ""), response.geturl()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = exc
            retryable = isinstance(exc, TimeoutError) or getattr(exc, "code", 0) in {408, 429, 500, 502, 503, 504}
            if attempt == 0 and retryable:
                time.sleep(2)
                continue
            raise
    raise last_error or RuntimeError("request failed")


def gh_api_paginated(endpoint: str, fields: dict[str, str]) -> bytes:
    command = [str(GH), "api", "--paginate", "--slurp", endpoint, "-X", "GET"]
    for key, value in fields.items():
        command += ["-f", f"{key}={value}"]
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            return subprocess.run(command, check=True, capture_output=True, timeout=180).stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    raise last_error or RuntimeError(f"paginated gh api failed for {endpoint}")


def element_text(element: ET.Element, names: set[str]) -> str:
    for child in element.iter():
        if child.tag.split("}")[-1] in names and child.text:
            return child.text.strip()
    return ""


def parse_feed(data: bytes, base_url: str) -> list[dict[str, Any]]:
    root = ET.fromstring(data)
    entries = [e for e in root.iter() if e.tag.split("}")[-1] in {"item", "entry"}]
    output: list[dict[str, Any]] = []
    for entry in entries:
        title = clean_text(element_text(entry, {"title"}), 500)
        link = ""
        for child in entry.iter():
            if child.tag.split("}")[-1] == "link":
                link = child.attrib.get("href") or (child.text or "").strip()
                if link:
                    break
        link = urllib.parse.urljoin(base_url, link)
        if not title or not link:
            continue
        published_raw = element_text(entry, {"published", "updated", "pubDate", "date"})
        published_at, precision = parse_date(published_raw)
        output.append({
            "source_item_id": element_text(entry, {"guid", "id"}) or link,
            "title": title,
            "url": link,
            "published_at": published_at,
            "published_precision": precision,
            "author": clean_text(element_text(entry, {"author", "creator", "name"}), 300),
            "summary": clean_text(element_text(entry, {"summary", "description", "content", "encoded"})),
        })
    return output


def parse_hn(data: bytes) -> list[dict[str, Any]]:
    payload = json.loads(data)
    output = []
    for hit in payload.get("hits", []):
        title = clean_text(hit.get("title") or hit.get("story_title"), 500)
        link = hit.get("url") or f"https://news.ycombinator.com/item?id={hit.get('objectID')}"
        if not title:
            continue
        output.append({"source_item_id": str(hit.get("objectID")), "title": title, "url": link,
                       "published_at": parse_date(hit.get("created_at"))[0], "published_precision": "datetime",
                       "author": hit.get("author") or "", "summary": f"HN points={hit.get('points', 0)} comments={hit.get('num_comments', 0)} thread=https://news.ycombinator.com/item?id={hit.get('objectID')}"})
    return output


def parse_html_links(data: bytes, base_url: str, external_only: bool) -> list[dict[str, Any]]:
    parser = LinkParser()
    parser.feed(data.decode("utf-8", "replace"))
    base_host = urllib.parse.urlsplit(base_url).netloc
    seen: set[str] = set()
    output = []
    for href, title in parser.links:
        link = urllib.parse.urljoin(base_url, href)
        parts = urllib.parse.urlsplit(link)
        if parts.scheme not in {"http", "https"} or len(title) < 12:
            continue
        if external_only and parts.netloc == base_host:
            continue
        c = canonical_url(link)
        if c in seen:
            continue
        seen.add(c)
        date_match = re.search(r"/(20\d{2})[/-](0[1-9]|1[0-2])[/-]([0-2]\d|3[01])(?:/|$)", parts.path)
        published_at = None
        precision = "unknown"
        if date_match:
            published_at, _ = parse_date("-".join(date_match.groups()))
            precision = "source_url_date"
        output.append({"source_item_id": c, "title": title, "url": link, "published_at": published_at,
                       "published_precision": precision, "author": "", "summary": "Discovered on source page; publication time requires original-source verification."})
    return output


def parse_hf_daily(data: bytes) -> list[dict[str, Any]]:
    payload = json.loads(data)
    rows = payload if isinstance(payload, list) else payload.get("papers", [])
    output = []
    for row in rows:
        paper = row.get("paper", row)
        pid = paper.get("id") or paper.get("paper_id") or paper.get("arxiv_id")
        title = clean_text(paper.get("title"), 500)
        if not pid or not title:
            continue
        published_at, precision = parse_date(paper.get("publishedAt") or paper.get("published_at"))
        output.append({"source_item_id": str(pid), "title": title, "url": f"https://huggingface.co/papers/{pid}",
                       "published_at": published_at, "published_precision": precision,
                       "author": ", ".join(a.get("name", "") if isinstance(a, dict) else str(a) for a in paper.get("authors", [])[:8]),
                       "summary": clean_text(paper.get("summary") or paper.get("abstract"))})
    return output


def parse_arxiv_oai(data: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(data)
    output = []
    for record in [e for e in root.iter() if e.tag.split("}")[-1] == "record"]:
        if any(e.tag.split("}")[-1] == "header" and e.attrib.get("status") == "deleted" for e in record):
            continue
        identifier = element_text(record, {"id"})
        title = clean_text(element_text(record, {"title"}), 500)
        categories = set(element_text(record, {"categories"}).split())
        if not identifier or not title or not (categories & TARGET_ARXIV_CATEGORIES):
            continue
        # OAI datestamp is a modification/harvesting clock, not publication.
        submitted = element_text(record, {"created"})
        published_at, precision = parse_date(submitted)
        output.append({"source_item_id": identifier, "title": title, "url": f"https://arxiv.org/abs/{identifier}",
                       "published_at": published_at, "published_precision": precision,
                       "publication_observed": True,
                       "author": clean_text(element_text(record, {"authors"}), 500),
                       "summary": clean_text(element_text(record, {"abstract"}))})
    return output


def parse_sitemap(data: bytes, include_patterns: list[str], include_prefixes: list[str]) -> tuple[list[dict[str, Any]], list[str]]:
    root = ET.fromstring(data)
    output: list[dict[str, Any]] = []
    nested: list[str] = []
    for node in root:
        local = node.tag.split("}")[-1]
        loc = element_text(node, {"loc"})
        if local == "sitemap" and loc:
            nested.append(loc)
            continue
        pattern_match = not include_patterns or any(p in loc for p in include_patterns)
        prefix_match = not include_prefixes or any(loc.startswith(p) for p in include_prefixes)
        if local != "url" or not loc or not pattern_match or not prefix_match:
            continue
        modified = element_text(node, {"lastmod"})
        published_at, precision = parse_date(modified)
        title = urllib.parse.unquote(urllib.parse.urlsplit(loc).path.rstrip("/").split("/")[-1]).replace("-", " ").strip().title()
        output.append({"source_item_id": loc, "title": title or loc, "url": loc, "published_at": published_at,
                       "published_precision": precision, "author": "", "summary": "Official sitemap update; article publication time must be verified on the original page."})
    return output, nested


def parse_cisa(data: bytes) -> list[dict[str, Any]]:
    payload = json.loads(data)
    output = []
    for row in payload.get("vulnerabilities", []):
        cve = row.get("cveID")
        if not cve:
            continue
        published_at, precision = parse_date(row.get("dateAdded"))
        title = f"{cve}: {row.get('vendorProject', '')} {row.get('product', '')}"
        summary = " ".join(filter(None, [row.get("vulnerabilityName"), row.get("shortDescription"), row.get("requiredAction"), f"Due {row.get('dueDate')}" if row.get("dueDate") else None]))
        output.append({"source_item_id": cve, "title": clean_text(title, 500), "url": f"https://www.cisa.gov/known-exploited-vulnerabilities-catalog?search_api_fulltext={cve}",
                       "published_at": published_at, "published_precision": precision, "author": "CISA", "summary": clean_text(summary)})
    return output


def parse_github_advisories(data: bytes) -> list[dict[str, Any]]:
    payload = json.loads(data)
    output = []
    for row in payload if isinstance(payload, list) else []:
        ghsa = row.get("ghsa_id")
        if not ghsa:
            continue
        output.append({"source_item_id": ghsa, "title": clean_text(f"{ghsa}: {row.get('summary', '')}", 500),
                       "url": row.get("html_url") or f"https://github.com/advisories/{ghsa}",
                       "published_at": parse_date(row.get("published_at"))[0], "published_precision": "datetime",
                       "author": "GitHub Advisory Database", "summary": clean_text(row.get("description"))})
    return output


def fetch_source(source: dict[str, Any], config: dict[str, Any], run_id: str) -> tuple[list[dict[str, Any]], str, str, str]:
    source_type = source["type"]
    if source_type == "declared_gap":
        return [], source["status"], "declared", source["detail"]
    timeout = int(config["request_timeout_seconds"])
    method = source_type
    detail = ""
    partial_reasons = []
    if source_type == "hn":
        cutoff = int((now_utc() - dt.timedelta(hours=config["lookback_hours"])).timestamp())
        chunks: list[bytes] = []
        items = []
        for page in range(10):
            url = source["url"].replace("search_by_date", "search") + "?" + urllib.parse.urlencode({"tags": "story", "numericFilters": f"created_at_i>{cutoff},points>5", "hitsPerPage": 100, "page": page})
            page_data, _, final_url = http_get(url, timeout)
            chunks.append(page_data)
            payload = json.loads(page_data)
            items.extend(parse_hn(page_data))
            if page + 1 >= int(payload.get("nbPages", 1)):
                break
        data = b"\n".join(chunks)
        method = "hn_algolia_paginated"
        if int(payload.get("nbPages", 1)) > 10:
            partial_reasons.append("Hacker News pagination ceiling reached; later pages not fetched.")
    elif source_type == "arxiv_oai":
        start = (now_utc() - dt.timedelta(days=5)).date().isoformat()
        end = now_utc().date().isoformat()
        url = source["url"] + "?" + urllib.parse.urlencode({"verb": "ListRecords", "from": start, "until": end, "set": "cs", "metadataPrefix": "arXivRaw"})
        data, _, final_url = http_get(url, timeout * 2)
        items = parse_arxiv_oai(data)
        method = "official_oai_pmh"
        detail = "Atom API timed out during validation; using official OAI-PMH incremental records."
        if any(n.tag.split("}")[-1] == "resumptionToken" and (n.text or "").strip() for n in ET.fromstring(data).iter()):
            partial_reasons.append("OAI-PMH resumption token remains; only the first page was fetched.")
    elif source_type == "cisa_kev":
        errors = []
        data = b""
        final_url = ""
        for index, url in enumerate(source["urls"]):
            try:
                data, _, final_url = http_get(url, timeout)
                method = "official_json" if index == 0 else "official_github_mirror"
                if index:
                    detail = "CISA.gov TLS failed; used the official cisagov/kev-data GitHub mirror."
                break
            except Exception as exc:
                errors.append(f"{url}: {type(exc).__name__}: {exc}")
        if not data:
            raise RuntimeError("; ".join(errors))
        items = parse_cisa(data)
    elif source_type == "github_advisories":
        start = (now_utc() - dt.timedelta(hours=config["lookback_hours"])).date().isoformat()
        end = now_utc().date().isoformat()
        data = gh_api_paginated("advisories", {"published": f"{start}..{end}", "per_page": "100"})
        pages = json.loads(data)
        flattened = [row for page in pages for row in page]
        items = parse_github_advisories(json.dumps(flattened).encode())
        final_url = source["url"]
        method = "github_advisories_paginated_date_range"
        detail = f"Authenticated GitHub API; complete published-date range {start}..{end}."
    else:
        url = source["url"]
        data, _, final_url = http_get(url, timeout)
        if source_type in {"feed", "feed_filter"}:
            items = parse_feed(data, final_url)
            if source_type == "feed_filter":
                keywords = [k.lower() for k in source.get("keywords", [])]
                items = [i for i in items if any(k in (" " + i["title"] + " " + i["summary"]).lower() for k in keywords)]
        elif source_type == "html_links":
            items = parse_html_links(data, final_url, bool(source.get("external_only")))
        elif source_type == "hf_daily":
            items = parse_hf_daily(data)
        elif source_type in {"sitemap", "sitemap_index"}:
            items, nested = parse_sitemap(data, source.get("include_patterns", []), source.get("include_prefixes", []))
            if len(nested) > 10:
                partial_reasons.append(f"Sitemap index has {len(nested)} children; only the first 10 were fetched.")
            for child_number, nested_url in enumerate(nested[:10], 1):
                nested_data, _, _ = http_get(nested_url, timeout)
                with gzip.open(RAW_DIR / f"{run_id}-{source['id']}-sitemap-{child_number:04d}.gz", "wb") as child_raw:
                    child_raw.write(nested_data)
                extra, remaining = parse_sitemap(nested_data, source.get("include_patterns", []), source.get("include_prefixes", []))
                if remaining:
                    partial_reasons.append("A child sitemap contains another index; deeper children were not fetched.")
                items.extend(extra)
            if nested:
                detail = f"Retained {min(len(nested), 10)} child sitemap payloads as {run_id}-{source['id']}-sitemap-NNNN.gz alongside the parent payload."
        elif source_type == "github_advisories":
            items = []
            chunks = [data]
            items.extend(parse_github_advisories(data))
            for page in range(2, 6):
                page_url = re.sub(r"([?&])page=\d+", r"\1", source["url"])
                separator = "&" if "?" in page_url else "?"
                page_data, _, _ = http_get(page_url + f"{separator}page={page}", timeout)
                page_items = parse_github_advisories(page_data)
                if not page_items:
                    break
                chunks.append(page_data)
                items.extend(page_items)
            data = b"\n".join(chunks)
            method = "github_advisories_paginated"
        else:
            raise ValueError(f"unsupported source type: {source_type}")
    raw_name = RAW_DIR / f"{run_id}-{source['id']}.gz"
    with gzip.open(raw_name, "wb") as raw_file:
        raw_file.write(data)
    source_detail = source.get("detail", "")
    if source_detail:
        detail = " ".join(part for part in [detail, source_detail] if part)
    limit = int(source.get("max_items", config["max_items_per_source"]))
    if len(items) > limit:
        detail = " ".join(filter(None, [detail, f"Parsed {len(items)} items; retained {limit}; source retention limit reached; raw payload preserved."]))
        partial_reasons.append("Source retention limit reached.")
    detail = " ".join(filter(None, [detail, *dict.fromkeys(partial_reasons)]))
    return items[:limit], "partial" if partial_reasons else "complete", method, detail


def upsert_items(conn: sqlite3.Connection, source_id: str, items: list[dict[str, Any]], run_id: str, seen_at: str) -> int:
    count = 0
    for item in items:
        url = canonical_url(item["url"])
        key_basis = f"{source_id}|{item.get('source_item_id') or url}"
        item_key = hashlib.sha256(key_basis.encode()).hexdigest()
        # A missing authoritative creation date invalidates a legacy OAI
        # modification date. Other discovery sources may still retain a known
        # original date when a later response simply omits it.
        if item.get("publication_observed") and not item.get("published_at"):
            conn.execute("UPDATE items SET published_at=NULL,published_precision='unknown' WHERE item_key=?", (item_key,))
        conn.execute(
            """
            INSERT INTO items(item_key, source_id, source_item_id, title, url, canonical_url, published_at,
                              published_precision, first_seen_at, last_seen_at, author, summary, raw_run_id)
            VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(item_key) DO UPDATE SET
              title=excluded.title, url=excluded.url, canonical_url=excluded.canonical_url,
              published_at=COALESCE(excluded.published_at, items.published_at),
              published_precision=CASE WHEN excluded.published_at IS NOT NULL THEN excluded.published_precision ELSE items.published_precision END,
              last_seen_at=excluded.last_seen_at, author=excluded.author, summary=excluded.summary, raw_run_id=excluded.raw_run_id
            """,
            (item_key, source_id, str(item.get("source_item_id") or url), item["title"], item["url"], url,
             item.get("published_at"), item.get("published_precision", "unknown"), seen_at, seen_at,
             item.get("author", ""), item.get("summary", ""), run_id),
        )
        count += 1
    return count


def atomic_write(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(content)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    finally:
        if os.path.exists(temp_name):
            os.unlink(temp_name)


def render_family(run_id: str, generated: str, family: str, family_items: list[dict[str, Any]]) -> str:
    lines = [f"# Tech & AI Local Collector — {family}", "", f"- run_id: `{run_id}`", f"- generated_at: `{generated}`", f"- candidate_count: `{len(family_items)}`", "", "Selected candidates must be verified against the linked original source.", ""]
    for item in family_items:
        timestamp = item["published_at"] or f"first_seen {item['first_seen_at']}"
        summary = clean_text(item.get("summary"), 520).replace("\n", " ")
        lines.append(f"- [{item['title']}]({item['url']}) — {item['source_name']} — `{timestamp}` — precision `{item['published_precision']}`")
        if summary:
            lines.append(f"  - {summary}")
    return "\n".join(lines).rstrip() + "\n"


def export_bundle(conn: sqlite3.Connection, run_id: str, config: dict[str, Any]) -> None:
    generated = iso(now_utc())
    export_hours = int(config.get("export_hours", config["lookback_hours"]))
    cutoff = iso(now_utc() - dt.timedelta(hours=export_hours))
    coverage = [dict(r) for r in conn.execute("SELECT s.name, s.family, c.source_id, c.status, c.method, c.item_count, c.detail FROM coverage c JOIN sources s ON s.source_id=c.source_id WHERE c.run_id=? ORDER BY s.family,s.name", (run_id,))]
    items = [dict(r) for r in conn.execute("SELECT i.raw_run_id,i.source_id,s.name AS source_name,s.family,i.source_item_id,i.title,i.url,i.canonical_url,i.published_at,i.published_precision,i.first_seen_at,i.last_seen_at,i.author,i.summary FROM items i JOIN sources s ON s.source_id=i.source_id WHERE COALESCE(i.published_at,i.first_seen_at)>=? AND i.published_precision!='bootstrap_unknown' ORDER BY COALESCE(i.published_at,i.first_seen_at) DESC", (cutoff,))]
    payload = {"schema_version": 2, "run_id": run_id, "generated_at": generated, "collection_lookback_hours": config["lookback_hours"], "export_hours": export_hours, "coverage": coverage, "items": items}
    manifest = write_input_manifest(EXPORT_DIR, "tech-ai", payload, config, Path(__file__))
    atomic_write(LATEST_JSON, json.dumps(payload, ensure_ascii=False, indent=2))
    lines = ["# Tech & AI Local Collector — Latest", "", f"- run_id: `{run_id}`", f"- generated_at: `{generated}`", f"- collection_lookback_hours: `{config['lookback_hours']}`", f"- export_hours: `{export_hours}`", f"- candidate_count: `{len(items)}`", "", "## Coverage", "", "| Family | Source | Status | Method | Items | Detail |", "|---|---|---|---|---:|---|"]
    for row in coverage:
        detail = (row.get("detail") or "").replace("|", "\\|")
        lines.append(f"| {row['family']} | {row['name']} | {row['status']} | {row.get('method') or ''} | {row['item_count']} | {detail} |")
    lines += ["", "## Candidate bundles", "", "Cloud synthesis must fetch every non-empty family file below, then verify selected candidates against the linked original source. Search/discovery feeds do not own factual claims.", ""]
    by_family: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_family.setdefault(item["family"], []).append(item)
    expected_families = set()
    for family, family_items in sorted(by_family.items()):
        family_filename = f"Tech AI Local Collector - Latest - {family}.md"
        expected_families.add(family_filename)
        lines.append(f"- `{family_filename}` — {len(family_items)} candidates")
        atomic_write(EXPORT_DIR / family_filename, render_family(run_id, generated, family, family_items))
    for old in EXPORT_DIR.glob("Tech AI Local Collector - Latest - *.md"):
        if old.name not in expected_families:
            old.unlink()
    lines += ["", "## Reproducible inputs", "", "Use `brief-workflow/inputs/tech-ai/manifest.json` and its bounded partitions at one pinned Git commit. Legacy family files may exceed connector limits; do not treat truncated reads as complete.", "", f"Exported {manifest['candidate_count']} candidates without a global cap. Source coverage and freshness remain separate from editorial review."]
    markdown = "\n".join(lines).rstrip() + "\n"
    atomic_write(LATEST_MD, markdown)
    local_now = now_utc().astimezone(dt.timezone(dt.timedelta(hours=8)))
    if (local_now.hour == 3 and local_now.minute >= 50) or (local_now.hour == 4 and local_now.minute <= 20):
        archive_name = EXPORT_DIR / f"archive/Tech AI Local Collector - {run_id}.md"
        atomic_write(archive_name, markdown)


def _publish_repo_unlocked(run_id: str) -> tuple[str, str]:
    if not (BRIDGE_REPO / ".git").exists():
        return "failed", f"Bridge repository is missing at {BRIDGE_REPO}"
    try:
        sync_bridge(BRIDGE_REPO)
        expected = {LATEST_MD.name, LATEST_JSON.name}
        expected.update(path.name for path in EXPORT_DIR.glob("Tech AI Local Collector - Latest - *.md"))
        for old in BRIDGE_REPO.glob("Tech AI Local Collector - Latest - *.md"):
            if old.name not in expected:
                old.unlink()
        for name in expected:
            source = EXPORT_DIR / name
            if source.exists():
                shutil.copy2(source, BRIDGE_REPO / name)
        publish_inputs(EXPORT_DIR, BRIDGE_REPO, "tech-ai")
        subprocess.run([GIT, "-C", str(BRIDGE_REPO), "add", "--", ":(glob)Tech AI Local Collector - Latest*.md", LATEST_JSON.name, "brief-workflow/inputs/tech-ai", "brief-workflow/history-latest.json"], check=True, timeout=30)
        changed = subprocess.run([GIT, "-C", str(BRIDGE_REPO), "diff", "--cached", "--quiet"], timeout=30).returncode != 0
        if not changed:
            return "unchanged", "No bridge content changed."
        subprocess.run([GIT, "-C", str(BRIDGE_REPO), "commit", "--quiet", "-m", f"collector: update {run_id}"], check=True, timeout=60)
        push_bridge(BRIDGE_REPO)
        revision = subprocess.check_output([GIT, "-C", str(BRIDGE_REPO), "rev-parse", "HEAD"], text=True, timeout=20).strip()
        return "published", revision
    except Exception as exc:
        return "failed", f"{type(exc).__name__}: {exc}"[:1000]


def publish_repo(run_id: str) -> tuple[str, str]:
    BRIDGE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    with BRIDGE_LOCK.open("w") as lock:
        fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
        return _publish_repo_unlocked(run_id)


def collect() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    conn = db_connect()
    started = now_utc()
    run_id = started.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%dT%H%M%S+0800")
    started_at = iso(started)
    conn.execute("INSERT INTO runs(run_id,started_at,status) VALUES(?,?,?)", (run_id, started_at, "running"))
    conn.commit()
    total = 0
    failures = 0
    for source in config["sources"]:
        conn.execute("INSERT INTO sources(source_id,name,family,source_type) VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET name=excluded.name,family=excluded.family,source_type=excluded.source_type", (source["id"], source["name"], source["family"], source["type"]))
        checked_at = iso(now_utc())
        try:
            had_items = conn.execute("SELECT 1 FROM items WHERE source_id=? LIMIT 1", (source["id"],)).fetchone() is not None
            items, status, method, detail = fetch_source(source, config, run_id)
            if source["type"] in {"sitemap", "sitemap_index", "html_links"}:
                for item in items:
                    if not item.get("published_at"):
                        item["published_precision"] = "first_seen_only" if had_items else "bootstrap_unknown"
            count = upsert_items(conn, source["id"], items, run_id, checked_at)
            total += count
            if status != "complete":
                failures += 1
            success_at = checked_at if status == "complete" else None
            conn.execute("UPDATE sources SET last_status=?,last_checked_at=?,last_success_at=COALESCE(?,last_success_at),last_error=?,last_method=? WHERE source_id=?", (status, checked_at, success_at, detail if status != "complete" else None, method, source["id"]))
            conn.execute("INSERT INTO coverage(run_id,source_id,status,method,item_count,detail) VALUES(?,?,?,?,?,?)", (run_id, source["id"], status, method, count, detail))
        except Exception as exc:
            failures += 1
            detail = f"{type(exc).__name__}: {exc}"[:1000]
            conn.execute("UPDATE sources SET last_status='failed',last_checked_at=?,last_error=? WHERE source_id=?", (checked_at, detail, source["id"]))
            conn.execute("INSERT INTO coverage(run_id,source_id,status,method,item_count,detail) VALUES(?,?,?,?,?,?)", (run_id, source["id"], "failed", source["type"], 0, detail))
        conn.commit()
    status = "complete" if failures == 0 else "partial"
    conn.execute("UPDATE runs SET finished_at=?,status=?,item_count=? WHERE run_id=?", (iso(now_utc()), status, total, run_id))
    conn.commit()
    export_bundle(conn, run_id, config)
    publish_status, publish_detail = publish_repo(run_id)
    print(json.dumps({"run_id": run_id, "status": status, "source_failures": failures, "items_seen": total, "database": str(DB_PATH), "export_index": str(LATEST_MD), "publish_status": publish_status, "publish_detail": publish_detail}, ensure_ascii=False))
    if publish_status == "failed":
        return 3
    return 0 if failures == 0 else 2


def status() -> int:
    conn = db_connect()
    latest = conn.execute("SELECT * FROM runs ORDER BY started_at DESC LIMIT 1").fetchone()
    sources = [dict(r) for r in conn.execute("SELECT source_id,name,last_status,last_checked_at,last_success_at,last_error,last_method FROM sources ORDER BY source_id")]
    print(json.dumps({"latest_run": dict(latest) if latest else None, "sources": sources}, ensure_ascii=False, indent=2))
    return 0


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("command", choices=["collect", "status"], nargs="?", default="collect")
    args = parser.parse_args()
    return collect() if args.command == "collect" else status()


if __name__ == "__main__":
    sys.exit(main())

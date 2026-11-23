#!/usr/bin/env python3
from __future__ import annotations

import argparse
import datetime as dt
import email.utils
import fcntl
import gzip
import hashlib
from html.parser import HTMLParser
import html
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
BRIDGE_REPO = Path(os.environ.get("DAILY_BRIEF_BRIDGE_REPO", str(ROOT.parent / "tech-ai" / "bridge-repo")))
BRIDGE_LOCK = Path(os.environ.get("DAILY_BRIEF_BRIDGE_LOCK", str(ROOT.parent / "github-bridge.lock")))
LATEST_MD = EXPORT_DIR / "Open Source Local Collector - Latest.md"
LATEST_JSON = EXPORT_DIR / "Open Source Local Collector - Latest.json"
GH = Path(os.environ.get("GH_BIN") or shutil.which("gh") or "/opt/homebrew/bin/gh")
GIT = shutil.which("git") or "/usr/bin/git"
USER_AGENT = "OpenSourceBriefCollector/1.0 (local evidence collector)"


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
    try:
        parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return iso(parsed), "date" if re.fullmatch(r"\d{4}-\d{2}-\d{2}", value) else "datetime"
    except ValueError:
        return None, "unknown"


def clean_text(value: str | None, limit: int = 1600) -> str:
    if not value:
        return ""
    value = re.sub(r"<[^>]+>", " ", html.unescape(value))
    return re.sub(r"\s+", " ", value).strip()[:limit]


def canonical_url(value: str) -> str:
    parts = urllib.parse.urlsplit(value)
    query = [(k, v) for k, v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"ref", "source", "fbclid", "gclid"}]
    return urllib.parse.urlunsplit((parts.scheme.lower(), parts.netloc.lower(), parts.path.rstrip("/") or "/", urllib.parse.urlencode(query), ""))


def http_get(url: str, timeout: int) -> tuple[bytes, str]:
    request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT, "Accept": "application/atom+xml, application/json, text/html;q=0.9, */*;q=0.5"})
    last_error: Exception | None = None
    for attempt in range(2):
        try:
            with urllib.request.urlopen(request, timeout=timeout) as response:
                return response.read(10 * 1024 * 1024), response.geturl()
        except (urllib.error.URLError, urllib.error.HTTPError, TimeoutError) as exc:
            last_error = exc
            if attempt == 0 and (isinstance(exc, TimeoutError) or getattr(exc, "code", 0) in {408, 429, 500, 502, 503, 504}):
                time.sleep(2)
                continue
            break
    curl = Path(shutil.which("curl") or "/usr/bin/curl")
    if curl.exists():
        result = subprocess.run([str(curl), "--fail", "--http1.1", "-L", "--retry", "2", "--max-time", str(timeout * 2),
                                 "-A", USER_AGENT, "-sS", url], check=True, capture_output=True, timeout=timeout * 3)
        return result.stdout, url
    raise last_error or RuntimeError("request failed")


def gh_api(endpoint: str, fields: dict[str, str] | None = None) -> bytes:
    if not GH.exists():
        raise RuntimeError(f"gh is missing at {GH}")
    command = [str(GH), "api", endpoint]
    if fields:
        command += ["-X", "GET"]
        for key, value in fields.items():
            command += ["-f", f"{key}={value}"]
    last_error: Exception | None = None
    for attempt in range(3):
        try:
            result = subprocess.run(command, check=True, capture_output=True, timeout=120)
            return result.stdout
        except (subprocess.CalledProcessError, subprocess.TimeoutExpired) as exc:
            last_error = exc
            if attempt < 2:
                time.sleep(2 * (attempt + 1))
    if isinstance(last_error, subprocess.CalledProcessError):
        detail = (last_error.stderr or b"").decode("utf-8", "replace")[:800]
        raise RuntimeError(f"gh api failed for {endpoint}: {detail}") from last_error
    raise last_error or RuntimeError(f"gh api failed for {endpoint}")


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


def save_raw(run_id: str, source_id: str, data: bytes) -> None:
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    with gzip.open(RAW_DIR / f"{run_id}-{source_id}.gz", "wb") as handle:
        handle.write(data)


def parse_trending(data: bytes, since: str, seen_at: str) -> list[dict[str, Any]]:
    text = data.decode("utf-8", "replace")
    rows = re.findall(r'<article class="Box-row">(.*?)</article>', text, flags=re.S)
    items: list[dict[str, Any]] = []
    for rank, row in enumerate(rows, 1):
        match = re.search(r'<h2[^>]*>.*?<a[^>]+href="/([^"?#]+/[^"?#]+)"', row, flags=re.S)
        if not match:
            continue
        repo = clean_text(match.group(1), 200).replace(" ", "")
        description = ""
        description_match = re.search(r'<p[^>]*>(.*?)</p>', row, flags=re.S)
        if description_match:
            description = clean_text(description_match.group(1), 700)
        language_match = re.search(r'itemprop="programmingLanguage">(.*?)</span>', row, flags=re.S)
        language = clean_text(language_match.group(1), 100) if language_match else ""
        gain_match = re.search(r'([\d,]+)\s+stars\s+(today|this week)', clean_text(row), flags=re.I)
        gain = gain_match.group(1).replace(",", "") if gain_match else ""
        summary = f"GitHub Trending {since} snapshot rank={rank}"
        if gain:
            summary += f" star_gain={gain}"
        if language:
            summary += f" language={language}"
        if description:
            summary += f". {description}"
        items.append({
            "source_item_id": f"{since}:{repo}", "title": repo,
            "url": f"https://github.com/{repo}", "published_at": None,
            "published_precision": "snapshot_discovery", "author": repo.split("/", 1)[0],
            "summary": summary, "snapshot_at": seen_at,
        })
    return items


def parse_product_hunt(data: bytes) -> list[dict[str, Any]]:
    root = ET.fromstring(data)
    output = []
    for entry in [e for e in root.iter() if e.tag.split("}")[-1] == "entry"]:
        fields: dict[str, str] = {}
        link = ""
        author = ""
        for child in entry.iter():
            name = child.tag.split("}")[-1]
            if name in {"id", "title", "published", "updated", "content"} and child.text:
                fields[name] = child.text.strip()
            if name == "link" and child.attrib.get("rel") == "alternate":
                link = child.attrib.get("href", "")
            if name == "name" and child.text:
                author = child.text.strip()
        if fields.get("title") and link:
            published_at, precision = parse_date(fields.get("published"))
            output.append({"source_item_id": fields.get("id") or link, "title": clean_text(fields["title"], 400),
                           "url": link, "published_at": published_at, "published_precision": precision,
                           "author": author, "summary": clean_text(fields.get("content"), 1000)})
    return output


def watched_repo_items(repo: str, lookback_hours: int) -> tuple[list[dict[str, Any]], bytes, bool]:
    cutoff = iso(now_utc() - dt.timedelta(hours=lookback_hours))
    meta_data = gh_api(f"repos/{repo}")
    meta = json.loads(meta_data)
    canonical = meta["full_name"]
    releases_data = gh_api(f"repos/{canonical}/releases?per_page=100")
    commit_pages: list[bytes] = []
    commits: list[dict[str, Any]] = []
    for page in range(1, 11):
        page_data = gh_api(f"repos/{canonical}/commits", {"since": cutoff or "", "per_page": "100", "page": str(page)})
        page_rows = json.loads(page_data)
        commit_pages.append(page_data)
        commits.extend(page_rows)
        if len(page_rows) < 100:
            break
    commits_data = b"\n".join(commit_pages)
    releases = json.loads(releases_data)
    items: list[dict[str, Any]] = []
    for row in releases:
        published = row.get("published_at") or row.get("created_at")
        if published and published >= (cutoff or ""):
            items.append({"source_item_id": f"release:{row.get('id')}",
                          "title": f"{canonical} release {row.get('name') or row.get('tag_name')}",
                          "url": row.get("html_url"), "published_at": parse_date(published)[0],
                          "published_precision": "datetime", "author": (row.get("author") or {}).get("login", ""),
                          "summary": clean_text(row.get("body"), 1600)})
    for row in commits:
        commit = row.get("commit") or {}
        committed = (commit.get("committer") or {}).get("date") or (commit.get("author") or {}).get("date")
        items.append({"source_item_id": f"commit:{row.get('sha')}",
                      "title": f"{canonical}: {clean_text(commit.get('message'), 420)}",
                      "url": row.get("html_url"), "published_at": parse_date(committed)[0],
                      "published_precision": "datetime", "author": ((row.get("author") or {}).get("login") or (commit.get("author") or {}).get("name", "")),
                      "summary": f"Verified commit in watched repository {canonical}; sha={row.get('sha', '')[:12]}."})
    raw = json.dumps({"metadata": meta, "releases": releases, "commits": commits}, ensure_ascii=False).encode()
    complete = len(page_rows) < 100 and len(releases) < 100
    return items, raw, complete


def parse_repo_search(data: bytes) -> list[dict[str, Any]]:
    output = []
    for row in json.loads(data).get("items", []):
        topics = ", ".join(row.get("topics") or [])
        summary = f"New repository discovery. stars={row.get('stargazers_count', 0)} language={row.get('language') or ''} topics={topics}. {row.get('description') or ''}"
        output.append({"source_item_id": str(row.get("id")), "title": row.get("full_name"), "url": row.get("html_url"),
                       "published_at": parse_date(row.get("created_at"))[0], "published_precision": "datetime",
                       "author": (row.get("owner") or {}).get("login", ""), "summary": clean_text(summary, 1500)})
    return output


def parse_advisories(data: bytes) -> list[dict[str, Any]]:
    output = []
    for row in json.loads(data):
        ghsa = row.get("ghsa_id")
        if ghsa:
            output.append({"source_item_id": ghsa, "title": f"{ghsa}: {clean_text(row.get('summary'), 500)}",
                           "url": row.get("html_url") or f"https://github.com/advisories/{ghsa}",
                           "published_at": parse_date(row.get("published_at"))[0], "published_precision": "datetime",
                           "author": "GitHub Advisory Database", "summary": clean_text(row.get("description"), 1600)})
    return output


def db_connect() -> sqlite3.Connection:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    conn.executescript("""
      PRAGMA journal_mode=WAL;
      CREATE TABLE IF NOT EXISTS runs (run_id TEXT PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT, status TEXT NOT NULL, item_count INTEGER NOT NULL DEFAULT 0);
      CREATE TABLE IF NOT EXISTS sources (source_id TEXT PRIMARY KEY, name TEXT NOT NULL, family TEXT NOT NULL, source_type TEXT NOT NULL, last_status TEXT, last_checked_at TEXT, last_success_at TEXT, last_error TEXT, last_method TEXT);
      CREATE TABLE IF NOT EXISTS coverage (run_id TEXT NOT NULL, source_id TEXT NOT NULL, status TEXT NOT NULL, method TEXT, item_count INTEGER NOT NULL DEFAULT 0, detail TEXT, PRIMARY KEY(run_id, source_id));
      CREATE TABLE IF NOT EXISTS items (item_key TEXT PRIMARY KEY, source_id TEXT NOT NULL, source_item_id TEXT, title TEXT NOT NULL, url TEXT NOT NULL, canonical_url TEXT NOT NULL, published_at TEXT, published_precision TEXT NOT NULL, first_seen_at TEXT NOT NULL, last_seen_at TEXT NOT NULL, author TEXT, summary TEXT, snapshot_at TEXT, raw_run_id TEXT NOT NULL);
      CREATE INDEX IF NOT EXISTS idx_os_items_published ON items(published_at);
      CREATE INDEX IF NOT EXISTS idx_os_items_seen ON items(first_seen_at);
      CREATE INDEX IF NOT EXISTS idx_os_items_source ON items(source_id);
    """)
    return conn


def upsert_items(conn: sqlite3.Connection, source_id: str, items: list[dict[str, Any]], run_id: str, seen_at: str) -> int:
    for item in items:
        url = canonical_url(item["url"])
        key = hashlib.sha256(f"{source_id}|{item.get('source_item_id') or url}".encode()).hexdigest()
        conn.execute("""
          INSERT INTO items(item_key,source_id,source_item_id,title,url,canonical_url,published_at,published_precision,first_seen_at,last_seen_at,author,summary,snapshot_at,raw_run_id)
          VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)
          ON CONFLICT(item_key) DO UPDATE SET title=excluded.title,url=excluded.url,canonical_url=excluded.canonical_url,published_at=COALESCE(excluded.published_at,items.published_at),published_precision=excluded.published_precision,last_seen_at=excluded.last_seen_at,author=excluded.author,summary=excluded.summary,snapshot_at=excluded.snapshot_at,raw_run_id=excluded.raw_run_id
        """, (key, source_id, str(item.get("source_item_id") or url), item["title"], item["url"], url,
              item.get("published_at"), item.get("published_precision", "unknown"), seen_at, seen_at,
              item.get("author", ""), item.get("summary", ""), item.get("snapshot_at"), run_id))
    return len(items)


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


def render_family(run_id: str, generated: str, family: str, items: list[dict[str, Any]]) -> str:
    lines = [f"# Open Source Local Collector — {family}", "", f"- run_id: `{run_id}`", f"- generated_at: `{generated}`", f"- candidate_count: `{len(items)}`", "", "Discovery candidates must be verified against the linked repository, release, commit, product, or advisory before factual use.", ""]
    for item in items:
        timestamp = item["published_at"] or f"snapshot {item.get('snapshot_at') or item['first_seen_at']}"
        lines.append(f"- [{item['title']}]({item['url']}) — {item['source_name']} — `{timestamp}` — precision `{item['published_precision']}`")
        summary = clean_text(item.get("summary"), 650)
        if summary:
            lines.append(f"  - {summary}")
    return "\n".join(lines).rstrip() + "\n"


def export_bundle(conn: sqlite3.Connection, run_id: str, config: dict[str, Any]) -> None:
    generated = iso(now_utc()) or ""
    cutoff = iso(now_utc() - dt.timedelta(hours=int(config["export_hours"])))
    coverage = [dict(r) for r in conn.execute("SELECT s.name,s.family,c.source_id,c.status,c.method,c.item_count,c.detail FROM coverage c JOIN sources s ON s.source_id=c.source_id WHERE c.run_id=? ORDER BY s.family,s.name", (run_id,))]
    items = [dict(r) for r in conn.execute("SELECT i.raw_run_id,i.source_id,s.name source_name,s.family,i.source_item_id,i.title,i.url,i.canonical_url,i.published_at,i.published_precision,i.first_seen_at,i.last_seen_at,i.author,i.summary,i.snapshot_at FROM items i JOIN sources s ON s.source_id=i.source_id WHERE COALESCE(i.published_at,i.last_seen_at)>=? ORDER BY COALESCE(i.published_at,i.last_seen_at) DESC", (cutoff,))]
    payload = {"schema_version": 2, "run_id": run_id, "generated_at": generated, "collection_lookback_hours": config["lookback_hours"], "export_hours": config["export_hours"], "coverage": coverage, "items": items}
    manifest = write_input_manifest(EXPORT_DIR, "open-source", payload, config, Path(__file__))
    atomic_write(LATEST_JSON, json.dumps(payload, ensure_ascii=False, indent=2))
    by_family: dict[str, list[dict[str, Any]]] = {}
    for item in items:
        by_family.setdefault(item["family"], []).append(item)
    lines = ["# Open Source Local Collector — Latest", "", f"- run_id: `{run_id}`", f"- generated_at: `{generated}`", f"- candidate_count: `{len(items)}`", "", "## Coverage", "", "| Family | Source | Status | Method | Items | Detail |", "|---|---|---|---|---:|---|"]
    for row in coverage:
        lines.append(f"| {row['family']} | {row['name']} | {row['status']} | {row.get('method') or ''} | {row['item_count']} | {(row.get('detail') or '').replace('|', '\\|')} |")
    lines += ["", "## Candidate bundles", "", "Cloud synthesis must read every non-empty file and use GitHub Trending/Product Hunt only for discovery. Exact-window claims require repository, release, commit, or advisory evidence.", ""]
    expected = {LATEST_MD.name, LATEST_JSON.name}
    for family, family_items in sorted(by_family.items()):
        filename = f"Open Source Local Collector - Latest - {family}.md"
        expected.add(filename)
        lines.append(f"- `{filename}` — {len(family_items)} candidates")
        atomic_write(EXPORT_DIR / filename, render_family(run_id, generated, family, family_items))
    for old in EXPORT_DIR.glob("Open Source Local Collector - Latest - *.md"):
        if old.name not in expected:
            old.unlink()
    lines += ["", "## Reproducible inputs", "", "Use `brief-workflow/inputs/open-source/manifest.json` and its bounded partitions at one pinned Git commit. Legacy family files may exceed connector limits; do not treat truncated reads as complete.", "", f"Exported {manifest['candidate_count']} candidates without a global cap. Source coverage and freshness remain separate from editorial review."]
    markdown = "\n".join(lines).rstrip() + "\n"
    atomic_write(LATEST_MD, markdown)
    local_now = now_utc().astimezone(dt.timezone(dt.timedelta(hours=8)))
    if (local_now.hour == 3 and local_now.minute >= 45) or (local_now.hour == 4 and local_now.minute <= 20):
        atomic_write(EXPORT_DIR / f"archive/Open Source Local Collector - {run_id}.md", markdown)


def publish_repo(run_id: str) -> tuple[str, str]:
    if not (BRIDGE_REPO / ".git").exists():
        return "failed", f"Bridge repository is missing at {BRIDGE_REPO}"
    BRIDGE_LOCK.parent.mkdir(parents=True, exist_ok=True)
    try:
        with BRIDGE_LOCK.open("w") as lock:
            fcntl.flock(lock.fileno(), fcntl.LOCK_EX)
            sync_bridge(BRIDGE_REPO)
            expected = {LATEST_MD.name, LATEST_JSON.name}
            expected.update(p.name for p in EXPORT_DIR.glob("Open Source Local Collector - Latest - *.md"))
            for old in BRIDGE_REPO.glob("Open Source Local Collector - Latest - *.md"):
                if old.name not in expected:
                    old.unlink()
            for name in expected:
                source = EXPORT_DIR / name
                if source.exists():
                    shutil.copy2(source, BRIDGE_REPO / name)
            publish_inputs(EXPORT_DIR, BRIDGE_REPO, "open-source")
            subprocess.run([GIT, "-C", str(BRIDGE_REPO), "add", "--", ":(glob)Open Source Local Collector - Latest*.md", LATEST_JSON.name, "brief-workflow/inputs/open-source", "brief-workflow/history-latest.json"], check=True, timeout=30)
            if subprocess.run([GIT, "-C", str(BRIDGE_REPO), "diff", "--cached", "--quiet"], timeout=30).returncode == 0:
                return "unchanged", "No bridge content changed."
            subprocess.run([GIT, "-C", str(BRIDGE_REPO), "commit", "--quiet", "-m", f"open-source collector: update {run_id}"], check=True, timeout=60)
            push_bridge(BRIDGE_REPO)
            revision = subprocess.check_output([GIT, "-C", str(BRIDGE_REPO), "rev-parse", "HEAD"], text=True, timeout=20).strip()
            return "published", revision
    except Exception as exc:
        return "failed", f"{type(exc).__name__}: {exc}"[:1000]


def fetch_source(source: dict[str, Any], config: dict[str, Any], run_id: str, seen_at: str) -> tuple[list[dict[str, Any]], str, str, str]:
    source_type = source["type"]
    timeout = int(config["request_timeout_seconds"])
    if source_type == "trending":
        data, _ = http_get(source["url"], timeout)
        items = parse_trending(data, source["since"], seen_at)
        save_raw(run_id, source["id"], data)
        return items, "complete", "github_trending_html", "Snapshot discovery only; rank is observed at collection time, not reconstructed at the cutoff."
    if source_type == "watched_repo":
        items, data, complete = watched_repo_items(source["repo"], int(config["lookback_hours"]))
        save_raw(run_id, source["id"], data)
        detail = "Releases and commits carry exact GitHub timestamps."
        if not complete:
            detail += " Commit or release pagination ceiling reached; later pages remain uncollected."
        return items, "complete" if complete else "partial", "github_authenticated_api", detail
    if source_type == "repo_search":
        since = (now_utc() - dt.timedelta(hours=int(config["lookback_hours"]))).date().isoformat()
        query = source["query"].replace("{since}", since)
        data = gh_api("search/repositories", {"q": query, "sort": "stars", "order": "desc", "per_page": "100"})
        save_raw(run_id, source["id"], data)
        result = json.loads(data)
        partial = result.get("incomplete_results") or result.get("total_count", 0) > len(result.get("items", []))
        detail = "Discovery only; selected repositories must be verified through README, releases, commits, issues, or discussions."
        if partial:
            detail += " Search is incomplete or limited to its first page; later results are not collected."
        return parse_repo_search(data), "partial" if partial else "complete", "github_search_api", detail
    if source_type == "advisories":
        start = (now_utc() - dt.timedelta(hours=int(config["lookback_hours"]))).date().isoformat()
        end = now_utc().date().isoformat()
        data = gh_api_paginated("advisories", {"published": f"{start}..{end}", "per_page": "100"})
        pages = json.loads(data)
        flattened = [row for page in pages for row in page]
        items = parse_advisories(json.dumps(flattened).encode())
        save_raw(run_id, source["id"], data)
        return items, "complete", "github_advisories_paginated", "Global advisory discovery; report only issues relevant to watched or selected repositories/dependencies."
    if source_type == "product_hunt":
        data, _ = http_get(source["url"], timeout)
        save_raw(run_id, source["id"], data)
        return parse_product_hunt(data), "complete", "product_hunt_atom", "Discovery only; launches must be verified on the product page and upstream repository when available."
    raise ValueError(f"unsupported source type: {source_type}")


def collect() -> int:
    config = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    conn = db_connect()
    started = now_utc()
    run_id = started.astimezone(dt.timezone(dt.timedelta(hours=8))).strftime("%Y%m%dT%H%M%S+0800")
    started_at = iso(started) or ""
    conn.execute("INSERT INTO runs(run_id,started_at,status) VALUES(?,?,?)", (run_id, started_at, "running"))
    conn.commit()
    total = 0
    failures = 0
    for source in config["sources"]:
        conn.execute("INSERT INTO sources(source_id,name,family,source_type) VALUES(?,?,?,?) ON CONFLICT(source_id) DO UPDATE SET name=excluded.name,family=excluded.family,source_type=excluded.source_type", (source["id"], source["name"], source["family"], source["type"]))
        checked_at = iso(now_utc()) or ""
        try:
            items, status, method, detail = fetch_source(source, config, run_id, checked_at)
            count = upsert_items(conn, source["id"], items, run_id, checked_at)
            total += count
            if status != "complete":
                failures += 1
            conn.execute("UPDATE sources SET last_status=?,last_checked_at=?,last_success_at=COALESCE(?,last_success_at),last_error=?,last_method=? WHERE source_id=?", (status, checked_at, checked_at if status == "complete" else None, detail if status != "complete" else None, method, source["id"]))
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
    return 3 if publish_status == "failed" else (0 if failures == 0 else 2)


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

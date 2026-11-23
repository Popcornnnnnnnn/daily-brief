#!/usr/bin/env python3
"""Lossless briefing inputs, explicit feedback and reproducible editorial receipts.

Only standard-library code. No model, network, Notion mutation or scheduler.
"""
from __future__ import annotations

import argparse
import collections
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import socket
import subprocess
import tempfile
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from zoneinfo import ZoneInfo

SCHEMA = 1
POLICY_VERSION = "brief-policy-v1"
PART_SIZE = 25
PART_BYTES = 22000
SHANGHAI = ZoneInfo("Asia/Shanghai")
TAG_ALIASES = {"Interested": "Interesting", "Not Interested": "Not Interesting"}
TAGS = {"Saved", "Interesting", "Research", "Later", "Not Interesting", "Try"}
DISPOSITIONS = {"selected", "duplicate", "no_material_change", "out_of_scope",
                "insufficient_evidence", "deferred", "unreviewed"}


def canonical_url(value: str) -> str:
    p = urlsplit(value)
    if p.scheme not in {"https", "http"} or not p.hostname or p.username or p.password:
        raise ValueError("An absolute public-source HTTP(S) URL is required")
    query = [(k, v) for k, v in parse_qsl(p.query, keep_blank_values=True)
             if not k.lower().startswith("utm_") and k.lower() not in {"fbclid", "gclid"}]
    return urlunsplit((p.scheme.lower(), p.netloc.lower(), p.path.rstrip("/") or "/",
                      urlencode(sorted(query)), ""))


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def encoded(value) -> bytes:
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + "\n").encode()


def atomic_write(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def candidate_id(kind: str, item: dict) -> str:
    identity = [kind, item["source_id"], str(item.get("source_item_id") or item["canonical_url"])]
    return kind + ":" + digest(encoded(identity))[:24]


def window(report_date: str) -> tuple[dt.datetime, dt.datetime]:
    end = dt.datetime.combine(dt.date.fromisoformat(report_date), dt.time(4), SHANGHAI)
    return end - dt.timedelta(days=1), end


def timestamp(value: str) -> dt.datetime:
    parsed = dt.datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError("Timestamp must include its time zone")
    return parsed


def time_class(item: dict, report_date: str) -> str:
    """Discovery clocks and sitemap lastmod never establish news publication time."""
    precision = item.get("published_precision")
    if precision != "datetime" or not item.get("published_at"):
        return "needs_original_date"
    if "sitemap" in str(item.get("discovery_method", "")):
        return "needs_original_date"
    published = timestamp(item["published_at"])
    start, end = window(report_date)
    if start <= published < end:
        return "news_window"
    if end <= published:
        return "after_cutoff"
    if end - dt.timedelta(days=14) <= published < start:
        return "recent_discovery"
    return "older_context"


def safe_family(value: str) -> str:
    if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", value):
        raise ValueError("Invalid family name")
    return value


def write_input_manifest(export_dir: Path, kind: str, payload: dict,
                         config: dict, script: Path) -> dict:
    """Preserve all exported candidates; partition before any editorial ranking."""
    if kind not in {"tech-ai", "open-source"}:
        raise ValueError("Unknown collector kind")
    by_family = collections.defaultdict(list)
    coverage = {r["source_id"]: r for r in payload["coverage"]}
    identities = set()
    for item in payload["items"]:
        item["candidate_id"] = candidate_id(kind, item)
        if item["candidate_id"] in identities:
            raise ValueError("Duplicate candidate identity in input snapshot")
        identities.add(item["candidate_id"])
        item["fresh_in_run"] = item.get("raw_run_id") == payload["run_id"]
        item["discovery_method"] = coverage.get(item["source_id"], {}).get("method", "unknown")
        by_family[safe_family(item["family"])].append(item)
    target = export_dir / "brief-workflow" / "inputs" / kind
    target.mkdir(parents=True, exist_ok=True)
    entries = []
    files = set()
    for family, items in sorted(by_family.items()):
        source_queues = collections.defaultdict(collections.deque)
        for item in items:
            source_queues[item["source_id"]].append(item)
        interleaved = []
        while any(source_queues.values()):
            for source in sorted(source_queues):
                if source_queues[source]:
                    interleaved.append(source_queues[source].popleft())
        items = interleaved
        chunks, chunk = [], []
        def partition_body(part, page):
            return encoded({"schema_version": SCHEMA, "collector": kind,
                            "run_id": payload["run_id"], "family": family,
                            "page": page, "items": part})
        for item in items:
            if chunk and (len(chunk) == PART_SIZE or len(partition_body(chunk + [item], len(chunks) + 1)) > PART_BYTES):
                chunks.append(chunk)
                chunk = []
            if len(partition_body([item], len(chunks) + 1)) > PART_BYTES:
                raise ValueError("One candidate exceeds the partition bound; raw/database data is retained and this export must not publish")
            chunk.append(item)
        if chunk:
            chunks.append(chunk)
        for page, part in enumerate(chunks, 1):
            name = f"{family}-{page:04d}.json"
            body = partition_body(part, page)
            atomic_write(target / name, body)
            files.add(name)
            entries.append({"path": f"brief-workflow/inputs/{kind}/{name}",
                            "family": family, "page": page, "count": len(part),
                            "bytes": len(body), "sha256": digest(body),
                            "git_blob_sha1": hashlib.sha1(b"blob " + str(len(body)).encode() + b"\0" + body).hexdigest()})
    # Only remove this exporter's own stale generated partitions.
    for p in target.glob("*-????.json"):
        if p.name not in files:
            p.unlink()
    manifest = {"schema_version": SCHEMA, "collector": kind,
                "run_id": payload["run_id"], "generated_at": payload["generated_at"],
                "collection_lookback_hours": payload["collection_lookback_hours"],
                "export_hours": payload["export_hours"], "candidate_count": len(identities),
                "export_truncated": False, "family_counts": {k: len(v) for k, v in sorted(by_family.items())},
                "source_coverage": payload["coverage"],
                "source_coverage_complete": bool(payload["coverage"]) and all(r["status"] == "complete" for r in payload["coverage"]),
                "code_sha256": digest(script.read_bytes()),
                "support_sha256": digest(Path(__file__).read_bytes()),
                "config_sha256": digest(encoded(config)),
                "runtime": {"hostname": socket.gethostname(), "script": str(script)},
                "generation_mode": config.get("workflow_export_mode", "collection"),
                "policy_version": POLICY_VERSION, "partitions": entries,
                "publication_boundary": "Discovery timestamps are not original publication proof. Failed sources and cached items remain explicit."}
    atomic_write(target / "manifest.json", encoded(manifest))
    lines = [f"# Brief input manifest: {kind}", "", f"Run: `{payload['run_id']}`",
             f"Generated: `{payload['generated_at']}`", f"Candidates: {len(identities)}; no global export limit.",
             "", "Read manifest.json at one pinned Git commit. Start one page from every non-empty family, then continue by editorial value. Unread pages remain unreviewed, never rejected or complete.", ""]
    for family, items in sorted(by_family.items()):
        pages = [p for p in entries if p["family"] == family]
        lines.append(f"- {family}: {len(items)} candidates, {len(pages)} pages; first `{pages[0]['path']}`")
    atomic_write(target / "index.md", ("\n".join(lines) + "\n").encode())
    return manifest


def publish_inputs(export_dir: Path, bridge: Path, kind: str) -> None:
    source = export_dir / "brief-workflow" / "inputs" / kind
    target = bridge / "brief-workflow" / "inputs" / kind
    if not source.is_dir():
        raise ValueError("Input manifest is missing; refusing an incomplete publish")
    target.mkdir(parents=True, exist_ok=True)
    names = {p.name for p in source.iterdir() if p.is_file()}
    for p in target.iterdir():
        if p.is_file() and p.name not in names:
            p.unlink()
    for name in names:
        shutil.copy2(source / name, target / name)
    atomic_write(bridge / "brief-workflow/history-latest.json", encoded(history(bridge)))


def sync_bridge(bridge: Path) -> None:
    """Preserve concurrent cloud receipt commits and any unrelated local staging."""
    branch = subprocess.check_output(["git", "-C", str(bridge), "branch", "--show-current"], text=True).strip()
    if branch != "main":
        raise ValueError("Bridge must stay on its established main branch")
    if subprocess.run(["git", "-C", str(bridge), "diff", "--cached", "--quiet"]).returncode:
        raise ValueError("Bridge has pre-existing staged changes; refusing another writer's work")
    subprocess.run(["git", "-C", str(bridge), "pull", "--rebase", "--quiet"], check=True, timeout=90)


def push_bridge(bridge: Path) -> None:
    for attempt in range(3):
        result = subprocess.run(["git", "-C", str(bridge), "push", "--quiet", "origin", "main"], capture_output=True, timeout=120)
        if result.returncode == 0:
            return
        detail = result.stderr.decode("utf-8", "replace")
        if attempt == 2 or not any(t in detail for t in ("fetch first", "non-fast-forward", "remote contains work")):
            raise RuntimeError("Bridge push failed: " + detail[:800])
        # A different path was committed by a cloud task. No force-push or reset.
        subprocess.run(["git", "-C", str(bridge), "pull", "--rebase", "--quiet"], check=True, timeout=90)
        # The rebase may have introduced a cloud receipt after the original
        # derived index was built. Publish the index for the actual combined tree.
        atomic_write(bridge / "brief-workflow/history-latest.json", encoded(history(bridge)))
        subprocess.run(["git", "-C", str(bridge), "add", "--", "brief-workflow/history-latest.json"], check=True, timeout=30)
        if subprocess.run(["git", "-C", str(bridge), "diff", "--cached", "--quiet"], timeout=30).returncode:
            subprocess.run(["git", "-C", str(bridge), "commit", "--quiet", "-m", "collector: reconcile history after concurrent receipt"], check=True, timeout=60)


def read_inputs(root: Path, kind: str) -> tuple[dict, list[dict]]:
    base = root / "brief-workflow" / "inputs" / kind
    manifest = json.loads((base / "manifest.json").read_bytes())
    if manifest.get("schema_version") != SCHEMA or manifest.get("collector") != kind:
        raise ValueError("Unsupported manifest")
    items = []
    counts = collections.Counter()
    seen = set()
    for part in manifest["partitions"]:
        path = root / part["path"]
        if not path.resolve().is_relative_to(base.resolve()) or path.name in seen:
            raise ValueError("Invalid or repeated partition path")
        seen.add(path.name)
        data = path.read_bytes()
        if digest(data) != part["sha256"] or len(data) != part["bytes"]:
            raise ValueError("Partition hash or size mismatch")
        value = json.loads(data)
        if value["run_id"] != manifest["run_id"] or value["collector"] != kind or value["family"] != part["family"]:
            raise ValueError("Mixed input runs or families")
        if len(value["items"]) != part["count"]:
            raise ValueError("Partition count mismatch")
        items.extend(value["items"])
        counts[part["family"]] += len(value["items"])
    if len(items) != manifest["candidate_count"] or dict(counts) != manifest["family_counts"]:
        raise ValueError("Manifest totals do not reconcile")
    ids = [i["candidate_id"] for i in items]
    if len(set(ids)) != len(ids) or any(i["candidate_id"] != candidate_id(kind, i) for i in items):
        raise ValueError("Candidate identity mismatch")
    return manifest, items


def feedback_signals(snapshot: dict) -> dict:
    """Only explicit human tags affect exact subjects. Seen/Unread never imply taste."""
    if snapshot.get("schema_version") != SCHEMA or not snapshot.get("observed_at"):
        raise ValueError("Feedback requires schema and observation time")
    timestamp(snapshot["observed_at"])
    output = []
    seen = set()
    for row in snapshot.get("items", []):
        key = ("queue:" + row["row_url"] if row.get("source_kind", "reading_queue") == "reading_queue"
               else row["feedback_id"])
        if key in seen:
            raise ValueError("Repeated feedback row")
        seen.add(key)
        tags = [TAG_ALIASES.get(x, x) for x in (row.get("tags") or [])]
        if any(x not in TAGS for x in tags):
            raise ValueError("Unknown human tag; refresh the live schema")
        if row.get("observation_status") in {"missing_control", "missing_queue_row", "unrefreshed"}:
            # Preserve its historical human state in the index, but do not
            # apply a vanished control as a currently observed preference.
            continue
        if not tags:
            continue
        subjects = [canonical_url(row["original_url"])] if row.get("original_url") else []
        # Use the recorded original URL only; supplied topic mappings are not
        # evidence that the human rated a whole topic or repository.
        if not subjects:
            continue
        disposition = ("conflict" if "Not Interesting" in tags and any(t in tags for t in {"Interesting", "Research", "Try"})
                       else "avoid_exact" if "Not Interesting" in tags else "park_exact" if "Later" in tags
                       else "prefer_exact" if any(t in tags for t in {"Interesting", "Research", "Try"}) else "reference_only")
        output.append({"row_url": row["row_url"], "feedback_id": key,
                       "source_kind": row.get("source_kind", "reading_queue"),
                       "quote": row.get("quote"), "provenance": row.get("provenance"),
                       "observed_at": row.get("observed_at", snapshot["observed_at"]),
                       "subjects": sorted(set(subjects)), "tags": sorted(set(tags)),
                       "disposition": disposition, "reading_status": row.get("reading_status"),
                       "next": row.get("next") or "", "authority": "explicit_human_disposition"})
    return {"schema_version": SCHEMA, "observed_at": snapshot["observed_at"], "signals": output,
            "review_notes": snapshot.get("review_notes", []),
            "coverage_gaps": snapshot.get("coverage_gaps", []),
            "limits": "Exact subjects only. No tag grants research, installation, cleanup or notification permission. No response is unknown."}


def prepare(root: Path, revision: str, report_date: str, out: Path, feedback: dict | None = None) -> dict:
    if not re.fullmatch(r"[0-9a-f]{40}", revision):
        raise ValueError("Pin a full 40-character Git revision")
    start, end = window(report_date)
    binding_verified = False
    if (root / ".git").exists():
        actual = subprocess.check_output(["git", "-C", str(root), "rev-parse", "HEAD"], text=True).strip()
        dirty = subprocess.check_output(["git", "-C", str(root), "status", "--porcelain", "--", "brief-workflow/inputs"], text=True)
        if actual != revision or dirty:
            raise ValueError("Checked-out input files do not match the pinned Git revision")
        binding_verified = True
    sources = []
    candidates = []
    for kind in ("tech-ai", "open-source"):
        manifest, items = read_inputs(root, kind)
        sources.append({"collector": kind, "revision": revision, "run_id": manifest["run_id"],
                        "generated_at": manifest["generated_at"], "source_coverage_complete": manifest["source_coverage_complete"],
                        "manifest_sha256": digest((root / "brief-workflow/inputs" / kind / "manifest.json").read_bytes()),
                        "candidate_count": len(items), "family_counts": manifest["family_counts"],
                        "source_coverage": manifest["source_coverage"]})
        for item in items:
            item = dict(item)
            item["time_class"] = time_class(item, report_date)
            item["editorial_status"] = "unreviewed"
            candidates.append(item)
    receipt = {"schema_version": SCHEMA, "report_date": report_date,
               "window_start": start.isoformat(), "window_end": end.isoformat(),
               "input_revision": revision, "revision_binding_verified": binding_verified, "policy_version": POLICY_VERSION,
               "inputs": sources, "candidate_count": len(candidates),
               "time_counts": dict(collections.Counter(i["time_class"] for i in candidates)),
               "editorial_complete": False, "delivery_verified": False}
    atomic_write(out / "input-receipt.json", encoded(receipt))
    atomic_write(out / "candidate-register.jsonl", b"".join(encoded(i).replace(b"\n", b"") + b"\n" for i in candidates))
    if feedback is not None:
        atomic_write(out / "feedback-signals.json", encoded(feedback_signals(feedback)))
    return receipt


AUDIT_STAGES = {
    "trigger", "collector_dispatch", "source_access", "favorites_processing",
    "research", "source_verification", "dedupe", "draft", "notion_write",
    "notion_readback", "report_delivery",
}
AUDIT_COUNTS = {"discovered", "fully_read", "saved_verified", "unfavorited", "remaining"}
COLLECTOR_THREAD_ID = "01a16b62-d379-7619-bab0-33d09ad31027"


def validate_execution_audit(record: dict) -> None:
    """Validate optional evidence structure, not the truth of external outcomes.

    Legacy records remain valid without this extension. Null means unknown,
    never zero. Publishing a valid receipt does not establish user delivery.
    """
    if "execution_audit" not in record:
        return
    audit = record["execution_audit"]
    if not isinstance(audit, dict) or audit.get("schema_version") != 1:
        raise ValueError("Unsupported execution audit")
    if audit.get("run_id") != record.get("run_id"):
        raise ValueError("Execution audit must bind to the editorial run")
    if audit.get("collector_thread_id") not in {None, COLLECTOR_THREAD_ID}:
        raise ValueError("Execution audit references an unapproved collector")

    def evidence(value):
        if not isinstance(value, list) or any(not isinstance(x, str) or not x.strip() for x in value):
            raise ValueError("Audit evidence must be a list of nonempty strings")
        for item in value:
            if re.search(r"(?i)(?:authorization\s*:\s*bearer|(?:password|access_token|refresh_token|api_key)\s*[=:])", item):
                raise ValueError("Audit evidence must not contain credentials")
        return value

    stages = audit.get("stages")
    if not isinstance(stages, list):
        raise ValueError("Execution audit requires stages")
    seen = set()
    for stage in stages:
        if not isinstance(stage, dict):
            raise ValueError("Audit stage must be an object")
        sid = stage.get("stage_id")
        if not isinstance(sid, str) or not re.fullmatch(r"[a-z][a-z0-9_.-]*", sid) or sid in seen:
            raise ValueError("Invalid or repeated audit stage")
        seen.add(sid)
        if sid.split(".", 1)[0] not in AUDIT_STAGES:
            raise ValueError("Unknown audit stage")
        status = stage.get("status")
        if status not in {"success", "failed", "unknown", "not_attempted"}:
            raise ValueError("Unknown audit status")
        required = {"started_at", "ended_at", "evidence", "error", "cause_proven", "next_step"}
        if not required.issubset(stage):
            raise ValueError("Audit stage is missing evidence fields")
        times = []
        for key in ("started_at", "ended_at"):
            value = stage[key]
            if value is not None and not isinstance(value, str):
                raise ValueError("Audit times must be zoned ISO strings or null")
            times.append(timestamp(value) if value is not None else None)
        if all(t is not None for t in times) and times[1] < times[0]:
            raise ValueError("Audit stage ends before it starts")
        ev = evidence(stage["evidence"])
        error = stage["error"]
        if error is not None and (not isinstance(error, dict) or not all(isinstance(error.get(k), str) and error[k].strip() for k in ("code", "message"))):
            raise ValueError("Audit error needs a code and message")
        if type(stage["cause_proven"]) is not bool:
            raise ValueError("Audit cause_proven must be boolean")
        if stage["next_step"] is not None and (not isinstance(stage["next_step"], str) or not stage["next_step"].strip()):
            raise ValueError("Audit next step must be nonempty text or null")
        if status == "success" and (not ev or error is not None):
            raise ValueError("Audit success needs evidence and no error")
        if status == "failed" and (error is None or not stage["next_step"]):
            raise ValueError("Audit failure needs the observed error and next step")
        if status == "not_attempted" and (any(t is not None for t in times) or error is not None):
            raise ValueError("Unattempted stages cannot have execution times or errors")
        if stage["cause_proven"] and (not ev or error is None):
            raise ValueError("A proven cause requires error and supporting evidence")
        if error is not None and re.search(r"(?i)(login.{0,10}expired|expired.{0,10}login|登录.{0,4}过期)", error["code"] + " " + error["message"]):
            if not stage["cause_proven"] or not ev:
                raise ValueError("Expired login requires explicit supporting evidence")
        if "result" in stage and not isinstance(stage["result"], dict):
            raise ValueError("Audit result must be an object")
    if not AUDIT_STAGES.issubset(seen):
        raise ValueError("Execution audit is missing required stages")
    counts = audit.get("counts")
    if not isinstance(counts, dict) or set(counts) != AUDIT_COUNTS:
        raise ValueError("Execution audit requires the five distinct counts")
    if any(v is not None and (type(v) is not int or v < 0) for v in counts.values()):
        raise ValueError("Audit counts must be nonnegative integers or null")
    favorite = next(s for s in stages if s["stage_id"] == "favorites_processing")
    if favorite["status"] == "not_attempted" and any(counts[k] not in (None, 0) for k in ("fully_read", "saved_verified", "unfavorited")):
        raise ValueError("Unattempted favorites cannot claim completed work")
    received = audit.get("user_received")
    received_evidence = evidence(audit.get("user_received_evidence"))
    if received not in {"unknown", "confirmed"} or (received == "confirmed" and not received_evidence):
        raise ValueError("User receipt cannot be inferred from submission")


def validate_record(record: dict, known_ids: set[str] | None = None) -> dict:
    if record.get("schema_version") != SCHEMA:
        raise ValueError("Unsupported editorial record")
    if not re.fullmatch(r"[a-zA-Z0-9_-]{1,100}", record.get("run_id", "")):
        raise ValueError("Invalid editorial run ID")
    start, end = window(record["report_date"])
    if timestamp(record["window_start"]) != start or timestamp(record["window_end"]) != end:
        raise ValueError("Editorial window must remain fixed at 04:00 Shanghai")
    if record.get("policy_version") != POLICY_VERSION:
        raise ValueError("Unknown policy version")
    if not re.fullmatch(r"[a-zA-Z0-9_.-]{1,100}", record.get("strategy_version", "")):
        raise ValueError("An explicit strategy version is required")
    if not re.fullmatch(r"[0-9a-f]{40}", record.get("input_revision", "")):
        raise ValueError("Missing immutable input revision")
    seen = set()
    for decision in record.get("decisions", []):
        cid = decision["candidate_id"]
        if cid in seen or (known_ids is not None and cid not in known_ids):
            raise ValueError("Unknown or repeated candidate decision")
        seen.add(cid)
        if decision.get("disposition") not in DISPOSITIONS or not decision.get("reason", "").strip():
            raise ValueError("A decision needs its disposition and concrete reason")
        if decision["disposition"] == "selected":
            if not decision.get("event_id") or not decision.get("evidence_urls"):
                raise ValueError("Selected decisions require event identity and original evidence")
            for u in decision["evidence_urls"]:
                canonical_url(u)
        if decision.get("reported_url"):
            canonical_url(decision["reported_url"])
    total = record.get("candidate_count")
    if not isinstance(total, int) or total < len(seen):
        raise ValueError("Invalid candidate denominator")
    unreviewed = total - sum(d["disposition"] != "unreviewed" for d in record.get("decisions", []))
    if record.get("unreviewed_count") != unreviewed:
        raise ValueError("Unreviewed denominator does not reconcile")
    if record.get("editorial_complete") and unreviewed:
        raise ValueError("Unread candidates cannot be declared complete")
    for extra in record.get("external_candidates", []):
        canonical_url(extra["original_url"])
        if extra.get("disposition") not in DISPOSITIONS or not extra.get("reason", "").strip():
            raise ValueError("External decisions need a disposition and concrete reason")
        if extra["disposition"] == "selected" and (not extra.get("event_id") or not extra.get("evidence_urls")):
            raise ValueError("Selected external decisions require event identity and evidence")
        for u in extra.get("evidence_urls", []):
            canonical_url(u)
    if record.get("delivery_verified"):
        delivery = record.get("delivery_receipt") or {}
        if not delivery.get("page_url") or not delivery.get("readback_at") or not delivery.get("page_id"):
            raise ValueError("Delivery needs the actual page identity and independent readback time")
        canonical_url(delivery["page_url"])
        timestamp(delivery["readback_at"])
    validate_execution_audit(record)
    return record


def record_receipt(root: Path, record: dict, known_ids: set[str] | None = None) -> Path:
    validate_record(record, known_ids)
    target = root / "brief-workflow/records" / record["report_date"] / (record["run_id"] + ".json")
    body = encoded(record)
    if target.exists() and target.read_bytes() != body:
        raise ValueError("A run receipt is immutable; use a new linked correction run")
    if not target.exists():
        atomic_write(target, body)
    return target


def history(root: Path) -> dict:
    events = collections.defaultdict(list)
    invalid = []
    receipts = []
    for path in sorted((root / "brief-workflow/records").glob("*/*.json")):
        try:
            value = validate_record(json.loads(path.read_bytes()))
            receipts.append({"path": str(path.relative_to(root)), "sha256": digest(path.read_bytes()),
                             "run_id": value["run_id"], "unreviewed_count": value["unreviewed_count"]})
            # A rehearsal must never suppress an event in the real report.
            if value.get("mode") == "shadow":
                continue
            for decision in value.get("decisions", []) + value.get("external_candidates", []):
                if decision.get("event_id"):
                    events[decision["event_id"]].append({"report_date": value["report_date"],
                        "run_id": value["run_id"], "disposition": decision["disposition"],
                        "delivery_verified": bool(value.get("delivery_verified")),
                        "reason": decision["reason"], "reported_url": decision.get("reported_url"),
                        "evidence_urls": decision.get("evidence_urls", [])})
        except (ValueError, KeyError, TypeError) as exc:
            invalid.append({"path": str(path.relative_to(root)), "error": str(exc)})
    return {"schema_version": SCHEMA, "events": dict(events), "receipts": receipts,
            "invalid_receipts": invalid, "history_complete": not invalid,
            "limits": "Shadow runs are excluded. Only independently verified delivery proves an event was reported; reconcile pending records against Notion. Absence is not proof an event was never reported."}


def evaluate(gold: dict, baseline: dict, candidate: dict) -> dict:
    """Score comparable saved decisions, counting missing/failed outputs in coverage."""
    if baseline["input_revision"] != candidate["input_revision"]:
        raise ValueError("Compare strategies on the same immutable inputs")
    validate_record(baseline)
    validate_record(candidate)
    if baseline["report_date"] != candidate["report_date"]:
        raise ValueError("Evaluation windows must match")
    cases = gold["cases"]
    if len({x["candidate_id"] for x in cases}) != len(cases):
        raise ValueError("Repeated gold cases")
    scores = {}
    for name, record in (("baseline", baseline), ("candidate", candidate)):
        decisions = {x["candidate_id"]: x for x in record["decisions"]}
        correct = answered = tp = fp = fn = 0
        for case in cases:
            if case.get("expected") not in {"select", "reject"} or case.get("origin") not in {"human_explicit", "policy_fixture"}:
                raise ValueError("Gold labels require an explicit origin and select/reject answer")
            decision = decisions.get(case["candidate_id"])
            valid = decision and decision["disposition"] not in {"unreviewed", "deferred", "insufficient_evidence"}
            selected = bool(valid and decision["disposition"] == "selected")
            answered += bool(valid)
            correct += bool(valid and selected == (case["expected"] == "select"))
            tp += selected and case["expected"] == "select"
            fp += selected and case["expected"] == "reject"
            fn += not selected and case["expected"] == "select"
        scores[name] = {"cases": len(cases), "answered": answered,
                        "coverage": answered / len(cases) if cases else 0,
                        "complete_accuracy": correct / len(cases) if cases else 0,
                        "precision": tp / (tp + fp) if tp + fp else 0,
                        "recall": tp / (tp + fn) if tp + fn else 0}
    human_holdout = bool(cases) and all(x.get("origin") == "human_explicit" and x.get("split") == "holdout" for x in cases)
    eligible = (human_holdout and len(cases) >= 20 and scores["candidate"]["coverage"] == 1
                and scores["candidate"]["complete_accuracy"] > scores["baseline"]["complete_accuracy"]
                and scores["candidate"]["recall"] >= scores["baseline"]["recall"])
    return {"schema_version": SCHEMA, "input_revision": candidate["input_revision"], "scores": scores,
            "strategies": {"baseline": baseline["strategy_version"], "candidate": candidate["strategy_version"]},
            "promotion_eligible": eligible,
            "reason": "Promotion requires at least 20 untouched explicit-human holdout cases, complete coverage, improved accuracy and no recall regression. Fixtures never prove personal relevance."}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    p = sub.add_parser("prepare", help="Validate pinned inputs and prepare a shadow run without publishing")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--revision", required=True)
    p.add_argument("--date", required=True)
    p.add_argument("--out", type=Path, required=True)
    p.add_argument("--feedback", type=Path)
    p = sub.add_parser("record", help="Validate and persist an immutable editorial receipt")
    p.add_argument("--root", type=Path, required=True)
    p.add_argument("--file", type=Path, required=True)
    p = sub.add_parser("history", help="Build history from validated editorial receipts")
    p.add_argument("--root", type=Path, required=True)
    p = sub.add_parser("evaluate", help="Compare saved decisions using explicitly labeled cases")
    for name in ("gold", "baseline", "candidate"):
        p.add_argument("--" + name, type=Path, required=True)
    args = parser.parse_args()
    if args.command == "prepare":
        result = prepare(args.root, args.revision, args.date, args.out,
                         json.loads(args.feedback.read_bytes()) if args.feedback else None)
    elif args.command == "record":
        result = {"path": str(record_receipt(args.root, json.loads(args.file.read_bytes())))}
    elif args.command == "history":
        result = history(args.root)
    else:
        result = evaluate(*(json.loads(getattr(args, n).read_bytes()) for n in ("gold", "baseline", "candidate")))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


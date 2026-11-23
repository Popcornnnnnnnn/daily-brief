#!/usr/bin/env python3
"""Capture explicit inline/chat feedback; no network, model or Notion writes."""
from __future__ import annotations

import argparse
import copy
import datetime as dt
import hashlib
import json
from pathlib import Path
import re
from urllib.parse import urlsplit

from brief_workflow import canonical_url, encoded, feedback_signals, timestamp, atomic_write

LIKE = re.compile(r'^\t* {0,3}- \[([ xX])\] 👍 \[Useful\]\((https?://[^\s<>]+)\)(?: \{color="[^"]+"\})?\s*$')
KINDS = {"relevance", "presentation", "correction", "workflow", "unresolved"}
LABELS = {"interesting": "Interesting", "not_interesting": "Not Interesting", "later": "Later"}


def fingerprint(*parts: str) -> str:
    return hashlib.sha256(encoded(list(parts))).hexdigest()[:32]


def notion_page_url(value: str) -> str:
    page = urlsplit(canonical_url(value))
    if page.hostname not in {"app.notion.com", "www.notion.so", "notion.so"}:
        raise ValueError("Inline feedback must identify its actual Notion page")
    match = re.search(r"([0-9a-f]{32}|[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12})$",
                      page.path.rstrip("/").split("/")[-1], re.I)
    if not match:
        raise ValueError("A stable Notion page ID is required")
    return "https://app.notion.com/p/" + match[1].replace("-", "").lower()


def inline_snapshot(page: dict, observed_at: str, run_id: str) -> dict:
    """A complete read includes unchecked controls, so removing a like clears it."""
    timestamp(observed_at)
    page_url = notion_page_url(page["url"])
    def snapshot(rows, complete=True, reason=None):
        return {"schema_version": 1, "run_id": run_id, "observed_at": observed_at,
                "source_kind": "notion_inline_like", "page_url": page_url, "items": rows,
                "coverage": {"page_complete": complete, "controls_read": len(rows), "reason": reason},
                "limits": "Unchecked is unknown/retracted, never dislike. Missing or unread controls are coverage gaps, not unchecks."}
    if page.get("complete") is not True or page.get("truncated") or page.get("unknown_block_count", 0):
        return snapshot([], False, "incomplete_page_read")
    rows = []
    seen = set()
    fence = None
    for line in page["markdown"].splitlines():
        # Notion uses tabs for child blocks; Markdown fence indentation within
        # that container is limited to three spaces, including closing fences.
        delimiter = re.match(r"^(\t*) {0,3}(`{3,}|~{3,})(.*)$", line)
        if fence:
            if (delimiter and delimiter[1] == fence[2] and delimiter[2][0] == fence[0]
                    and len(delimiter[2]) >= fence[1] and not delimiter[3].strip()):
                fence = None
            continue
        if delimiter:
            fence = (delimiter[2][0], len(delimiter[2]), delimiter[1])
            continue
        if re.match(r"^\t* {4,}\S", line):
            continue
        match = LIKE.fullmatch(line)
        if not match:
            if "👍" in line and "[Useful]" in line and re.match(r"\s*- \[[ xX]\]", line):
                return snapshot([], False, "malformed_control")
            continue
        try:
            original = canonical_url(match[2])
        except ValueError:
            return snapshot([], False, "invalid_control_source_url")
        key = "inline:" + fingerprint(page_url, original)
        if key in seen:
            return snapshot([], False, "repeated_control")
        seen.add(key)
        checked = match[1].lower() == "x"
        rows.append({"feedback_id": key, "row_url": page_url, "original_url": original,
                     "tags": ["Interesting"] if checked else [], "source_kind": "notion_inline_like",
                     "checked": checked, "observed_at": observed_at})
    return snapshot([], False, "unclosed_code_fence") if fence else snapshot(rows)


def chat_snapshot(message: dict, observed_at: str) -> dict:
    """Classification is explicit input from the current trusted human turn."""
    timestamp(observed_at)
    if message.get("role") != "user" or not message.get("quote", "").strip():
        raise ValueError("Only the original human message can supply feedback")
    if not message.get("thread_id") or not message.get("message_ref") or not message.get("thread_url"):
        raise ValueError("Chat feedback needs a real thread and message locator")
    kind = message["kind"]
    if kind not in KINDS:
        raise ValueError("Unknown chat feedback kind")
    key = "chat:" + fingerprint(message["thread_id"], message["message_ref"])
    quote = message["quote"]
    provenance = {k: message[k] for k in ("thread_id", "thread_url", "message_ref", "role", "quote")}
    provenance["quote_sha256"] = hashlib.sha256(quote.encode()).hexdigest()
    items, notes = [], []
    if kind == "relevance":
        if message.get("scope") != "item" or message.get("label") not in LABELS:
            raise ValueError("Relevance feedback needs an exact item and explicit label")
        original = canonical_url(message["original_url"])
        items.append({"feedback_id": key, "row_url": message["thread_url"], "original_url": original,
                      "tags": [LABELS[message["label"]]], "source_kind": "chat", "quote": quote,
                      "provenance": provenance, "observed_at": observed_at})
    else:
        if message.get("label") is not None:
            raise ValueError("Presentation, corrections and workflow requests are not relevance labels")
        note = {"feedback_id": key, "kind": kind, "scope": message.get("scope", "unresolved"),
                "provenance": provenance, "observed_at": observed_at,
                "status": "needs_review", "human_gold_eligible": False}
        for field in ("report_url", "original_url"):
            if message.get(field):
                note[field] = canonical_url(message[field])
        notes.append(note)
    result = {"schema_version": 1, "run_id": key.replace(":", "_"), "observed_at": observed_at,
              "source_kind": "chat", "items": items, "review_notes": notes,
              "limits": "Preserve the quote. Ambiguous scope is unresolved. No automatic broad preference or action permission."}
    validate_snapshot(result)
    return result


def validate_chat_provenance(entry: dict) -> None:
    provenance = entry.get("provenance", {})
    for field in ("quote", "thread_id", "thread_url", "message_ref"):
        value = provenance.get(field)
        if not isinstance(value, str) or not value.strip() or (field != "quote" and value != value.strip()):
            raise ValueError("Chat provenance requires non-whitespace original text and locators")
    quote = provenance.get("quote", "")
    url = urlsplit(provenance["thread_url"])
    thread = provenance["thread_id"]
    if (url.username or url.password or not
            ((url.scheme == "codex" and url.netloc == "threads" and url.path.strip("/") == thread)
             or (url.scheme == "https" and url.hostname in {"chatgpt.com", "chat.openai.com"}
                 and url.path.rstrip("/").endswith("/c/" + thread)))):
        raise ValueError("Chat locator must identify the actual Codex or ChatGPT thread")
    if (provenance.get("role") != "user" or not quote
            or not provenance.get("thread_id") or not provenance.get("message_ref")
            or not provenance.get("thread_url")
            or provenance.get("quote_sha256") != hashlib.sha256(quote.encode()).hexdigest()
            or entry.get("feedback_id") != "chat:" + fingerprint(provenance["thread_id"], provenance["message_ref"])):
        raise ValueError("Chat feedback requires consistent original-human provenance")


def validate_snapshot(value: dict) -> None:
    feedback_signals(value)
    if not isinstance(value.get("items", []), list) or not isinstance(value.get("review_notes", []), list):
        raise ValueError("Feedback entries must be arrays")
    source_kind = value.get("source_kind", "reading_queue")
    if source_kind not in {"reading_queue", "notion_inline_like", "chat"}:
        raise ValueError("Unknown feedback receipt source kind")
    observed = timestamp(value["observed_at"])
    for entry in value.get("items", []) + value.get("review_notes", []):
        if "observation_status" in entry:
            raise ValueError("Observation status belongs to the derived index, not source receipts")
        if (source_kind in {"chat", "notion_inline_like"} or "observed_at" in entry):
            if timestamp(entry.get("observed_at", "")) != observed:
                raise ValueError("Entry observation time must match its receipt")
    if source_kind == "chat":
        entries = value.get("items", []) + value.get("review_notes", [])
        if len(entries) != 1 or value.get("run_id") != entries[0].get("feedback_id", "").replace(":", "_"):
            raise ValueError("A chat run ID must match its single stable feedback identity")
    if source_kind == "notion_inline_like":
        complete = value.get("coverage", {}).get("page_complete")
        if type(complete) is not bool or (not complete and value.get("items")):
            raise ValueError("Incomplete inline observations cannot supply human labels")
        notion_page_url(value["page_url"])
    if (source_kind == "reading_queue" and value.get("coverage_complete") is not None
            and type(value["coverage_complete"]) is not bool):
        raise ValueError("Queue coverage must be true, false or explicitly unknown")
    for item in value.get("items", []):
        kind = item.get("source_kind", source_kind)
        if kind != source_kind:
            raise ValueError("Feedback items must agree with their receipt source kind")
        if kind == "reading_queue":
            if item.get("feedback_id") not in {None, "queue:" + item["row_url"]}:
                raise ValueError("Queue identity must be derived from its actual row URL")
        elif kind == "notion_inline_like":
            original = canonical_url(item["original_url"])
            page = notion_page_url(item["row_url"])
            if (type(item.get("checked")) is not bool
                    or item.get("tags") != (["Interesting"] if item["checked"] else [])
                    or item.get("feedback_id") != "inline:" + fingerprint(page, original)
                    or page != notion_page_url(value["page_url"])):
                raise ValueError("Inline state, tags and exact-item identity must agree")
        elif kind == "chat":
            validate_chat_provenance(item)
            if (item.get("quote") != item["provenance"]["quote"]
                    or item["row_url"] != item["provenance"]["thread_url"]
                    or len(item.get("tags", [])) != 1 or item["tags"][0] not in LABELS.values()):
                raise ValueError("Chat item labels must retain their exact quote and source")
    for note in value.get("review_notes", []):
        if note.get("kind") not in KINDS - {"relevance"} or note.get("human_gold_eligible") is not False:
            raise ValueError("A review note cannot become a relevance gold label")
        if source_kind != "chat":
            raise ValueError("Review notes require a chat receipt")
        validate_chat_provenance(note)


def snapshot_meaning(value: dict) -> dict:
    value = copy.deepcopy(value)
    value.pop("observed_at", None)
    for entry in value.get("items", []) + value.get("review_notes", []):
        entry.pop("observed_at", None)
    return value


def record_snapshot(root: Path, value: dict) -> Path:
    validate_snapshot(value)
    run = value["run_id"]
    if not re.fullmatch(r"[A-Za-z0-9_-]+", run):
        raise ValueError("Invalid feedback run ID")
    date = timestamp(value["observed_at"]).astimezone(dt.timezone(dt.timedelta(hours=8))).date()
    path = root / "brief-workflow" / "feedback" / str(date) / (run + ".json")
    data = encoded(value)
    if value.get("source_kind") == "chat":
        # Re-capturing the same human turn tomorrow must not create a new label.
        for previous in (root / "brief-workflow" / "feedback").glob("*/" + run + ".json"):
            if snapshot_meaning(json.loads(previous.read_bytes())) == snapshot_meaning(value):
                return previous
            raise ValueError("The same human message cannot be relabeled; use a linked correction")
    if path.exists():
        if path.read_bytes() == data:
            return path
        raise ValueError("Feedback receipts are immutable; use a new correction record")
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("xb") as stream:
        stream.write(data)
    return path


def build_index(root: Path) -> dict:
    latest, notes, receipts = {}, {}, []
    page_controls, latest_pages = {}, {}
    queue_controls, latest_queue = set(), None
    chat_messages = {}
    for path in sorted((root / "brief-workflow" / "feedback").glob("*/*.json")):
        value = json.loads(path.read_bytes())
        validate_snapshot(value)
        observed = timestamp(value["observed_at"])
        receipts.append({"path": str(path.relative_to(root)),
                         "sha256": hashlib.sha256(path.read_bytes()).hexdigest()})
        if value.get("source_kind") == "chat":
            identity = value["run_id"]
            meaning = snapshot_meaning(value)
            if identity in chat_messages:
                if chat_messages[identity] != meaning:
                    raise ValueError("The same human message cannot be relabeled in another receipt")
                continue
            chat_messages[identity] = meaning
        if value.get("source_kind") == "notion_inline_like":
            page = notion_page_url(value["page_url"])
            controls = {i["feedback_id"] for i in value["items"]}
            complete = value["coverage"]["page_complete"]
            page_controls.setdefault(page, set()).update(controls)
            previous = latest_pages.get(page)
            if previous and observed == previous[0] and (controls, complete) != previous[1:3]:
                raise ValueError("Conflicting page coverage observations at the same time")
            if not previous or observed > previous[0]:
                latest_pages[page] = (observed, controls, complete, value["coverage"].get("reason"))
        if value.get("source_kind", "reading_queue") == "reading_queue":
            controls = {"queue:" + i["row_url"] for i in value.get("items", [])}
            queue_controls.update(controls)
            complete = value.get("coverage_complete")
            if latest_queue and observed == latest_queue[0] and (controls, complete) != latest_queue[1:]:
                raise ValueError("Conflicting Queue coverage observations at the same time")
            if not latest_queue or observed > latest_queue[0]:
                latest_queue = (observed, controls, complete)
        for item in value.get("items", []):
            key = ("queue:" + item["row_url"] if value.get("source_kind", "reading_queue") == "reading_queue"
                   else item["feedback_id"])
            old = latest.get(key)
            if old and observed == old[0] and item != old[1]:
                raise ValueError("Conflicting feedback observations at the same time")
            if not old or observed > old[0]:
                latest[key] = (observed, copy.deepcopy(item))
        for note in value.get("review_notes", []):
            key = note["feedback_id"]
            if key in notes and notes[key] != note:
                raise ValueError("A chat message was classified twice; keep a linked correction")
            notes[key] = note
    observed_at = max((t[0] for t in latest.values()), default=dt.datetime(1970, 1, 1, tzinfo=dt.timezone.utc))
    for note in notes.values():
        observed_at = max(observed_at, timestamp(note["observed_at"]))
    gaps = []
    for page, known in sorted(page_controls.items()):
        observed, current, complete, reason = latest_pages[page]
        observed_at = max(observed_at, observed)
        if not complete:
            gaps.append({"kind": "inline_read_incomplete", "page_url": page,
                         "page_observed_at": observed.isoformat(), "reason": reason})
        for key in sorted(known - current):
            latest[key][1]["observation_status"] = "missing_control" if complete else "unrefreshed"
            if complete:
                gaps.append({"kind": "missing_inline_control", "page_url": page, "feedback_id": key,
                             "page_observed_at": observed.isoformat(),
                             "last_control_observed_at": latest[key][0].isoformat()})
    if latest_queue:
        observed, current, complete = latest_queue
        observed_at = max(observed_at, observed)
        if complete is not True:
            gaps.append({"kind": "queue_read_incomplete" if complete is False else "queue_coverage_unknown",
                         "observed_at": observed.isoformat()})
        for key in sorted(queue_controls - current):
            latest[key][1]["observation_status"] = "missing_queue_row" if complete else "unrefreshed"
            if complete:
                gaps.append({"kind": "missing_queue_row", "feedback_id": key,
                             "observed_at": observed.isoformat()})
    for observed, item in latest.values():
        item["observed_at"] = observed.isoformat()
    snapshot = {"schema_version": 1, "observed_at": observed_at.isoformat(),
                "items": [v[1] for _, v in sorted(latest.items())],
                "review_notes": [v for _, v in sorted(notes.items())], "coverage_gaps": gaps}
    signals = feedback_signals(snapshot)
    subjects = {}
    for signal in signals["signals"]:
        for subject in signal["subjects"]:
            subjects.setdefault(subject, set()).add(signal["disposition"])
    conflicts = [s for s, ds in sorted(subjects.items())
                 if "conflict" in ds or ("avoid_exact" in ds and "prefer_exact" in ds)]
    return {**snapshot, "records": receipts, "record_set_complete": True,
            "feedback_coverage_complete": not gaps,
            "conflicting_subjects": conflicts,
            "limits": "Complete for listed repository records only. Refresh live pages and Queue separately. Missing/failed reads are not empty feedback. Conflicting sources need review; do not silently pick a winner."}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    for name in ("inline", "chat"):
        p = commands.add_parser(name)
        p.add_argument("--file", type=Path, required=True)
        p.add_argument("--observed-at", required=True)
        p.add_argument("--root", type=Path, required=True)
        if name == "inline":
            p.add_argument("--run-id", required=True)
    p = commands.add_parser("index")
    p.add_argument("--root", type=Path, required=True)
    args = parser.parse_args()
    if args.command == "index":
        result = build_index(args.root)
        atomic_write(args.root / "brief-workflow/feedback/index.json", encoded(result))
    else:
        data = json.loads(args.file.read_bytes())
        snapshot = (inline_snapshot(data, args.observed_at, args.run_id) if args.command == "inline"
                    else chat_snapshot(data, args.observed_at))
        result = {"path": str(record_snapshot(args.root, snapshot))}
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()

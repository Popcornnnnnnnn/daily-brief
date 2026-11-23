#!/usr/bin/env python3
"""Fetch sanitized counter-only provenance from private editorial receipts.

Only numeric counters, short 7-char revision SHA, verification booleans, and
window bounds are ever returned. Decisions, human feedback, page IDs, and URLs
are strictly excluded.
"""
from __future__ import annotations

import base64
import json
import shutil
import subprocess
from typing import Any


def fetch_sanitized_provenance(receipts_repo: str, report_date: str) -> dict[str, Any] | None:
    if not receipts_repo or not report_date:
        return None
    gh = shutil.which("gh") or "/opt/homebrew/bin/gh"
    try:
        listing_raw = subprocess.check_output(
            [gh, "api", f"repos/{receipts_repo}/contents/brief-workflow/records/{report_date}"],
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        entries = json.loads(listing_raw.decode("utf-8"))
    except Exception:
        return None

    daily_files = sorted(
        e["name"]
        for e in entries
        if isinstance(e, dict) and str(e.get("name", "")).startswith("daily_") and str(e.get("name", "")).endswith(".json")
    )
    if not daily_files:
        return None

    # Prefer the latest daily / correction receipt for that date.
    target = daily_files[-1]
    try:
        file_raw = subprocess.check_output(
            [
                gh,
                "api",
                f"repos/{receipts_repo}/contents/brief-workflow/records/{report_date}/{target}",
                "--jq",
                ".content",
            ],
            stderr=subprocess.DEVNULL,
            timeout=15,
        )
        decoded = base64.b64decode(file_raw.decode("utf-8").strip()).decode("utf-8")
        receipt = json.loads(decoded)
    except Exception:
        return None

    rev = str(receipt.get("input_revision") or "")
    short_rev = rev[:7] if len(rev) >= 7 else ""
    candidates = receipt.get("candidate_count")
    unreviewed = receipt.get("unreviewed_count")
    coverage = receipt.get("coverage") if isinstance(receipt.get("coverage"), dict) else {}
    source_gaps = coverage.get("source_gaps") or coverage.get("failed_sources") or []
    gap_count = len(source_gaps) if isinstance(source_gaps, list) else int(source_gaps or 0) if isinstance(source_gaps, int) else 0

    return {
        "candidates": int(candidates) if isinstance(candidates, int) else None,
        "unreviewed": int(unreviewed) if isinstance(unreviewed, int) else None,
        "revision": short_rev or None,
        "verified": bool(receipt.get("delivery_verified")),
        "coverage_complete": bool(coverage.get("source_coverage_complete")) if "source_coverage_complete" in coverage else None,
        "source_gap_count": gap_count,
    }

# Daily Brief — Action Radar Prompt Template (Sanitized)

Use this template for a follow-up sweep ~2 hours after the main brief (e.g. `06:15 <YOUR_TIMEZONE>`) to catch late-breaking GitHub releases, security advisories, or high-signal repositories that landed overnight without creating a duplicate edition.

---

1. Read today's already-published edition from `<YOUR_DESTINATION>` and today's main editorial receipt (`brief-workflow/records/YYYY-MM-DD/daily_*.json`).
2. Inspect fresh collector partitions at the latest pinned `main` commit of `<YOUR_STATE_REPO>` plus direct GitHub/advisory verification for high-priority watched repositories and security feeds.
3. Add **only** net-new, verified material updates that were absent from the morning brief:
   - Genuinely major developer CLI / agent runtime / SDK breakthroughs or substantial capability leaps — skip routine point releases, small fixes, setting renames, and incremental permission/resume tweaks for coding agents and agent harnesses.
   - Critical security advisories affecting actively tracked ecosystems.
   - High-signal open-source developer tools verified directly against primary repositories.
   - If nothing genuinely high-signal qualifies, stay quiet and omit Action Radar (leaving today's edition untouched) rather than padding with minor updates.
4. Merge new items into today's existing edition in place without altering unrelated sections, changing centered page width, or removing morning coverage. Follow the same readability rules: `###` item headings, 1-sentence plain-English leads before jargon, short sentences (~12–18 words), bullets for 3+ items, bold **Why it matters** / **What to do** takeaways, and plain inline source links (no bookmark cards or PDF embeds).
5. Record a separate `radar_*` receipt linking checked candidates and verify the updated edition via independent readback.

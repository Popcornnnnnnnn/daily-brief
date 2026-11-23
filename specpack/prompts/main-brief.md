# Daily Brief — Main Editorial Prompt Template (Sanitized)

Use this template in any agent harness (Codex, Claude Code, Antigravity, OpenCode, or cron CI) after customizing `<YOUR_TIMEZONE>`, `<YOUR_STATE_REPO>`, and `<YOUR_DESTINATION>`.

---

Generate today's **Daily AI & Developer Brief** at `04:00 <YOUR_TIMEZONE>`.

## Fixed Window & Scope

- News window: `[previous day 04:00, report day 04:00)` in `<YOUR_TIMEZONE>`. Do not slide the window if execution runs late.
- Recent open-source discovery & catch-up: look back `7` days (or `14` days for important missed releases), clearly stating actual release/discovery dates.
- Goal: open with **Today in 90 seconds** (3–4 high-signal takeaways), followed by full, evidence-backed reporting with no artificial item cap that drops important news.

## Step 1 — Read Reproducible Collector Inputs

1. Resolve the current `main` commit of `<YOUR_STATE_REPO>` and record the full 40-character SHA.
2. Read `brief-workflow/inputs/tech-ai/manifest.json` and `brief-workflow/inputs/open-source/manifest.json` at that exact commit.
3. Read partition `0001` of each family first before expanding high-volume families. Verify item counts and Git blob SHAs against the manifest. Keep unread partitions explicitly accounted as `unreviewed`.
4. Never trust `first_seen_at` or `sitemap` `<lastmod>` as publication time — verify the original source page before placing an item inside the 24-hour news window.

## Step 2 — Editorial Verification & Deduplication

- Check the last `7` published editions (up to `14` days for ongoing stories) by `owner/repo` and concrete event identity (`release`, `CVE`, `commit`, `announcement`). Never suppress a genuine new version or vulnerability just because the project appeared earlier.
- Link primary sources beside every factual claim (official engineering blog, release tag, security advisory, arXiv paper). State limits, alpha maturity, and self-reported benchmarks explicitly.
- Preserve genuine upstream source gaps (timeouts, HTTP errors, paywalls, pagination limits) in a concise `# Coverage note` at the end.

## Step 3 — Structure & Restraint

- `# Today in 90 seconds`: 3–4 crisp bullets capturing the day's biggest shifts.
- `# Tech & AI`: model releases, research, engineering postmortems, infrastructure, security advisories.
- `# Open Source & Developer Tools`: notable releases, Trending / Show HN discoveries (with verification of README, commits, and limits), and catch-up items.
- `# Business Opportunities` (optional): 1–2 concrete product hypotheses grounded strictly in today's cited evidence, including validation steps and primary risk.
- `# Worth opening`: curated short list of primary links.
- `# Coverage note`: exact collector/discovery coverage limits.

## Step 4 — Publish, Read Back & Record Receipt

1. Write the edition to `<YOUR_DESTINATION>` (and/or export Markdown to `editions/YYYY-MM-DD.md`).
2. Read back the published output independently to verify complete body, valid dates, working links, and media before marking `delivery_verified: true`.
3. Write one immutable editorial receipt under `brief-workflow/records/YYYY-MM-DD/<RUN_ID>.json` using `collectors/brief_workflow.py` and validate it before finishing.

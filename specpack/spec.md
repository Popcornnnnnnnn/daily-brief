# Daily Brief — Core Contract (`spec.md`)

Daily Brief turns noisy daily AI, security, and open-source feeds into a **reproducible, verifiable morning briefing** without silently dropping items, hallucinating timestamps, or leaking private reading context into public pages.

Everything in `decisions.md` and `prompts/` is advisory. Everything below is **strict contract**.

## Non-goals

- **No silent context truncation:** Never dump hundreds of raw items into one unpartitioned file and assume the agent read all of them.
- **No fabricated publication times:** Never treat discovery clock time, GitHub Trending snapshot time, or `sitemap.xml` `lastmod` elements as the original publication timestamp.
- **No fake quiet days:** Never report "nothing important happened" when upstream feeds timed out, returned HTTP errors, or hit pagination ceilings.
- **No silent preference overfitting:** Never auto-promote a single thumbs-up, UI tag, or unverified comment into global topic suppression.
- **No leaky public mirroring:** Never publish private workspaces, personal notes, local machine paths, or private reading-queue references to a public website or feed.

## Core invariants

### 1. Reproducible & Lossless Input Acquisition

- [1.1] **Commit-pinned manifest & bounded lossless partitions:** Collectors MUST export a machine-readable `manifest.json` referencing size-bounded, hash-verified partition files (`<= 25` items and `<= 22 KB` per partition) interleaved fairly across sources within each family, and editorial runs MUST pin one full 40-character Git commit SHA before reading so inputs never shift mid-run.
- [1.2] **Explicit unreviewed accounting:** Every candidate partition skipped by the editorial pass MUST keep explicit `unreviewed` accounting (`unreviewed_count = candidate_count - reviewed_decisions`) rather than appearing rejected or fully read.

### 2. Temporal Truthfulness & Coverage Transparency

- [2.1] **Strict news window vs. original publication date:** Daily reporting MUST evaluate items against a fixed timezone-aware reporting window using verified original publication timestamps; `first_seen_at`, Trending snapshots, and `sitemap` `lastmod` dates MUST trigger original-source verification or carry an explicit retrospective catch-up label.
- [2.2] **Honest upstream coverage disclosure:** Partial pagination, feed failures, subscription-only gates, and timeouts MUST remain recorded in `source_coverage` and summarized concisely in the published Coverage Note; cached candidates from earlier runs MUST NOT mark a currently failed source as complete.

### 3. Immutable Receipts & Verified Delivery

- [3.1] **Append-only editorial receipts & linked corrections:** Every run MUST emit a schema-validated immutable decision receipt (`selected`, `duplicate`, `no_material_change`, `out_of_scope`, `insufficient_evidence`, `deferred`, `unreviewed`) with concrete evidence URLs; subsequent fixes MUST create a linked correction record (`correction_of`) rather than mutating history in place.
- [3.2] **Independent readback before claiming delivery:** A run MUST NOT mark `delivery_verified: true` solely from local file creation or API write acceptance; it MUST read back the published destination and verify date, body completeness, sources, and media first.

### 4. Holdout-Gated Feedback & Public Privacy Boundary

- [4.1] **Provenance-bound explicit human feedback:** Only verifiable explicit human quotes or authenticated comment observations may enter the feedback ledger, separating item relevance from length/depth/correction notes and guarding derived index merges against concurrent write conflicts via expected SHA checks.
- [4.2] **Holdout gate before strategy promotion:** Editorial ranking changes MUST NOT activate automatically from ad-hoc feedback; strategy promotion requires at least `20` untouched human-labeled holdout examples with complete evaluation coverage, higher accuracy, and zero recall regression.
- [4.3] **Allowlist-only public publication & automated privacy gate:** Publicly exported editions and generated static site artifacts MUST strip non-public personal sections, local filesystem paths, auth tokens, and private workspace links, and MUST pass an automated zero-tolerance privacy gate before any public commit or deploy.

## Measurable success

- `Invariants % = 100` across `tests/test_brief_workflow.py`, `tests/test_brief_feedback.py`, `tests/test_execution_audit.py`, and `tests/test_publisher.py`.
- `python3 publisher/privacy_gate.py .` exits `0` with zero leaked internal links, local usernames, secrets, or disallowed personal sections.
- Any generated public website builds deterministically to static files with zero external runtime tracking or backend database requirements.

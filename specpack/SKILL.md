---
name: daily-brief
description: Build or adapt your own Daily Brief pipeline — reproducible partitioned collectors, honest timestamps, immutable editorial receipts, and an editorial reader surface — tailored via an adaptive Guided Taste Interview.
---

# How to build Daily Brief (Freedom by Default)

1. **Obey `spec.md` strictly.** Its `## Non-goals` and bracketed Core Invariants (`[1.1]`–`[4.3]`) are the non-negotiable engineering contract.
2. **Read `decisions.md` as field notes, not orders.** Reuse the provided Python collectors and Astro broadsheet directly if they fit, or rebuild any layer in your preferred stack as long as every invariant holds.
3. **Run an adaptive Personalisation Interview (`Guided Taste Interview`) before customizing or rebuilding.** Treat these axes as a compass — ask **one concise high-level question at a time**, offer 2–4 concrete directions with one marked `(Recommended)`, honor `"just use defaults"`, and stop as soon as the shape is clear:
   - **Delivery surface:** Public editorial website (Cloudflare Pages / GitHub Pages) *(Recommended)*, private Notion / Obsidian vault, email / RSS-first digest, or Slack / Discord morning wire.
   - **Coverage focus:** General AI labs + open-source developer tools + security advisories *(Recommended)*, plus optional domain lenses (e.g., Robotics, Systems/Compilers, Infra/Cloud, Local Models).
   - **Schedule & timezone:** Fixed daily 24-hour cutoff (e.g. `04:00` local time *(Recommended)*) vs. weekday-only or twice-daily action radar.
   - **Reading cadence & tone:** 90-second executive scan + deep evidence dive below *(Recommended)*, link-dense engineer changelog, or narrative weekly synthesis.
   - **Privacy boundary:** Local-only private notes vs. dual-layer public edition with automated privacy gate *(Recommended when publishing)*.
4. **Verify against `eval.md`** before shipping or enabling any automated schedule.

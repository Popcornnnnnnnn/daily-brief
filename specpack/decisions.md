# Daily Brief — Field Notes & Decisions (`decisions.md`)

Everything here records **real failure modes** we hit running a daily AI & open-source brief in production — shared as advice so you do not step on the same rakes.

## Reference choices (advisory)

- **Zero-dependency Python standard library core:** `brief_workflow.py`, `brief_feedback.py`, and both collectors run on stock Python 3 (`urllib`, `sqlite3`, `xml.etree.ElementTree`, `hashlib`, `fcntl`) plus `gh` CLI for GitHub API pagination. That makes local LaunchAgents and CI boringly reliable.
- **Git commit as the synchronization boundary:** Rather than running a local database server or message broker, local collectors push partitioned JSON to a state repo and the writer reads everything at one pinned 40-char commit SHA.
- **Astro static broadsheet for public publishing:** Editorial prose + Markdown frontmatter + pre-downloaded local media compiles to pure static HTML/RSS/JSON Feed/Markdown endpoints that deploy anywhere (Cloudflare Pages, GitHub Pages) with zero runtime attack surface.

## Dead ends & traps

1. **The `sitemap.xml` `lastmod` trap:** Lab blogs frequently bump `lastmod` across hundreds of old posts during a CMS migration or navigation update. Treating `lastmod` as `published_at` floods the next day's brief with two-year-old articles disguised as breaking news. Always mark sitemap discoveries `needs_original_date` until the article page itself confirms the date.
2. **One noisy repo eating the whole family:** A watched monorepo or security advisory feed can emit 500+ items overnight. If partitions aren't round-robin interleaved by `source_id`, a single repository fills every slot in partition `0001` and starves smaller high-signal sources.
3. **Silent partial reads look like empty days:** When a collector script catches an exception and writes an empty list, or when an agent only reads page 1 of a 40-page feed, downstream writers falsely conclude "quiet day." Separate *failed/partial source coverage* (`source_coverage_complete`) from *editorially unreviewed partitions* (`unreviewed_count`).
4. **Conflating "too shallow" with "not interested":** Early feedback schemas mapped every negative comment to topic down-ranking. In practice, readers frequently critique depth, missing benchmarks, or stale links on topics they *want* covered better. Separate `relevance` from `presentation`, `correction`, and `workflow` notes.
5. **Expiring Notion-hosted image URLs:** Notion file blocks return S3 pre-signed URLs that expire in roughly one hour. Mirroring Notion Markdown directly to a static website guarantees broken image embeds an hour later unless the exporter downloads and fingerprints file-backed media into `site/public/media/` at export time.
6. **Raw Notion mirror leaks:** A private reading hub naturally accumulates personal bookmarks, private workspace URLs, or personal follow-up toggles inside the same page or database. Never flip a private workspace public or mirror pages unfiltered — enforce an allowlist + automated build-blocking privacy gate.

## Taste calls

- **90-second opening, unlimited depth below:** Busy readers want the top 3–4 structural shifts in 90 seconds, followed by full evidence-backed analysis when a topic matters — without forcing arbitrary 5-item caps that drop real news.
- **Primary sources next to every claim:** Link directly to the official blog post, release tag, commit, or CVE rather than aggregator rewrites.
- **Light archival broadsheet over dark neon dashboards:** Long-form morning technical reading works best on high-contrast warm paper (`#F7F5F0`) with strong serif/mono typographic hierarchy and calm whitespace.
- **Visible editorial provenance:** Showing candidates scanned, pinned revision, verified readback timestamp, and disclosed source gaps builds real reader trust without adding UI clutter.

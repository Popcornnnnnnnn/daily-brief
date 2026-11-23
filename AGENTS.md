# Working Agreements (`daily-brief`)

This is the **public open-source repository** (`Popcornnnnnnnn/daily-brief`) for the Daily Brief collector engine, SpecPack v0.2 editorial architecture, sanitized public editions, and static editorial website (`site/`).

## Privacy & Separation Guardrails (Critical)

- **Never commit private data or runtime state.** Collector configs (`collectors/*/config.json`), SQLite databases, raw payloads, publisher configs (`publisher/config.json`), tokens, personal bookmarks, private workspace IDs, local usernames/paths, and unpublished personal sections MUST stay out of Git.
- **Keep private state separate.** Private collector snapshots, editorial receipts, and personal feedback stay in a separate private state repository and are never mirrored unfiltered here.
- **Run the privacy gate before every commit or push:**

```sh
python3 publisher/privacy_gate.py .
```

## Verification Gates

Run all three checks before committing or merging changes:

```sh
python3 -m unittest discover -s tests -v
python3 publisher/privacy_gate.py .
pnpm --dir site build
```

## Website & Modern Web Standards

- **Strictly light-mode editorial aesthetic** (`DESIGN.md` — archival paper `#F7F5F0`, charcoal ink `#141413`, international vermilion accent `#C83E2B`). Do not add a dark theme.
- Keep `site/` 100% static (`output: 'static'`) for Cloudflare Pages / GitHub Pages compatibility.
- Keep native `<dialog>`, CSS `@view-transition`, scroll-driven `animation-timeline`, and `hidden="until-found"` patterns compliant with `modern-web-guidance` (including `prefers-reduced-motion` and Safari fallbacks).

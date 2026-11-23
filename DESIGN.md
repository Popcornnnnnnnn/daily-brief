# Daily Brief — Design System (`DESIGN.md`)

**Theme stance:** Strictly **Light-Mode Archival Editorial** (Swiss Broadsheet × Modern Editorial Studio). No dark theme.

## 1. Color Tokens (WCAG AA Checked on Archival Paper)

| Token | Hex | Role |
| --- | --- | --- |
| `--paper` | `#F7F5F0` | Primary uncoated archival paper canvas |
| `--sheet` | `#FCFBF8` | Elevated dossier cards, search surface, modal sheet |
| `--tint` | `#EFECE4` | Quote/source card background, active hover row |
| `--ink` | `#141413` | Primary headline, body prose, heavy masthead rules |
| `--ink-secondary` | `#44433E` | Subheadings, lead summaries, secondary prose |
| `--ink-muted` | `#63615B` | Dates, issue numbers, telemetry kickers (≥ 5.6:1 on `#F7F5F0`) |
| `--rule` | `#DCD7CC` | 1px Swiss grid column & row hairlines |
| `--rule-strong` | `#141413` | 2px top masthead / section dividers |
| `--vermilion` | `#C83E2B` | Editorial highlight: live beacon, active nav underline, 90s badge, scroll bar |
| `--cobalt` | `#1B4D7A` | Primary source inline links |
| `--sage` | `#256347` | Verified readback / privacy gate / coverage OK indicator |
| `--amber` | `#9A5B13` | Partial upstream coverage / source gap notice |

## 2. Typography Trio (100% Self-Hosted Variable Fonts)

- **Display / Masthead / Issue Titles (`--font-serif`):** `Fraunces Variable` (`opsz` enabled, negative tracking `-0.03em` to `-0.02em`, crisp optical hairline contrast).
- **Body Prose & UI (`--font-sans`):** `Inter Variable` (`16.5px`–`17px` prose, `1.68` line height, `66ch` measure).
- **Telemetry / Issue Numbers / Code (`--font-mono`):** `JetBrains Mono Variable` (`tabular-nums`, `0.08em` tracked uppercase eyebrows).

## 3. Motion & Modern Web Platform Contract

- **Cross-Document View Transitions:** `@view-transition { navigation: auto; }` inside `@media (prefers-reduced-motion: no-preference)` plus directional `next`/`previous` transitions on issue navigation via `pagereveal`.
- **Scroll Progress Indicator:** Fixed 2px vermilion bar driven by CSS `animation-timeline: scroll()` (with JS scroll fallback where unsupported).
- **90-Second Mode (`hidden="until-found"`):** Toggling 90-Second Mode conceals deep-dive sections using `hidden="until-found"` so native browser Find-in-Page (`Cmd+F`) still matches and auto-expands via `beforematch`.
- **Native `<dialog closedby="any">`:** Powers both `⌘K` Quick Search and the Media Lightbox with `overscroll-behavior: contain` on both `dialog:modal` and `dialog::backdrop`.

import { Marked, Renderer, type Tokens } from 'marked';
import { slugifyHeading } from './editions';

function escapeHtml(value: string): string {
  return value
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#39;');
}

const STORY_PREFIX_RE = /^(Follow-up|Recent discovery|Recent hot project|Recent Trending|Catch-up|Action radar|Radar follow-up|Worth studying|Worth trying|Worth exploring|In development|Security catch-up|Practical catch-up)\s*(?:—|\||:)\s*(.+)$/i;

export function renderEditorialMarkdown(markdown: string): string {
  if (!markdown.trim()) return '';

  const renderer = new Renderer();

  renderer.heading = function ({ tokens, depth }: Tokens.Heading) {
    const raw = this.parser.parseInline(tokens);
    const plain = raw.replace(/<[^>]+>/g, '').trim();
    const id = slugifyHeading(plain);

    if (depth === 2) {
      return `<h2 id="${id}" class="section-band">${raw}</h2>\n`;
    }
    if (depth === 3) {
      const m = STORY_PREFIX_RE.exec(plain);
      if (m) {
        const kicker = escapeHtml(m[1]);
        // Re-render title cleanly preserving inline link if whole heading was a link
        const strippedRaw = raw.replace(
          new RegExp(`^((?:<a[^>]*>)?)${m[1].replace(/[.*+?^${}()|[\]\\]/g, '\\$&')}\\s*(?:—|\\||:)\\s*`, 'i'),
          '$1'
        );
        return `<h3 id="${id}" class="story-heading"><span class="story-kicker">${kicker}</span><span>${strippedRaw}</span></h3>\n`;
      }
      return `<h3 id="${id}" class="story-heading">${raw}</h3>\n`;
    }
    return `<h${depth} id="${id}">${raw}</h${depth}>\n`;
  };

  renderer.link = function ({ href, title, tokens }: Tokens.Link) {
    const label = this.parser.parseInline(tokens);
    const safeHref = href || '#';
    const isExternal = /^https?:\/\//i.test(safeHref);
    const attrs = isExternal ? ' target="_blank" rel="noopener noreferrer"' : '';
    const titleAttr = title ? ` title="${escapeHtml(title)}"` : '';
    return `<a href="${escapeHtml(safeHref)}"${titleAttr}${attrs}>${label}</a>`;
  };

  renderer.image = function ({ href, text }: Tokens.Image) {
    const safeSrc = escapeHtml(href || '');
    const safeAlt = escapeHtml((text || 'Edition illustration').trim());
    return `<figure class="editorial-figure" data-editorial-figure>
  <button type="button" class="editorial-figure-btn" data-lightbox-src="${safeSrc}" data-lightbox-alt="${safeAlt}" aria-label="Inspect image: ${safeAlt}">
    <img src="${safeSrc}" alt="${safeAlt}" loading="lazy" decoding="async" onerror="this.closest('figure').classList.add('is-broken'); this.replaceWith(Object.assign(document.createElement('div'),{className:'py-6 px-4 text-xs font-mono-telemetry text-[var(--ink-muted)]',textContent:'Image unavailable on source host — see linked reference below.'}))" />
  </button>
  <figcaption>
    <span>${safeAlt}</span>
    <span class="font-mono-telemetry text-[11px] uppercase tracking-wider shrink-0 text-[var(--ink-secondary)]">Inspect ↗</span>
  </figcaption>
</figure>\n`;
  };

  renderer.blockquote = function ({ tokens }: Tokens.Blockquote) {
    const bodyHtml = this.parser.parse(tokens).trim();
    // Detect exporter bookmark / media cards: <p><strong>Source:</strong> <a href="...">Label</a></p>
    const bookmarkMatch = /^<p><strong>(Source|Media \([^)]+\)|Embed):<\/strong>\s*<a\s+href="([^"]+)"[^>]*>([\s\S]+?)<\/a><\/p>$/i.exec(bodyHtml);
    if (bookmarkMatch) {
      const kind = bookmarkMatch[1].toUpperCase();
      const href = bookmarkMatch[2];
      const labelHtml = bookmarkMatch[3];
      let host = 'Reference';
      try {
        host = new URL(href).hostname.replace(/^www\./, '');
      } catch {
        host = 'Archive';
      }
      const isExternal = /^https?:\/\//i.test(href);
      const attrs = isExternal ? ' target="_blank" rel="noopener noreferrer"' : '';
      return `<a class="source-bookmark-card no-underline" href="${href}"${attrs}>
  <span class="min-w-0">
    <span class="block font-mono-telemetry text-[10px] uppercase tracking-[0.09em] text-[var(--vermilion)] mb-0.5">${escapeHtml(kind)} · ${escapeHtml(host)}</span>
    <span class="block text-[0.94rem] font-medium text-[var(--ink)] truncate">${labelHtml}</span>
  </span>
  <span class="font-mono-telemetry text-xs text-[var(--ink-muted)] shrink-0" aria-hidden="true">↗</span>
</a>\n`;
    }
    return `<blockquote>\n${bodyHtml}\n</blockquote>\n`;
  };

  renderer.table = function (token: Tokens.Table) {
    const headerHtml = token.header
      .map((cell) => `<th>${this.parser.parseInline(cell.tokens)}</th>`)
      .join('');
    const rowsHtml = token.rows
      .map(
        (row) =>
          `<tr>${row.map((cell) => `<td>${this.parser.parseInline(cell.tokens)}</td>`).join('')}</tr>`
      )
      .join('\n');
    return `<div class="table-scroll-wrap"><table><thead><tr>${headerHtml}</tr></thead><tbody>${rowsHtml}</tbody></table></div>\n`;
  };

  const instance = new Marked({ renderer, gfm: true, breaks: false });
  return instance.parse(markdown) as string;
}

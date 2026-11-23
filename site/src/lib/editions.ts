import { getCollection, type CollectionEntry } from 'astro:content';

export type EditionEntry = CollectionEntry<'editions'>;

export interface SearchEditionItem {
  issue_number: number;
  date: string;
  title: string;
  window: string;
  summary: string;
  takeaways: string[];
  tags: string[];
  sections: string[];
  lead_image: { src: string; alt: string } | null;
  story_count: number;
  source_count: number;
  reading_time_min: number;
  provenance?: {
    candidates?: number | null;
    unreviewed?: number | null;
    revision?: string | null;
    verified?: boolean;
    source_gap_count?: number;
  } | null;
}

export async function getAllEditionsSorted(): Promise<EditionEntry[]> {
  const entries = await getCollection('editions');
  return entries.sort((a, b) => b.data.date.localeCompare(a.data.date));
}

export function toSearchItem(entry: EditionEntry): SearchEditionItem {
  return {
    issue_number: entry.data.issue_number,
    date: entry.data.date,
    title: entry.data.title,
    window: entry.data.window,
    summary: entry.data.summary,
    takeaways: entry.data.takeaways,
    tags: entry.data.tags,
    sections: entry.data.sections,
    lead_image: entry.data.lead_image ?? null,
    story_count: entry.data.story_count,
    source_count: entry.data.source_count,
    reading_time_min: entry.data.reading_time_min,
    provenance: entry.data.provenance ?? null,
  };
}

export function formatLongDate(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return new Intl.DateTimeFormat('en-US', {
    weekday: 'long',
    month: 'long',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(dt);
}

export function formatShortDate(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, d));
  return new Intl.DateTimeFormat('en-US', {
    month: 'short',
    day: 'numeric',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(dt);
}

export function formatMonthHeading(dateStr: string): string {
  const [y, m] = dateStr.split('-').map(Number);
  const dt = new Date(Date.UTC(y, m - 1, 1));
  return new Intl.DateTimeFormat('en-US', {
    month: 'long',
    year: 'numeric',
    timeZone: 'UTC',
  }).format(dt);
}

export function formatIssueNo(num: number): string {
  return `№ ${String(num).padStart(3, '0')}`;
}

export function slugifyHeading(text: string): string {
  return text
    .toLowerCase()
    .replace(/[^a-z0-9\s-]/g, '')
    .trim()
    .replace(/\s+/g, '-')
    .replace(/-+/g, '-');
}

const SUMMARY_HEADING_RE = /^(?:today in 90 seconds|in 90 seconds|in 30 seconds|30-second summary|key points|today at a glance|highlights|summary)$/i;
const COVERAGE_HEADING_RE = /^(?:coverage|coverage note|coverage limits|coverage and verification notes|verification and timing)$/i;

export interface SplitEditionBody {
  summaryMarkdown: string;
  deepDiveMarkdown: string;
  coverageMarkdown: string;
  deepDiveSections: Array<{ id: string; title: string }>;
}

/**
 * Splits normalized Markdown body into:
 * 1) Opening executive 90-second summary (always visible)
 * 2) Deep-dive editorial sections (toggleable via 90-Second Mode `hidden="until-found"`)
 * 3) Coverage & verification note (rendered cleanly as an Audit Colophon)
 */
export function splitEditionBody(rawBody: string): SplitEditionBody {
  const lines = rawBody.trim().split('\n');
  const blocks: Array<{ heading: string; content: string }> = [];
  let currentHeading = '';
  let buf: string[] = [];

  for (const line of lines) {
    const m = /^##\s+(.+)$/.exec(line);
    if (m) {
      if (currentHeading || buf.join('\n').trim()) {
        blocks.push({ heading: currentHeading, content: buf.join('\n').trim() });
      }
      currentHeading = m[1].trim();
      buf = [];
    } else {
      buf.push(line);
    }
  }
  if (currentHeading || buf.join('\n').trim()) {
    blocks.push({ heading: currentHeading, content: buf.join('\n').trim() });
  }

  let summaryMarkdown = '';
  const deepDiveParts: string[] = [];
  const coverageParts: string[] = [];
  const deepDiveSections: Array<{ id: string; title: string }> = [];

  for (const blk of blocks) {
    if (!blk.heading && !blk.content) continue;
    if (!blk.heading && blk.content) {
      summaryMarkdown = summaryMarkdown ? `${summaryMarkdown}\n\n${blk.content}` : blk.content;
      continue;
    }
    if (!summaryMarkdown && SUMMARY_HEADING_RE.test(blk.heading)) {
      summaryMarkdown = blk.content;
      continue;
    }
    if (COVERAGE_HEADING_RE.test(blk.heading)) {
      coverageParts.push(blk.content);
      continue;
    }
    const id = slugifyHeading(blk.heading);
    deepDiveSections.push({ id, title: blk.heading });
    deepDiveParts.push(`## ${blk.heading}\n\n${blk.content}`);
  }

  return {
    summaryMarkdown,
    deepDiveMarkdown: deepDiveParts.join('\n\n'),
    coverageMarkdown: coverageParts.join('\n\n'),
    deepDiveSections,
  };
}

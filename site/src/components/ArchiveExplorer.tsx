import React, { useMemo, useState } from 'react';
import { AnimatePresence, motion, useReducedMotion } from 'motion/react';
import { ArrowUpRight, CheckCircle2, Search, X } from 'lucide-react';
import type { SearchEditionItem } from '../lib/editions';

interface Props {
  editions: SearchEditionItem[];
}

function monthLabel(dateStr: string): string {
  const [y, m] = dateStr.split('-').map(Number);
  return new Intl.DateTimeFormat('en-US', { month: 'long', year: 'numeric', timeZone: 'UTC' }).format(
    new Date(Date.UTC(y, m - 1, 1))
  );
}

function shortDate(dateStr: string): string {
  const [y, m, d] = dateStr.split('-').map(Number);
  return new Intl.DateTimeFormat('en-US', { month: 'short', day: 'numeric', timeZone: 'UTC' }).format(
    new Date(Date.UTC(y, m - 1, d))
  );
}

export default function ArchiveExplorer({ editions }: Props) {
  const prefersReducedMotion = useReducedMotion();
  const [query, setQuery] = useState('');
  const [selectedTag, setSelectedTag] = useState<string>('All');
  const [previewDate, setPreviewDate] = useState<string>(editions[0]?.date || '');

  const tagCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const item of editions) {
      for (const tag of item.tags) {
        counts.set(tag, (counts.get(tag) || 0) + 1);
      }
    }
    return Array.from(counts.entries()).sort((a, b) => b[1] - a[1]);
  }, [editions]);

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    return editions.filter((item) => {
      if (selectedTag !== 'All' && !item.tags.includes(selectedTag)) return false;
      if (!q) return true;
      const hay = [item.date, item.title, item.summary, ...item.takeaways, ...item.tags]
        .join(' ')
        .toLowerCase();
      return q.split(/\s+/).every((part) => hay.includes(part));
    });
  }, [editions, query, selectedTag]);

  const groupedByMonth = useMemo(() => {
    const groups: Array<{ month: string; items: SearchEditionItem[] }> = [];
    for (const item of filtered) {
      const m = monthLabel(item.date);
      const last = groups[groups.length - 1];
      if (!last || last.month !== m) {
        groups.push({ month: m, items: [item] });
      } else {
        last.items.push(item);
      }
    }
    return groups;
  }, [filtered]);

  const previewItem = useMemo(() => {
    return filtered.find((e) => e.date === previewDate) || filtered[0] || editions[0] || null;
  }, [filtered, previewDate, editions]);

  return (
    <div className="space-y-8">
      {/* Filter & Search Bar */}
      <div className="border-y border-[var(--ink)] py-4 space-y-4">
        <div className="flex flex-col sm:flex-row gap-3 sm:items-center sm:justify-between">
          <div className="relative flex-1 max-w-xl">
            <Search
              className="w-4 h-4 text-[var(--ink-muted)] absolute left-3.5 top-1/2 -translate-y-1/2 pointer-events-none"
              aria-hidden="true"
            />
            <input
              type="search"
              value={query}
              onChange={(e) => setQuery(e.target.value)}
              placeholder="Filter issues by model, library, CVE, or takeaway..."
              aria-label="Filter archive issues"
              className="w-full pl-10 pr-9 py-2.5 bg-[var(--sheet)] border border-[var(--rule)] focus:border-[var(--ink)] rounded-[3px] text-sm text-[var(--ink)] placeholder:text-[var(--ink-muted)] focus:outline-none"
            />
            {query && (
              <button
                type="button"
                onClick={() => setQuery('')}
                aria-label="Clear search filter"
                className="absolute right-2.5 top-1/2 -translate-y-1/2 p-1 text-[var(--ink-muted)] hover:text-[var(--ink)] cursor-pointer"
              >
                <X className="w-3.5 h-3.5" aria-hidden="true" />
              </button>
            )}
          </div>
          <div className="font-mono-telemetry text-xs text-[var(--ink-muted)] shrink-0">
            Showing <span className="text-[var(--ink)] font-semibold">{filtered.length}</span> of {editions.length} editions
          </div>
        </div>

        {/* Topic Filter Pills */}
        <div className="flex flex-wrap items-center gap-1.5" role="group" aria-label="Filter by topic">
          <button
            type="button"
            onClick={() => setSelectedTag('All')}
            aria-pressed={selectedTag === 'All'}
            className={`px-3 py-1 rounded-[2px] font-mono-telemetry text-[11px] uppercase tracking-wider border transition-colors cursor-pointer ${
              selectedTag === 'All'
                ? 'bg-[var(--ink)] text-[var(--sheet)] border-[var(--ink)]'
                : 'bg-[var(--sheet)] text-[var(--ink-secondary)] border-[var(--rule)] hover:border-[var(--ink)]'
            }`}
          >
            All ({editions.length})
          </button>
          {tagCounts.map(([tag, count]) => {
            const active = selectedTag === tag;
            return (
              <button
                key={tag}
                type="button"
                onClick={() => setSelectedTag(active ? 'All' : tag)}
                aria-pressed={active}
                className={`px-3 py-1 rounded-[2px] font-mono-telemetry text-[11px] uppercase tracking-wider border transition-colors cursor-pointer ${
                  active
                    ? 'bg-[var(--vermilion)] text-white border-[var(--vermilion)]'
                    : 'bg-[var(--sheet)] text-[var(--ink-secondary)] border-[var(--rule)] hover:border-[var(--ink)]'
                }`}
              >
                {tag} ({count})
              </button>
            );
          })}
        </div>
      </div>

      {/* 12-Col Swiss Ledger + Sticky Preview Dossier */}
      <div className="grid grid-cols-1 lg:grid-cols-12 gap-8 items-start">
        <div className="lg:col-span-7 space-y-10">
          {groupedByMonth.length === 0 ? (
            <div className="border border-[var(--rule)] bg-[var(--sheet)] p-10 text-center rounded-[4px]">
              <p className="font-serif-display text-xl text-[var(--ink)] mb-2">No matching editions</p>
              <p className="text-sm text-[var(--ink-muted)] mb-4">
                Try clearing the search query or switching back to all topics.
              </p>
              <button
                type="button"
                onClick={() => {
                  setQuery('');
                  setSelectedTag('All');
                }}
                className="px-4 py-2 text-xs font-mono-telemetry uppercase tracking-wider bg-[var(--ink)] text-[var(--sheet)] rounded-[2px] cursor-pointer"
              >
                Reset Filters
              </button>
            </div>
          ) : (
            groupedByMonth.map((group) => (
              <section key={group.month} aria-label={group.month}>
                <div className="flex items-baseline justify-between border-b-2 border-[var(--ink)] pb-2 mb-1">
                  <h2 className="font-serif-display text-2xl font-semibold tracking-tight text-[var(--ink)]">
                    {group.month}
                  </h2>
                  <span className="font-mono-telemetry text-[11px] uppercase tracking-wider text-[var(--ink-muted)]">
                    {group.items.length} issues
                  </span>
                </div>
                <ul className="divide-y divide-[var(--rule)]">
                  {group.items.map((item) => {
                    const isFocused = previewItem?.date === item.date;
                    return (
                      <li key={item.date}>
                        <a
                          href={`/issues/${item.date}/`}
                          onMouseEnter={() => setPreviewDate(item.date)}
                          onFocus={() => setPreviewDate(item.date)}
                          className={`group grid grid-cols-12 gap-3 items-baseline py-3.5 px-2.5 -mx-2.5 rounded-[3px] transition-colors no-underline ${
                            isFocused ? 'bg-[var(--tint)]' : 'hover:bg-[var(--tint)]'
                          }`}
                        >
                          <div className="col-span-3 sm:col-span-2 flex items-center gap-2">
                            <span
                              className={`font-mono-telemetry text-xs font-semibold ${
                                isFocused ? 'text-[var(--vermilion)]' : 'text-[var(--ink-muted)] group-hover:text-[var(--vermilion)]'
                              }`}
                            >
                              № {String(item.issue_number).padStart(3, '0')}
                            </span>
                          </div>
                          <div className="col-span-9 sm:col-span-7 min-w-0">
                            <span className="block font-serif-display text-[1.08rem] font-semibold text-[var(--ink)] leading-snug group-hover:text-[var(--vermilion)] transition-colors">
                              {item.title}
                            </span>
                            <span className="block text-xs text-[var(--ink-muted)] mt-1 sm:hidden">
                              {shortDate(item.date)} · {item.story_count} stories · {item.reading_time_min} min
                            </span>
                          </div>
                          <div className="hidden sm:flex col-span-3 items-center justify-end gap-2 font-mono-telemetry text-[11px] text-[var(--ink-muted)]">
                            <span>{shortDate(item.date)}</span>
                            <ArrowUpRight
                              className={`w-3.5 h-3.5 transition-transform ${
                                isFocused
                                  ? 'text-[var(--vermilion)] translate-x-0.5 -translate-y-0.5'
                                  : 'opacity-50 group-hover:opacity-100'
                              }`}
                              aria-hidden="true"
                            />
                          </div>
                        </a>
                      </li>
                    );
                  })}
                </ul>
              </section>
            ))
          )}
        </div>

        {/* Sticky Desktop Kinetic Dossier Preview */}
        <aside className="hidden lg:block lg:col-span-5 lg:sticky lg:top-20">
          {previewItem && (
            <AnimatePresence mode="wait" initial={false}>
              <motion.div
                key={previewItem.date}
                initial={prefersReducedMotion ? false : { opacity: 0.7, y: 6 }}
                animate={{ opacity: 1, y: 0 }}
                exit={prefersReducedMotion ? undefined : { opacity: 0.7, y: -4 }}
                transition={{ duration: 0.16, ease: 'easeOut' }}
                className="border border-[var(--ink)] bg-[var(--sheet)] rounded-[4px] overflow-hidden"
              >
                <div className="flex items-center justify-between px-5 py-3 border-b border-[var(--rule)] bg-[var(--tint)]">
                  <div className="flex items-center gap-2 font-mono-telemetry text-[11px]">
                    <span className="font-semibold text-[var(--vermilion)]">
                      ISSUE № {String(previewItem.issue_number).padStart(3, '0')}
                    </span>
                    <span className="text-[var(--rule)]">·</span>
                    <span className="text-[var(--ink-secondary)]">{previewItem.date}</span>
                  </div>
                  {previewItem.provenance?.verified && (
                    <span className="inline-flex items-center gap-1 font-mono-telemetry text-[10px] uppercase tracking-wider text-[var(--sage)]">
                      <CheckCircle2 className="w-3 h-3" aria-hidden="true" />
                      <span>Verified</span>
                    </span>
                  )}
                </div>

                {previewItem.lead_image?.src && (
                  <div className="border-b border-[var(--rule)] bg-white p-3 flex items-center justify-center max-h-52 overflow-hidden">
                    <img
                      src={previewItem.lead_image.src}
                      alt={previewItem.lead_image.alt || previewItem.title}
                      className="max-h-46 w-auto object-contain"
                      loading="lazy"
                    />
                  </div>
                )}

                <div className="p-5 space-y-4">
                  <h3 className="font-serif-display text-2xl font-semibold leading-tight tracking-tight text-[var(--ink)]">
                    {previewItem.title}
                  </h3>

                  <div className="flex flex-wrap gap-1.5">
                    {previewItem.tags.map((tag) => (
                      <span
                        key={tag}
                        className="font-mono-telemetry text-[10px] uppercase tracking-wider px-2 py-0.5 bg-[var(--tint)] border border-[var(--rule)] rounded-[2px] text-[var(--ink-secondary)]"
                      >
                        {tag}
                      </span>
                    ))}
                  </div>

                  <div className="space-y-2 border-t border-[var(--rule)] pt-3.5">
                    <div className="font-mono-telemetry text-[10px] uppercase tracking-[0.1em] text-[var(--ink-muted)]">
                      90-Second Highlights
                    </div>
                    <ul className="space-y-2 text-[0.86rem] leading-relaxed text-[var(--ink-secondary)]">
                      {previewItem.takeaways.slice(0, 3).map((bullet, i) => (
                        <li key={i} className="flex gap-2.5">
                          <span className="font-mono-telemetry text-[11px] font-semibold text-[var(--vermilion)] shrink-0 mt-0.5">
                            0{i + 1}
                          </span>
                          <span className="line-clamp-3">{bullet}</span>
                        </li>
                      ))}
                    </ul>
                  </div>

                  <div className="border-t border-[var(--rule)] pt-3.5 flex items-center justify-between gap-3">
                    <span className="font-mono-telemetry text-[11px] text-[var(--ink-muted)]">
                      {previewItem.story_count} stories · {previewItem.source_count} sources · {previewItem.reading_time_min}m read
                    </span>
                    <a
                      href={`/issues/${previewItem.date}/`}
                      className="inline-flex items-center gap-1.5 px-3.5 py-1.5 bg-[var(--ink)] hover:bg-[var(--vermilion)] text-[var(--sheet)] text-xs font-medium rounded-[2px] transition-colors no-underline"
                    >
                      <span>Read Issue</span>
                      <ArrowUpRight className="w-3.5 h-3.5" aria-hidden="true" />
                    </a>
                  </div>
                </div>
              </motion.div>
            </AnimatePresence>
          )}
        </aside>
      </div>
    </div>
  );
}

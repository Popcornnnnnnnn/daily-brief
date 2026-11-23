import React, { useEffect, useState } from 'react';
import { ArrowLeft, ArrowRight, Check, Copy, FileCode2, Layers, Zap } from 'lucide-react';

interface SectionLink {
  id: string;
  title: string;
}

interface NeighborLink {
  date: string;
  issue_number: number;
  title: string;
}

interface Props {
  date: string;
  sections: SectionLink[];
  prevIssue: NeighborLink | null;
  nextIssue: NeighborLink | null;
}

export default function EditionControls({ date, sections, prevIssue, nextIssue }: Props) {
  const [mode, setMode] = useState<'full' | 'brief'>('full');
  const [activeId, setActiveId] = useState<string>(sections[0]?.id || '');
  const [copied, setCopied] = useState(false);

  // Sync 90-Second Mode with #deep-dive-wrapper using hidden="until-found"
  // (modern-web-guidance: search-hidden-content)
  useEffect(() => {
    const wrapper = document.getElementById('deep-dive-wrapper');
    const banner = document.getElementById('brief-mode-banner');
    if (!wrapper) return;

    const supportsBeforeMatch = 'onbeforematch' in HTMLElement.prototype;

    if (mode === 'brief') {
      if (supportsBeforeMatch) {
        wrapper.setAttribute('hidden', 'until-found');
        banner?.classList.remove('hidden');
        banner?.classList.add('flex');
      } else {
        // Fallback for browsers lacking beforematch (e.g. Safari): keep full content
        // searchable and scroll smoothly to the 90-second brief.
        wrapper.removeAttribute('hidden');
        banner?.classList.add('hidden');
        banner?.classList.remove('flex');
        document.getElementById('summary-brief')?.scrollIntoView({ behavior: 'smooth', block: 'start' });
      }
    } else {
      wrapper.removeAttribute('hidden');
      banner?.classList.add('hidden');
      banner?.classList.remove('flex');
    }

    const onBeforeMatch = () => {
      setMode('full');
      wrapper.removeAttribute('hidden');
      banner?.classList.add('hidden');
      banner?.classList.remove('flex');
    };
    wrapper.addEventListener('beforematch', onBeforeMatch);

    const onModeEvent = (ev: Event) => {
      const detail = (ev as CustomEvent<'full' | 'brief'>).detail;
      if (detail === 'full' || detail === 'brief') {
        setMode(detail);
      }
    };
    window.addEventListener('db:set-mode', onModeEvent);

    return () => {
      wrapper.removeEventListener('beforematch', onBeforeMatch);
      window.removeEventListener('db:set-mode', onModeEvent);
    };
  }, [mode]);

  // Track active section heading in TOC
  useEffect(() => {
    if (!sections.length) return;
    const headings = sections
      .map((s) => document.getElementById(s.id))
      .filter((el): el is HTMLElement => el !== null);
    if (!headings.length) return;

    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries.filter((e) => e.isIntersecting);
        if (visible.length > 0) {
          setActiveId(visible[0].target.id);
        }
      },
      { rootMargin: '-18% 0px -65% 0px', threshold: 0 }
    );

    headings.forEach((h) => observer.observe(h));
    return () => observer.disconnect();
  }, [sections]);

  // Keyboard navigation between issues (Left / Right) and 90-Second shortcut ('9')
  useEffect(() => {
    const onKeyDown = (e: KeyboardEvent) => {
      const target = e.target as HTMLElement | null;
      const inEditable =
        target?.tagName === 'INPUT' ||
        target?.tagName === 'TEXTAREA' ||
        target?.isContentEditable ||
        document.querySelector('dialog[open]') !== null;
      if (inEditable || e.metaKey || e.ctrlKey || e.altKey) return;

      if (e.key === 'ArrowLeft' && prevIssue) {
        window.location.href = `/issues/${prevIssue.date}/`;
      } else if (e.key === 'ArrowRight' && nextIssue) {
        window.location.href = `/issues/${nextIssue.date}/`;
      } else if (e.key === '9') {
        setMode((prev) => (prev === 'full' ? 'brief' : 'full'));
      }
    };
    window.addEventListener('keydown', onKeyDown);
    return () => window.removeEventListener('keydown', onKeyDown);
  }, [prevIssue, nextIssue]);

  const handleCopyMarkdown = async () => {
    try {
      const res = await fetch(`/editions/${date}.md`);
      if (!res.ok) throw new Error('fetch failed');
      const md = await res.text();
      await navigator.clipboard.writeText(md);
      setCopied(true);
      setTimeout(() => setCopied(false), 2000);
    } catch {
      window.open(`/editions/${date}.md`, '_blank', 'noopener');
    }
  };

  return (
    <aside className="space-y-6" aria-label="Edition controls and chapters">
      {/* Reading Mode Toggle */}
      <div className="border border-[var(--rule)] bg-[var(--sheet)] p-3.5 rounded-[4px]">
        <div className="flex items-center justify-between mb-2">
          <span className="font-mono-telemetry text-[10px] uppercase tracking-[0.1em] text-[var(--ink-muted)]">
            Reading Mode
          </span>
          <span className="font-mono-telemetry text-[10px] text-[var(--ink-muted)] border border-[var(--rule)] px-1.5 py-0.5 rounded-sm">
            Key 9
          </span>
        </div>
        <div className="grid grid-cols-2 gap-1 p-1 bg-[var(--tint)] rounded-[3px] border border-[var(--rule)]">
          <button
            type="button"
            aria-pressed={mode === 'full'}
            onClick={() => setMode('full')}
            className={`inline-flex items-center justify-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-[2px] transition-colors cursor-pointer ${
              mode === 'full'
                ? 'bg-[var(--sheet)] text-[var(--ink)] shadow-xs border border-[var(--rule)]'
                : 'text-[var(--ink-secondary)] hover:text-[var(--ink)]'
            }`}
          >
            <Layers className="w-3.5 h-3.5" aria-hidden="true" />
            <span>Full Brief</span>
          </button>
          <button
            type="button"
            aria-pressed={mode === 'brief'}
            onClick={() => setMode('brief')}
            className={`inline-flex items-center justify-center gap-1.5 px-2.5 py-1.5 text-xs font-medium rounded-[2px] transition-colors cursor-pointer ${
              mode === 'brief'
                ? 'bg-[var(--ink)] text-[var(--sheet)] shadow-xs'
                : 'text-[var(--ink-secondary)] hover:text-[var(--ink)]'
            }`}
          >
            <Zap className="w-3.5 h-3.5 text-[var(--vermilion)]" aria-hidden="true" />
            <span>90s Scan</span>
          </button>
        </div>
        {mode === 'brief' && (
          <p className="mt-2.5 text-[11px] leading-relaxed text-[var(--ink-muted)]">
            Deep-dive sections collapsed. Use <span className="font-mono-telemetry">⌘F</span> anytime to jump straight to any hidden detail.
          </p>
        )}
      </div>

      {/* Section Index */}
      {sections.length > 0 && mode === 'full' && (
        <nav aria-label="In this edition" className="border-t border-[var(--rule)] pt-4">
          <div className="font-mono-telemetry text-[10px] uppercase tracking-[0.1em] text-[var(--ink-muted)] mb-2.5">
            Sections in Issue
          </div>
          <ul className="space-y-1">
            {sections.map((sec) => {
              const active = sec.id === activeId;
              return (
                <li key={sec.id}>
                  <a
                    href={`#${sec.id}`}
                    className={`block py-1.5 pl-3 border-l-2 text-[0.83rem] leading-snug transition-colors no-underline ${
                      active
                        ? 'border-[var(--vermilion)] text-[var(--ink)] font-semibold'
                        : 'border-[var(--rule)] text-[var(--ink-secondary)] hover:text-[var(--ink)] hover:border-[var(--ink)]'
                    }`}
                  >
                    {sec.title}
                  </a>
                </li>
              );
            })}
          </ul>
        </nav>
      )}

      {/* Markdown & Agent Actions */}
      <div className="border-t border-[var(--rule)] pt-4 space-y-2">
        <div className="font-mono-telemetry text-[10px] uppercase tracking-[0.1em] text-[var(--ink-muted)] mb-2">
          Plaintext &amp; Feeds
        </div>
        <button
          type="button"
          onClick={handleCopyMarkdown}
          className="w-full inline-flex items-center justify-between px-3 py-2 text-xs font-medium bg-[var(--sheet)] hover:bg-[var(--tint)] border border-[var(--rule)] hover:border-[var(--ink)] rounded-[3px] transition-colors cursor-pointer text-[var(--ink)]"
        >
          <span className="inline-flex items-center gap-2">
            {copied ? (
              <Check className="w-3.5 h-3.5 text-[var(--sage)]" aria-hidden="true" />
            ) : (
              <Copy className="w-3.5 h-3.5 text-[var(--ink-muted)]" aria-hidden="true" />
            )}
            <span>{copied ? 'Copied Markdown' : 'Copy as Markdown'}</span>
          </span>
          <span className="font-mono-telemetry text-[10px] text-[var(--ink-muted)]">.md</span>
        </button>

        <a
          href={`/editions/${date}.md`}
          className="w-full inline-flex items-center justify-between px-3 py-2 text-xs font-medium bg-[var(--sheet)] hover:bg-[var(--tint)] border border-[var(--rule)] hover:border-[var(--ink)] rounded-[3px] transition-colors no-underline text-[var(--ink)]"
        >
          <span className="inline-flex items-center gap-2">
            <FileCode2 className="w-3.5 h-3.5 text-[var(--ink-muted)]" aria-hidden="true" />
            <span>Open Raw Markdown</span>
          </span>
          <span className="font-mono-telemetry text-[10px] text-[var(--ink-muted)]">↗</span>
        </a>
      </div>

      {/* Neighbor Edition Switcher */}
      <div className="border-t border-[var(--rule)] pt-4 grid grid-cols-2 gap-2">
        {prevIssue ? (
          <a
            href={`/issues/${prevIssue.date}/`}
            title={`Older: ${prevIssue.title}`}
            className="group p-2.5 border border-[var(--rule)] bg-[var(--sheet)] hover:bg-[var(--tint)] hover:border-[var(--ink)] rounded-[3px] no-underline transition-colors"
          >
            <span className="inline-flex items-center gap-1 font-mono-telemetry text-[10px] uppercase tracking-wider text-[var(--ink-muted)] group-hover:text-[var(--vermilion)]">
              <ArrowLeft className="w-3 h-3" aria-hidden="true" />
              <span>Prev</span>
            </span>
            <span className="block font-mono-telemetry text-xs font-semibold text-[var(--ink)] mt-0.5">
              № {String(prevIssue.issue_number).padStart(3, '0')}
            </span>
          </a>
        ) : (
          <div className="p-2.5 border border-[var(--rule)] opacity-45 rounded-[3px]">
            <span className="font-mono-telemetry text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
              First Issue
            </span>
          </div>
        )}

        {nextIssue ? (
          <a
            href={`/issues/${nextIssue.date}/`}
            title={`Newer: ${nextIssue.title}`}
            className="group p-2.5 border border-[var(--rule)] bg-[var(--sheet)] hover:bg-[var(--tint)] hover:border-[var(--ink)] rounded-[3px] no-underline text-right transition-colors"
          >
            <span className="inline-flex items-center justify-end gap-1 font-mono-telemetry text-[10px] uppercase tracking-wider text-[var(--ink-muted)] group-hover:text-[var(--vermilion)]">
              <span>Next</span>
              <ArrowRight className="w-3 h-3" aria-hidden="true" />
            </span>
            <span className="block font-mono-telemetry text-xs font-semibold text-[var(--ink)] mt-0.5">
              № {String(nextIssue.issue_number).padStart(3, '0')}
            </span>
          </a>
        ) : (
          <div className="p-2.5 border border-[var(--rule)] opacity-45 rounded-[3px] text-right">
            <span className="font-mono-telemetry text-[10px] uppercase tracking-wider text-[var(--ink-muted)]">
              Latest
            </span>
          </div>
        )}
      </div>
    </aside>
  );
}

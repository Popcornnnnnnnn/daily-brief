import React, { useEffect, useMemo, useRef, useState } from 'react';
import { ArrowUpRight, Search, X } from 'lucide-react';
import type { SearchEditionItem } from '../lib/editions';

interface Props {
  editions: SearchEditionItem[];
}

function attachClosedByFallback(dialog: HTMLDialogElement) {
  if ('closedBy' in HTMLDialogElement.prototype) return () => {};
  const outside = (e: PointerEvent) => {
    const r = dialog.getBoundingClientRect();
    return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
  };
  let pressedOutside = false;
  const onDown = (e: PointerEvent) => {
    pressedOutside = outside(e);
  };
  const onUp = (e: PointerEvent) => {
    if (pressedOutside && outside(e)) dialog.close();
  };
  dialog.addEventListener('pointerdown', onDown);
  dialog.addEventListener('pointerup', onUp);
  return () => {
    dialog.removeEventListener('pointerdown', onDown);
    dialog.removeEventListener('pointerup', onUp);
  };
}

export default function CommandSearch({ editions }: Props) {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const [query, setQuery] = useState('');
  const [activeIndex, setActiveIndex] = useState(0);

  const openModal = () => {
    const dlg = dialogRef.current;
    if (!dlg) return;
    if (!dlg.open) {
      dlg.showModal();
    }
    requestAnimationFrame(() => inputRef.current?.focus());
  };

  useEffect(() => {
    const dlg = dialogRef.current;
    if (!dlg) return;

    const cleanupFallback = attachClosedByFallback(dlg);

    const onClose = () => {
      dlg.returnValue = '';
      setQuery('');
      setActiveIndex(0);
    };
    dlg.addEventListener('close', onClose);

    const onTriggerClick = (e: Event) => {
      const target = e.target as HTMLElement | null;
      if (target?.closest('[data-open-search]')) {
        e.preventDefault();
        openModal();
      }
    };
    document.addEventListener('click', onTriggerClick);

    const onKeyDown = (e: KeyboardEvent) => {
      const tag = (e.target as HTMLElement | null)?.tagName;
      const inInput = tag === 'INPUT' || tag === 'TEXTAREA' || (e.target as HTMLElement | null)?.isContentEditable;
      if ((e.metaKey || e.ctrlKey) && e.key.toLowerCase() === 'k') {
        e.preventDefault();
        if (dlg.open) dlg.close();
        else openModal();
      } else if (!inInput && e.key === '/' && !dlg.open) {
        e.preventDefault();
        openModal();
      }
    };
    window.addEventListener('keydown', onKeyDown);

    return () => {
      cleanupFallback();
      dlg.removeEventListener('close', onClose);
      document.removeEventListener('click', onTriggerClick);
      window.removeEventListener('keydown', onKeyDown);
    };
  }, []);

  const results = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return editions.slice(0, 10);
    return editions
      .filter((item) => {
        const hay = [
          item.date,
          `issue ${item.issue_number}`,
          item.title,
          item.summary,
          ...item.takeaways,
          ...item.tags,
        ]
          .join(' ')
          .toLowerCase();
        return q.split(/\s+/).every((part) => hay.includes(part));
      })
      .slice(0, 14);
  }, [editions, query]);

  useEffect(() => {
    setActiveIndex(0);
  }, [query]);

  const onInputKeyDown = (e: React.KeyboardEvent<HTMLInputElement>) => {
    if (e.key === 'ArrowDown') {
      e.preventDefault();
      setActiveIndex((prev) => Math.min(prev + 1, Math.max(0, results.length - 1)));
    } else if (e.key === 'ArrowUp') {
      e.preventDefault();
      setActiveIndex((prev) => Math.max(prev - 1, 0));
    } else if (e.key === 'Enter' && results[activeIndex]) {
      e.preventDefault();
      window.location.href = `/issues/${results[activeIndex].date}/`;
    }
  };

  return (
    <dialog
      ref={dialogRef}
      id="command-search-dialog"
      className="editorial-dialog"
      closedby="any"
      aria-labelledby="command-search-title"
    >
      <div className="flex items-center gap-3 px-4 py-3.5 border-b border-[var(--rule)] bg-[var(--sheet)]">
        <Search className="w-4 h-4 text-[var(--ink-muted)] shrink-0" aria-hidden="true" />
        <h2 id="command-search-title" className="sr-only">
          Search Daily Brief Archive
        </h2>
        <input
          ref={inputRef}
          type="search"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          onKeyDown={onInputKeyDown}
          placeholder="Search 51 editions by topic, model, repository, CVE, or date..."
          aria-label="Search Daily Brief editions"
          autofocus
          className="w-full bg-transparent text-[0.95rem] text-[var(--ink)] placeholder:text-[var(--ink-muted)] focus:outline-none"
        />
        <form method="dialog">
          <button
            type="submit"
            value="close"
            aria-label="Close search"
            className="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-mono-telemetry uppercase tracking-wider text-[var(--ink-muted)] hover:text-[var(--ink)] border border-[var(--rule)] rounded-sm cursor-pointer"
          >
            <X className="w-3.5 h-3.5" aria-hidden="true" />
            <span>ESC</span>
          </button>
        </form>
      </div>

      <div className="max-h-[60vh] overflow-y-auto divide-y divide-[var(--rule)] bg-[var(--sheet)]">
        {results.length === 0 ? (
          <div className="px-5 py-10 text-center text-sm text-[var(--ink-muted)]">
            No editions match <span className="font-mono-telemetry text-[var(--ink)]">"{query}"</span>.
          </div>
        ) : (
          results.map((item, idx) => {
            const isActive = idx === activeIndex;
            return (
              <a
                key={item.date}
                href={`/issues/${item.date}/`}
                onMouseEnter={() => setActiveIndex(idx)}
                className={`flex items-start justify-between gap-4 px-5 py-3.5 transition-colors no-underline ${
                  isActive ? 'bg-[var(--tint)]' : 'hover:bg-[var(--tint)]'
                }`}
              >
                <div className="min-w-0">
                  <div className="flex flex-wrap items-center gap-2 mb-1">
                    <span className="font-mono-telemetry text-[11px] font-semibold text-[var(--vermilion)]">
                      № {String(item.issue_number).padStart(3, '0')}
                    </span>
                    <span className="font-mono-telemetry text-[11px] text-[var(--ink-muted)]">
                      {item.date}
                    </span>
                    <span className="text-[var(--rule)]">·</span>
                    <span className="font-mono-telemetry text-[11px] text-[var(--ink-muted)]">
                      {item.tags.slice(0, 2).join(' / ')}
                    </span>
                  </div>
                  <div className="font-serif-display text-[1.06rem] font-semibold text-[var(--ink)] leading-snug">
                    {item.title}
                  </div>
                  <p className="text-[0.83rem] text-[var(--ink-secondary)] line-clamp-1 mt-0.5">
                    {item.summary}
                  </p>
                </div>
                <ArrowUpRight
                  className={`w-4 h-4 shrink-0 mt-1 transition-transform ${
                    isActive ? 'text-[var(--vermilion)] translate-x-0.5 -translate-y-0.5' : 'text-[var(--ink-muted)]'
                  }`}
                  aria-hidden="true"
                />
              </a>
            );
          })
        )}
      </div>

      <div className="flex items-center justify-between px-4 py-2.5 border-t border-[var(--rule)] bg-[var(--paper)] text-[11px] font-mono-telemetry text-[var(--ink-muted)]">
        <span>↑↓ Navigate · ↵ Open issue</span>
        <a href="/archive/" className="text-[var(--cobalt)] hover:text-[var(--vermilion)]">
          Full Archive ({editions.length}) →
        </a>
      </div>
    </dialog>
  );
}

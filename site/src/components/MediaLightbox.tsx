import React, { useEffect, useRef, useState } from 'react';
import { X } from 'lucide-react';

export default function MediaLightbox() {
  const dialogRef = useRef<HTMLDialogElement>(null);
  const [media, setMedia] = useState<{ src: string; alt: string } | null>(null);

  useEffect(() => {
    const dlg = dialogRef.current;
    if (!dlg) return;

    let cleanupFallback = () => {};
    if (!('closedBy' in HTMLDialogElement.prototype)) {
      const outside = (e: PointerEvent) => {
        const r = dlg.getBoundingClientRect();
        return e.clientX < r.left || e.clientX > r.right || e.clientY < r.top || e.clientY > r.bottom;
      };
      let pressedOutside = false;
      const onDown = (e: PointerEvent) => {
        pressedOutside = outside(e);
      };
      const onUp = (e: PointerEvent) => {
        if (pressedOutside && outside(e)) dlg.close();
      };
      dlg.addEventListener('pointerdown', onDown);
      dlg.addEventListener('pointerup', onUp);
      cleanupFallback = () => {
        dlg.removeEventListener('pointerdown', onDown);
        dlg.removeEventListener('pointerup', onUp);
      };
    }

    const onClose = () => {
      dlg.returnValue = '';
      setMedia(null);
    };
    dlg.addEventListener('close', onClose);

    const onDocClick = (e: Event) => {
      const btn = (e.target as HTMLElement | null)?.closest<HTMLElement>('[data-lightbox-src]');
      if (!btn) return;
      const src = btn.getAttribute('data-lightbox-src');
      const alt = btn.getAttribute('data-lightbox-alt') || 'Edition media';
      if (!src) return;
      e.preventDefault();
      setMedia({ src, alt });
      if (!dlg.open) dlg.showModal();
    };
    document.addEventListener('click', onDocClick);

    return () => {
      cleanupFallback();
      dlg.removeEventListener('close', onClose);
      document.removeEventListener('click', onDocClick);
    };
  }, []);

  return (
    <dialog
      ref={dialogRef}
      id="media-lightbox-dialog"
      className="editorial-dialog editorial-lightbox"
      closedby="any"
      aria-labelledby="lightbox-caption"
    >
      <div className="flex items-center justify-between gap-4 px-4 py-2.5 border-b border-[var(--rule)] bg-[var(--sheet)]">
        <h2 id="lightbox-caption" className="font-mono-telemetry text-xs text-[var(--ink-secondary)] truncate">
          {media?.alt || 'Figure inspection'}
        </h2>
        <form method="dialog">
          <button
            type="submit"
            value="close"
            autofocus
            aria-label="Close image inspection"
            className="inline-flex items-center gap-1 px-2 py-1 text-[11px] font-mono-telemetry uppercase tracking-wider text-[var(--ink-muted)] hover:text-[var(--ink)] border border-[var(--rule)] rounded-sm cursor-pointer"
          >
            <X className="w-3.5 h-3.5" aria-hidden="true" />
            <span>Close</span>
          </button>
        </form>
      </div>
      <div className="p-4 sm:p-6 bg-white flex items-center justify-center max-h-[82vh] overflow-auto">
        {media && (
          <img
            src={media.src}
            alt={media.alt}
            className="max-w-full max-h-[74vh] w-auto h-auto object-contain"
          />
        )}
      </div>
    </dialog>
  );
}

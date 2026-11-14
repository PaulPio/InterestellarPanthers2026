import { useEffect, useRef, type ReactNode } from "react";

interface Props {
  title: string;
  children: ReactNode;
  confirmLabel: string;
  onConfirm: () => void;
  onCancel: () => void;
}

/**
 * An in-app confirmation dialog (never the browser's `confirm()`). Focus starts on Cancel, Escape
 * cancels, and Tab stays inside the dialog.
 */
export function ConfirmDialog({ title, children, confirmLabel, onConfirm, onCancel }: Props) {
  const cancelRef = useRef<HTMLButtonElement>(null);
  const dialogRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    const previous = document.activeElement as HTMLElement | null;
    cancelRef.current?.focus();
    return () => previous?.focus?.();
  }, []);

  function onKeyDown(e: React.KeyboardEvent) {
    if (e.key === "Escape") {
      e.stopPropagation();
      onCancel();
    } else if (e.key === "Tab" && dialogRef.current) {
      const focusable = dialogRef.current.querySelectorAll<HTMLElement>("button");
      const first = focusable[0];
      const last = focusable[focusable.length - 1];
      if (e.shiftKey && document.activeElement === first) {
        e.preventDefault();
        last.focus();
      } else if (!e.shiftKey && document.activeElement === last) {
        e.preventDefault();
        first.focus();
      }
    }
  }

  return (
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-black/70 p-4">
      {/* The black-and-yellow hazard border: used only for actions that cannot be undone. */}
      <div className="w-full max-w-[32.5rem] bg-[repeating-linear-gradient(-45deg,#f2c200_0_12px,#0a0a0a_12px_24px)] p-1.5">
        <div
          ref={dialogRef}
          role="alertdialog"
          aria-modal="true"
          aria-labelledby="confirm-title"
          aria-describedby="confirm-body"
          onKeyDown={onKeyDown}
          className="bg-panel p-6"
        >
          <p className="micro-label">Cannot be undone</p>
          <h2 id="confirm-title" className="mt-2 text-xl font-semibold">
            {title}
          </h2>
          <div id="confirm-body" className="mt-2.5 text-[0.9375rem] leading-[1.375rem] text-muted">
            {children}
          </div>
          <div className="mt-6 flex flex-wrap justify-end gap-3">
            <button
              ref={cancelRef}
              type="button"
              onClick={onCancel}
              className="h-14 min-w-[7.5rem] border border-line-strong px-5 text-[0.9375rem] font-semibold"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              className="h-14 min-w-[10.5rem] bg-ink px-5 text-[0.9375rem] font-semibold text-surface"
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

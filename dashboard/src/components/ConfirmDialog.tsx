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
    <div className="fixed inset-0 z-50 flex items-center justify-center bg-[rgba(2,5,16,0.78)] p-4 backdrop-blur-sm">
      <div
        ref={dialogRef}
        role="alertdialog"
        aria-modal="true"
        aria-labelledby="confirm-title"
        aria-describedby="confirm-body"
        onKeyDown={onKeyDown}
        className="w-full max-w-[30rem] overflow-hidden border border-line-strong bg-panel shadow-[0_24px_64px_rgba(0,0,0,0.6)]"
      >
        {/* The black-and-yellow hazard strip: used only for actions that cannot be undone. */}
        <div
          aria-hidden="true"
          className="h-2 bg-[repeating-linear-gradient(-45deg,#f2c200_0_10px,#0a0a0a_10px_20px)]"
        />
        <div className="bg-[linear-gradient(180deg,rgba(240,68,58,0.12),transparent_55%)] p-6">
          <p className="flex items-center gap-2">
            <svg width="16" height="16" viewBox="0 0 12 12" aria-hidden="true">
              <path d="M6 0.8 L11.6 11 L0.4 11 Z" fill="var(--yellow-mark)" />
            </svg>
            <span className="micro-label">Cannot be undone</span>
          </p>
          <h2 id="confirm-title" className="mt-2.5 text-xl leading-7 font-semibold">
            {title}
          </h2>
          <div id="confirm-body" className="mt-2 text-[0.9375rem] leading-[1.375rem] text-muted">
            {children}
          </div>
          <div className="mt-6 flex flex-wrap justify-end gap-3">
            <button
              ref={cancelRef}
              type="button"
              onClick={onCancel}
              className="btn h-12 min-w-[7.5rem] px-5 text-[0.9375rem]"
            >
              Cancel
            </button>
            <button
              type="button"
              onClick={onConfirm}
              className="btn btn-danger h-12 min-w-[10.5rem] px-5 text-[0.9375rem]"
            >
              {confirmLabel}
            </button>
          </div>
        </div>
      </div>
    </div>
  );
}

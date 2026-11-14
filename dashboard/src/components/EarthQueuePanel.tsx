import { useState } from "react";
import { api } from "../api/client";
import type { EarthQueue, EarthQueueItem } from "../api/types.gen";
import { useStatusContext } from "../hooks/StatusContext";
import { usePolling } from "../hooks/usePolling";
import { formatDuration } from "../lib/format";

const TIER_WORD: Record<EarthQueueItem["tier"], string> = {
  crew_only: "Crew only",
  flight_surgeon: "Flight surgeon",
  urgent: "Urgent",
};

function stateWord(item: EarthQueueItem): string {
  if (item.sent) return "Sent";
  return item.approved ? "Approved, waiting for the next window" : "Waiting for approval";
}

/** Dashed ring while waiting, a check once approved, a double check once sent. */
function StateGlyph({ item }: { item: EarthQueueItem }) {
  if (item.sent)
    return (
      <svg width="12" height="12" viewBox="0 0 14 14" aria-hidden="true">
        <path
          d="M1 7.4 L4 10.5 L9.5 3.5 M6 10.5 L13 3.5"
          fill="none"
          stroke="var(--muted)"
          strokeWidth="1.6"
        />
      </svg>
    );
  if (item.approved)
    return (
      <svg width="12" height="12" viewBox="0 0 14 14" aria-hidden="true">
        <path d="M2 7.4 L5.6 11 L12 3.5" fill="none" stroke="var(--muted)" strokeWidth="1.8" />
      </svg>
    );
  return (
    <svg width="12" height="12" viewBox="0 0 14 14" aria-hidden="true">
      <circle
        cx="7"
        cy="7"
        r="5.5"
        fill="none"
        stroke="var(--ink)"
        strokeWidth="1.5"
        strokeDasharray="2.5 2"
      />
    </svg>
  );
}

/** Summaries queued for Earth. Nothing leaves the vehicle until a crew member approves it. */
export function EarthQueuePanel() {
  const { data: status } = useStatusContext();
  const { data: queue, error, refresh } = usePolling<EarthQueue>((s) => api.earthQueue(s), 10_000);
  const [pending, setPending] = useState<Set<string>>(new Set());
  const [confirmed, setConfirmed] = useState<Record<string, EarthQueueItem>>({});
  const [failed, setFailed] = useState<string | null>(null);

  async function approve(item: EarthQueueItem) {
    setFailed(null);
    setPending((p) => new Set(p).add(item.id)); // optimistic: show it approved at once
    try {
      const approved = await api.approve(item.id);
      setConfirmed((c) => ({ ...c, [item.id]: approved })); // keep it approved until the poll agrees
      refresh();
    } catch {
      setFailed(item.id); // roll back: the item is shown as it really is
    } finally {
      setPending((p) => {
        const next = new Set(p);
        next.delete(item.id);
        return next;
      });
    }
  }

  const items = [...(queue?.items ?? [])].sort(
    (a, b) => Number(a.approved) - Number(b.approved) || b.t - a.t,
  );
  const untilWindow =
    queue && status ? formatDuration(Math.max(queue.next_window_t - status.t, 0)) : null;

  return (
    <section aria-label="Earth queue" className="pane pane-flush">
      <div className="panel-head flex-wrap">
        <h2 className="text-xs font-semibold">
          <span className="micro-label mr-2">Downlink</span>
          Earth queue
        </h2>
        <p
          data-testid="window-countdown"
          className="inline-flex h-5 items-center border border-line-strong px-1.5 text-[0.6875rem]"
        >
          {untilWindow ? (
            <>
              Next comms window in&nbsp;
              <span className="readout font-semibold">{untilWindow}</span>
            </>
          ) : (
            "Next comms window unknown"
          )}
        </p>
      </div>
      {failed ? (
        <p
          role="alert"
          className="m-2 border border-level-red-mark bg-level-red-bg px-2 py-1 text-xs text-ink"
        >
          Could not approve {failed}. It is still waiting for approval. Try again.
        </p>
      ) : null}
      {!queue ? (
        <p className="p-2 text-muted">
          {error ? "Could not load the queue." : "Loading the queue."}
        </p>
      ) : items.length === 0 ? (
        <p className="p-2 text-muted">Nothing is waiting for Earth.</p>
      ) : (
        <ul className="flex flex-col">
          {items.map((item) => {
            const known = item.approved ? item : (confirmed[item.id] ?? item);
            const optimistic = pending.has(item.id);
            const shown: EarthQueueItem = optimistic ? { ...known, approved: true } : known;
            const waiting = !shown.approved;
            return (
              <li
                key={item.id}
                data-testid={`queue-${item.id}`}
                data-state={shown.sent ? "sent" : shown.approved ? "approved" : "waiting"}
                className={`relative grid grid-cols-[auto_minmax(0,1fr)_auto_auto] items-center gap-x-2 gap-y-0.5 border-t border-line py-1 pr-2 pl-2.5 first:border-t-0 ${
                  waiting ? "bg-panel-2 text-ink" : "text-muted"
                }`}
              >
                <span
                  aria-hidden="true"
                  className={`absolute inset-y-0 left-0 w-[3px] ${waiting ? "bg-accent" : ""}`}
                />
                <span
                  className={`inline-flex h-4.5 w-[5.75rem] items-center justify-center border border-line-strong text-[0.625rem] ${
                    item.tier === "urgent" ? "font-semibold" : ""
                  }`}
                >
                  {TIER_WORD[item.tier]}
                </span>
                <span
                  data-testid={`queue-state-${item.id}`}
                  className={`inline-flex min-w-0 items-center gap-1.5 truncate text-[0.6875rem] leading-4 ${
                    waiting ? "font-semibold" : ""
                  }`}
                >
                  <StateGlyph item={shown} />
                  {stateWord(shown)}
                </span>
                <span className="readout text-right text-[0.6875rem]">{item.bytes} bytes</span>
                <button
                  type="button"
                  disabled={shown.approved}
                  onClick={() => void approve(item)}
                  className="row-span-2 h-7 min-w-[4.75rem] self-start px-2.5 text-xs font-semibold enabled:bg-accent enabled:text-accent-ink disabled:border disabled:border-line disabled:text-muted"
                >
                  {shown.approved ? "Approved" : "Approve"}
                </button>
                <div className="col-span-3 min-w-0">
                  <p className="text-xs leading-4">{item.summary}</p>
                  <details>
                    <summary className="cursor-pointer text-[0.6875rem] leading-4 text-muted">
                      What would be sent
                    </summary>
                    <pre className="readout mt-1 overflow-x-auto border border-line bg-surface px-2 py-1.5 text-[0.6875rem] leading-4 text-ink">
                      {JSON.stringify(item.payload, null, 2)}
                    </pre>
                  </details>
                </div>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}

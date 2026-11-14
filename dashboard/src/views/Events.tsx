import { useState } from "react";
import { api } from "../api/client";
import type { Events as EventsData } from "../api/types.gen";
import { usePolling } from "../hooks/usePolling";
import { clockTimeSeconds } from "../lib/format";
import { DOMAIN_NAME } from "../lib/levels";

type Event = EventsData["events"][number];
type Kind = Event["kind"];

export const KIND_LABEL: Record<Kind, string> = {
  spike: "Spike",
  drift: "Drift",
  correlation: "Link",
  sim_event: "Simulated event",
  checkin: "Check-in",
  node: "Node",
};

const FILTERS: { key: "all" | Kind; label: string }[] = [
  { key: "all", label: "All" },
  { key: "spike", label: "Spikes" },
  { key: "drift", label: "Drifts" },
  { key: "correlation", label: "Links" },
  { key: "sim_event", label: "Simulated events" },
  { key: "checkin", label: "Check-ins" },
  { key: "node", label: "Nodes" },
];

/** Each kind gets its own small shape and word; kinds are not severities, so no level colors. */
export function KindGlyph({ kind }: { kind: Kind }) {
  const common = {
    width: 16,
    height: 12,
    viewBox: "0 0 16 12",
    "aria-hidden": true,
    fill: "none",
  } as const;
  switch (kind) {
    case "spike":
      return (
        <svg {...common}>
          <path d="M0 10 L5 10 L8 1 L11 10 L16 10" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      );
    case "drift":
      return (
        <svg {...common}>
          <path d="M0 10 C5 10 9 8 16 2" stroke="currentColor" strokeWidth="1.6" />
        </svg>
      );
    case "correlation":
      return (
        <svg {...common}>
          <circle cx="4" cy="6" r="3" stroke="currentColor" strokeWidth="1.5" />
          <circle cx="12" cy="6" r="3" stroke="currentColor" strokeWidth="1.5" />
          <path d="M7 6 L9 6" stroke="currentColor" strokeWidth="1.5" />
        </svg>
      );
    case "sim_event":
      return (
        <svg {...common}>
          <rect
            x="2"
            y="1"
            width="12"
            height="10"
            stroke="currentColor"
            strokeWidth="1.5"
            strokeDasharray="2 2"
          />
        </svg>
      );
    case "checkin":
      return (
        <svg {...common}>
          <path
            d="M2 1 L14 1 L14 8 L7 8 L4 11 L4 8 L2 8 Z"
            stroke="currentColor"
            strokeWidth="1.4"
          />
        </svg>
      );
    case "node":
      return (
        <svg {...common}>
          <rect x="1" y="2" width="14" height="8" stroke="currentColor" strokeWidth="1.5" />
          <circle cx="5" cy="6" r="1.5" fill="currentColor" />
        </svg>
      );
  }
}

/** The event log, newest first. Check-ins are context: they never change a level. */
export default function Events() {
  const { data, error } = usePolling((s) => api.events({ limit: 500 }, s), 10_000);
  const [filter, setFilter] = useState<"all" | Kind>("all");

  const events = [...(data?.events ?? [])]
    .filter((e) => filter === "all" || e.kind === filter)
    .sort((a, b) => b.t - a.t);

  return (
    <div className="flex flex-col gap-2">
      <div className="flex flex-wrap items-baseline gap-x-3">
        <h1 className="text-base font-semibold">Events</h1>
        <p className="text-xs text-muted">
          Newest first. Check-ins are context only and never change a level.
        </p>
      </div>
      <div role="group" aria-label="Show" className="flex flex-wrap gap-1">
        {FILTERS.map((f) => (
          <button
            key={f.key}
            type="button"
            aria-pressed={filter === f.key}
            onClick={() => setFilter(f.key)}
            className={`inline-flex h-6 items-center gap-1 border px-2 text-[0.6875rem] ${
              filter === f.key
                ? "border-ink bg-panel-2 font-semibold text-ink"
                : "border-line-strong text-ink hover:bg-panel-2"
            }`}
          >
            {filter === f.key ? (
              <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                <path d="M1 5.2 L4 8 L9 2" fill="none" stroke="var(--ink)" strokeWidth="1.8" />
              </svg>
            ) : null}
            {f.label}
          </button>
        ))}
      </div>

      <section aria-label="Event log" className="pane pane-flush">
        {!data ? (
          <p className="p-2 text-muted">
            {error ? "Could not load the events." : "Loading the events."}
          </p>
        ) : events.length === 0 ? (
          <p className="p-2 text-muted">No events to show.</p>
        ) : (
          <ol className="divide-y divide-line text-xs">
            {events.map((e) => (
              <li
                key={e.id}
                data-testid={`event-${e.id}`}
                data-kind={e.kind}
                className={`flex min-h-6 flex-wrap items-center gap-x-3 gap-y-0.5 px-2.5 py-0.5 hover:bg-panel-2 ${
                  e.kind === "checkin" ? "text-muted" : ""
                }`}
              >
                <time
                  className="readout w-16 shrink-0 text-[0.6875rem]"
                  dateTime={new Date(e.t * 1000).toISOString()}
                >
                  {clockTimeSeconds(e.t)}
                </time>
                <span className="inline-flex w-36 shrink-0 items-center gap-1.5 font-semibold">
                  <KindGlyph kind={e.kind} />
                  {KIND_LABEL[e.kind]}
                  {e.kind === "checkin" ? <span className="font-normal"> (context)</span> : null}
                </span>
                <span className="min-w-0 flex-1">{e.sentence}</span>
                <span className="readout flex gap-2 text-[0.6875rem] text-muted">
                  {e.crew ? <span>{e.crew}</span> : null}
                  {e.domain ? <span>{DOMAIN_NAME[e.domain]}</span> : null}
                  {e.simulated ? (
                    <span
                      data-testid={`simulated-${e.id}`}
                      className="readout inline-flex h-4 items-center border border-dashed border-ink px-1 text-[0.625rem] text-ink"
                    >
                      Simulated
                    </span>
                  ) : null}
                </span>
              </li>
            ))}
          </ol>
        )}
      </section>
    </div>
  );
}

import { NavLink, Outlet } from "react-router";
import type { Status } from "../api/types.gen";
import { useStatusContext } from "../hooks/StatusContext";
import { useNow } from "../hooks/useNow";
import type { Polled } from "../hooks/usePolling";
import { clockTimeSeconds, formatDuration } from "../lib/format";
import { Banner } from "./Banner";
import { CheckinPrompt } from "./CheckinPrompt";

export const FOOTER_TEXT = "Decision-support prototype, not a medical device";

const NAV = [
  { to: "/", label: "Home", end: true },
  { to: "/history", label: "History" },
  { to: "/events", label: "Events" },
  { to: "/settings", label: "Settings" },
];

type Flow = "live" | "late" | "lost";

/**
 * The data-flow orb: green dot while polls are current, amber ring while late or a node is
 * offline, red cross once the link has failed. Driven only by what the client already knows.
 */
export function dataFlow(
  polled: Polled<Status>,
  now: number,
): { flow: Flow; word: string; age: string } {
  const age =
    polled.lastSuccessAt === null ? "" : formatDuration((now - polled.lastSuccessAt) / 1000);
  if (polled.data === null) {
    return polled.error
      ? { flow: "lost", word: "No link", age }
      : { flow: "late", word: "Waiting", age };
  }
  if (polled.stale)
    return { flow: polled.error ? "lost" : "late", word: polled.error ? "No link" : "Late", age };
  if (polled.data.nodes.some((n) => n.state === "offline"))
    return { flow: "late", word: "Partial", age };
  return { flow: "live", word: "Live", age };
}

function FlowGlyph({ flow }: { flow: Flow }) {
  if (flow === "live")
    return (
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <circle cx="6" cy="6" r="4" fill="var(--green-mark)" />
      </svg>
    );
  if (flow === "late")
    return (
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <circle cx="6" cy="6" r="4.2" fill="none" stroke="var(--yellow-mark)" strokeWidth="2" />
      </svg>
    );
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <path d="M2.5 2.5 L9.5 9.5 M9.5 2.5 L2.5 9.5" stroke="var(--red-mark)" strokeWidth="2.2" />
    </svg>
  );
}

/** The shell around every route: title, navigation, banner slot, content, and the footer. */
export function Layout() {
  const polled = useStatusContext();
  const now = useNow();
  const { flow, word, age } = dataFlow(polled, now);
  return (
    <div className="flex min-h-screen flex-col">
      <header className="topbar sticky top-0 z-30">
        <div className="mx-auto flex min-h-9 max-w-[1920px] flex-wrap items-center gap-x-5 gap-y-1 px-3">
          <p className="flex items-center gap-2 text-xs font-semibold tracking-wide whitespace-nowrap uppercase">
            <svg width="14" height="14" viewBox="0 0 18 18" aria-hidden="true">
              <rect
                x="1"
                y="1"
                width="16"
                height="16"
                fill="none"
                stroke="var(--ink)"
                strokeWidth="1.5"
              />
              <circle cx="9" cy="9" r="3" fill="var(--ink)" />
            </svg>
            Crew Context Monitor
          </p>
          <nav aria-label="Main" className="flex flex-1 flex-wrap gap-0.5">
            {NAV.map((item) => (
              <NavLink
                key={item.to}
                to={item.to}
                end={item.end}
                className={({ isActive }) =>
                  `inline-flex h-9 items-center px-2.5 text-xs hover:bg-panel-2 hover:text-ink ${
                    isActive
                      ? "font-semibold text-ink shadow-[inset_0_-2px_0_var(--ink)]"
                      : "text-muted"
                  }`
                }
              >
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="flex items-center gap-2.5">
            <p
              data-flow={flow}
              className="inline-flex h-6 items-center gap-1.5 border border-line bg-panel px-2 text-[0.6875rem] whitespace-nowrap"
            >
              <FlowGlyph flow={flow} />
              <span className="sr-only">Data link: </span>
              <span className="font-semibold">{word}</span>
              {age ? <span className="readout text-muted">{age}</span> : null}
            </p>
            <p className="readout text-[0.6875rem] whitespace-nowrap text-muted">
              <span className="text-ink">{clockTimeSeconds(now / 1000)}</span> UTC
            </p>
          </div>
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-[1920px] flex-1 flex-col px-3 pt-2">
        <Banner polled={polled} />
        <main className="flex-1 py-2">
          <Outlet />
        </main>
        <footer className="flex flex-wrap justify-between gap-2 border-t border-line py-2 text-[0.6875rem] text-muted">
          <span>{FOOTER_TEXT}</span>
        </footer>
      </div>
      <CheckinPrompt />
    </div>
  );
}

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

/** The brand mark: a planet on a tilted orbit with one small moon, drawn inline. */
function BrandMark() {
  return (
    <svg width="28" height="28" viewBox="0 0 28 28" aria-hidden="true">
      <defs>
        <linearGradient id="brand-planet" x1="0" y1="0" x2="1" y2="1">
          <stop offset="0" stopColor="#9bd4ff" />
          <stop offset="1" stopColor="#6d5cf0" />
        </linearGradient>
        <linearGradient id="brand-orbit" x1="0" y1="0" x2="1" y2="0">
          <stop offset="0" stopColor="#7cc4ff" />
          <stop offset="1" stopColor="#f29ad8" />
        </linearGradient>
      </defs>
      <circle cx="14" cy="14" r="6.5" fill="url(#brand-planet)" />
      <ellipse
        cx="14"
        cy="14"
        rx="12.5"
        ry="4.6"
        transform="rotate(-24 14 14)"
        fill="none"
        stroke="url(#brand-orbit)"
        strokeWidth="1.4"
      />
      <circle cx="24.6" cy="8.6" r="1.7" fill="#ffffff" />
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
        <div className="mx-auto flex min-h-12 max-w-[1920px] flex-wrap items-center gap-x-5 gap-y-1 px-4 py-1">
          <p className="flex items-center gap-2.5 whitespace-nowrap">
            <BrandMark />
            <span className="flex flex-col">
              <span className="brand-text text-[0.8125rem] leading-4 font-semibold tracking-[0.08em] uppercase">
                Crew Context Monitor
              </span>
              <span className="micro-label text-[0.5625rem]">Flight deck</span>
            </span>
          </p>
          <nav
            aria-label="Main"
            className="order-last flex w-full flex-wrap gap-1 sm:order-none sm:w-auto sm:flex-1"
          >
            {NAV.map((item) => (
              <NavLink key={item.to} to={item.to} end={item.end} className="nav-link">
                {item.label}
              </NavLink>
            ))}
          </nav>
          <div className="ml-auto flex items-center gap-3">
            <p
              data-flow={flow}
              className="inline-flex h-7 items-center gap-1.5 border border-line-strong bg-panel px-2.5 text-[0.6875rem] whitespace-nowrap"
            >
              <FlowGlyph flow={flow} />
              <span className="sr-only">Data link: </span>
              <span className="font-semibold">{word}</span>
              {age ? <span className="readout text-muted">{age}</span> : null}
            </p>
            <p className="readout flex flex-col items-end text-[0.6875rem] leading-4 whitespace-nowrap text-muted">
              <span className="text-sm leading-4 font-semibold text-ink">
                {clockTimeSeconds(now / 1000)}
              </span>
              <span className="micro-label text-[0.5625rem]">UTC</span>
            </p>
          </div>
        </div>
      </header>
      <div className="mx-auto flex w-full max-w-[1920px] flex-1 flex-col px-4 pt-2">
        <Banner polled={polled} />
        <main className="flex-1 py-2">
          <Outlet />
        </main>
        <footer className="flex flex-wrap justify-between gap-2 border-t border-line py-3 text-[0.6875rem] text-muted">
          <span>{FOOTER_TEXT}</span>
        </footer>
      </div>
      <CheckinPrompt />
    </div>
  );
}

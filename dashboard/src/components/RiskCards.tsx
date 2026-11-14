import { Link } from "react-router";
import type { Card, Level } from "../api/types.gen";
import { useNow } from "../hooks/useNow";
import { useStatusContext } from "../hooks/StatusContext";
import { formatDuration, formatValue } from "../lib/format";
import { DOMAIN_NAME } from "../lib/levels";
import { LevelBadge } from "./LevelBadge";

export type CardNumber = Card["numbers"][number];

/** Relative change from baseline, or null when either side is missing. */
export function deviation(n: CardNumber): number | null {
  if (n.value === null || n.baseline === null) return null;
  if (n.baseline === 0) return n.value === 0 ? 0 : Math.sign(n.value);
  return (n.value - n.baseline) / Math.abs(n.baseline);
}

/** Marker position on the tick bar in percent: baseline at 50, clamped to stay on the track. */
export function tickPosition(n: CardNumber): number {
  const d = deviation(n);
  return d === null ? 50 : Math.max(4, Math.min(96, 50 + d * 80));
}

const MARK: Record<Level, string> = {
  green: "var(--green-mark)",
  yellow: "var(--yellow-mark)",
  red: "var(--red-mark)",
};

const RAIL: Record<Level, string> = {
  green: "w-0.5 bg-level-green-mark",
  yellow: "w-1 bg-level-yellow-mark",
  red: "w-1 bg-level-red-mark",
};

// Label, value, then value against baseline: the same columns on every row and every card.
export const COLS = "grid grid-cols-[minmax(0,1.1fr)_6.75rem_minmax(0,1fr)] items-center gap-x-2";

/** One telemetry row: label, value, unit, an out-of-range arrow, and value against baseline. */
export function NumberRow({
  n,
  flagged,
  level,
}: {
  n: CardNumber;
  flagged: boolean;
  level: Level;
}) {
  const d = deviation(n);
  const arrow = flagged && d !== null && d !== 0 ? (d > 0 ? "up" : "down") : null;
  return (
    <div className={`${COLS} h-[1.125rem]`}>
      <dt title={n.label} className="truncate text-[0.6875rem] text-muted">
        {n.label}
      </dt>
      <dd className="readout flex items-baseline justify-end gap-1">
        <span className="text-xs font-semibold text-ink">{formatValue(n.value)}</span>
        <span title={n.unit} className="w-11 truncate text-[0.625rem] text-muted">
          {n.unit}
        </span>
        <span className="inline-block w-2" aria-hidden="true">
          {arrow ? (
            <svg width="8" height="8" viewBox="0 0 9 9">
              <path
                d={arrow === "up" ? "M4.5 0.5 L8.5 8.5 L0.5 8.5 Z" : "M0.5 0.5 L8.5 0.5 L4.5 8.5 Z"}
                fill={MARK[level]}
              />
            </svg>
          ) : null}
        </span>
        {arrow ? (
          <span className="sr-only">{arrow === "up" ? "above" : "below"} baseline</span>
        ) : null}
      </dd>
      <dd className="flex min-w-0 items-center gap-1.5">
        <span aria-hidden="true" className="relative h-2 min-w-6 flex-1">
          <span className="absolute inset-x-0 top-[3px] h-0.5 bg-line" />
          {n.baseline !== null ? (
            <>
              <span className="absolute top-px left-[40%] h-1.5 w-[20%] bg-line-strong opacity-55" />
              <span className="absolute -top-px left-1/2 h-2.5 w-px bg-muted" />
            </>
          ) : null}
          {n.value !== null ? (
            <span
              className="absolute -top-0.5 -ml-px h-3 w-[3px]"
              style={{
                left: `${tickPosition(n)}%`,
                background: flagged ? MARK[level] : "var(--ink)",
              }}
            />
          ) : null}
        </span>
        <span className="readout w-10 shrink-0 text-right text-[0.625rem] whitespace-nowrap text-muted">
          <span className="sr-only">baseline </span>
          {formatValue(n.baseline)}
        </span>
      </dd>
    </div>
  );
}

/** One risk card. The whole card is the link to the domain's detail view. */
export function RiskCard({ card, staleAge = null }: { card: Card; staleAge?: string | null }) {
  const numbers = card.numbers.slice(0, 3);
  // Flag the row furthest from its own baseline, and only when the card is not Green.
  let flagged = -1;
  if (card.level !== "green") {
    let worst = 0;
    numbers.forEach((n, i) => {
      const d = Math.abs(deviation(n) ?? 0);
      if (d > worst) [worst, flagged] = [d, i];
    });
  }
  return (
    <Link
      to={`/domain/${card.domain}`}
      data-testid={`card-${card.domain}`}
      data-level={card.level}
      className={`relative flex h-44 flex-col overflow-hidden pane pane-flush pt-1.5 pr-2.5 pb-2 pl-3 hover:border-line-strong hover:bg-panel-2 ${
        card.level === "red" ? "pulse-once" : ""
      }`}
    >
      <span aria-hidden="true" className={`absolute inset-y-0 left-0 ${RAIL[card.level]}`} />
      <div className="flex h-5 items-center justify-between gap-2">
        <h2 className="truncate text-xs font-semibold tracking-wide uppercase">
          {DOMAIN_NAME[card.domain]}
        </h2>
        <LevelBadge level={card.level} />
      </div>
      <div className={staleAge ? "opacity-55" : ""}>
        <div aria-hidden="true" className={`${COLS} mt-1 h-3.5 border-b border-line`}>
          <span className="micro-label text-[0.5625rem]">Reading</span>
          <span className="micro-label text-right text-[0.5625rem]">Value</span>
          <span className="micro-label text-right text-[0.5625rem]">vs baseline</span>
        </div>
        <dl className="mt-0.5 flex flex-col">
          {numbers.map((n, i) => (
            <NumberRow key={n.label} n={n} flagged={i === flagged} level={card.level} />
          ))}
        </dl>
        <p className="mt-1.5 line-clamp-3 text-[0.6875rem] leading-[0.9375rem]">{card.reason}</p>
      </div>
      {card.action ? (
        <p className="mt-auto line-clamp-2 border-t border-line pt-1 text-[0.6875rem] leading-[0.9375rem] text-muted">
          <span className="font-semibold text-ink">Next step:</span> {card.action}
        </p>
      ) : null}
      {staleAge ? (
        <>
          <span aria-hidden="true" className="stale-hatch pointer-events-none absolute inset-0" />
          <span className="readout absolute top-7 right-2.5 inline-flex h-4.5 items-center border border-level-yellow-mark bg-surface px-1 text-[0.625rem]">
            {staleAge} ago
          </span>
        </>
      ) : null}
    </Link>
  );
}

/** The four cards in their fixed order: Cardiovascular, Air quality, Thermal, Radiation (proxy). */
export function RiskCards() {
  const { data: status, stale, lastSuccessAt } = useStatusContext();
  const now = useNow();
  const staleAge =
    stale && lastSuccessAt !== null ? formatDuration((now - lastSuccessAt) / 1000) : null;
  return (
    <section aria-label="Risk cards">
      <ul className="grid grid-cols-1 gap-2 sm:grid-cols-2 xl:grid-cols-4">
        {status?.cards.map((card) => (
          <li key={card.domain} className="flex flex-col">
            <RiskCard card={card} staleAge={staleAge} />
          </li>
        ))}
      </ul>
    </section>
  );
}

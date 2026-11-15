import { useMemo } from "react";
import type { History } from "../api/types.gen";
import { formatValue } from "../lib/format";
import { nearestIndex } from "../lib/series";

interface Props {
  history: History;
  name: string;
  /** Class on the measured line, so tests and e2e can find each series. */
  seriesClass: string;
  tStart: number;
  tEnd: number;
  /** The time under the cursor, or null to mark the latest reading. */
  cursorT: number | null;
}

const W = 1000;
const H = 100;

function pathOf(coords: [number, number][]): string {
  return coords
    .map(([x, y], i) => `${i === 0 ? "M" : "L"}${x.toFixed(1)} ${y.toFixed(2)}`)
    .join(" ");
}

/**
 * One measure on its own scale: the measured line over the range seen inside each step, the
 * baseline range and the limit lines when the API sends them. Stretches past a limit are redrawn
 * in that limit's color. Nothing here decides what is abnormal; the band and lines are the API's.
 */
export function StripChart({ history, name, seriesClass, tStart, tEnd, cursorT }: Props) {
  const { points, baseline, limits, unit } = history;

  const geometry = useMemo(() => {
    if (points.length === 0) return null;
    const values = points.flatMap((p) => [p.min, p.max, p.mean]);
    for (const b of baseline ?? []) values.push(b.lo, b.hi);
    // A limit joins the scale only when the readings come near it; a far-off line would flatten
    // the series into a sliver. The limit still shows in the readout when it is reached.
    const dataLo = Math.min(...values);
    const dataHi = Math.max(...values);
    const reach = Math.max(dataHi - dataLo, Math.abs(dataHi) * 0.05, 0.5);
    const near = (v: number | null | undefined): v is number =>
      v != null && v <= dataHi + reach && v >= dataLo - reach;
    const shown = { yellow: near(limits?.yellow), red: near(limits?.red) };
    if (shown.yellow) values.push(limits!.yellow!);
    if (shown.red) values.push(limits!.red!);
    let lo = Math.min(...values);
    let hi = Math.max(...values);
    const pad = (hi - lo) * 0.1 || 1;
    lo -= pad;
    hi += pad;
    const span = tEnd - tStart || 1;
    const x = (t: number) => ((t - tStart) / span) * W;
    const y = (v: number) => ((hi - v) / (hi - lo)) * H;

    const line = pathOf(points.map((p) => [x(p.t), y(p.mean)]));
    const range =
      pathOf(points.map((p) => [x(p.t), y(p.max)])) +
      " " +
      pathOf([...points].reverse().map((p) => [x(p.t), y(p.min)])).replace("M", "L") +
      " Z";
    const band =
      baseline && baseline.length > 1
        ? pathOf(baseline.map((b) => [x(b.t), y(b.hi)])) +
          " " +
          pathOf([...baseline].reverse().map((b) => [x(b.t), y(b.lo)])).replace("M", "L") +
          " Z"
        : null;
    const baseLine =
      baseline && baseline.length > 1 ? pathOf(baseline.map((b) => [x(b.t), y(b.mean)])) : null;

    // Stretches at or past a limit, each starting from the point before it so the line is whole.
    const past = (from: number | null | undefined, below: number | null | undefined) => {
      if (from == null) return null;
      let d = "";
      let open = false;
      points.forEach((p, i) => {
        const inside = p.mean >= from && (below == null || p.mean < below);
        if (!inside) {
          open = false;
          return;
        }
        if (!open) {
          const prev = points[i - 1] ?? p;
          d += `M${x(prev.t).toFixed(1)} ${y(prev.mean).toFixed(2)} `;
          open = true;
        }
        d += `L${x(p.t).toFixed(1)} ${y(p.mean).toFixed(2)} `;
      });
      return d || null;
    };
    const split = limits?.red != null && limits.red !== limits?.yellow;
    return {
      lo,
      hi,
      shown,
      x,
      y,
      line,
      range,
      band,
      baseLine,
      yellow: past(limits?.yellow, split ? limits?.red : null),
      red: split ? past(limits?.red, null) : null,
    };
  }, [points, baseline, limits, tStart, tEnd]);

  if (!geometry) {
    return (
      <div className="flex h-18 items-center border border-line bg-panel px-2 text-xs text-muted">
        {name}: no readings yet.
      </div>
    );
  }

  const at = cursorT === null ? points.length - 1 : nearestIndex(points, cursorT);
  const point = points[at];
  const limitLines = [
    limits?.yellow != null && geometry.shown.yellow
      ? {
          key: "yellow",
          v: limits.yellow,
          color: "var(--yellow-mark)",
          label: limits.red === limits.yellow ? "Threshold" : "Yellow line",
        }
      : null,
    limits?.red != null && limits.red !== limits?.yellow && geometry.shown.red
      ? { key: "red", v: limits.red, color: "var(--red-mark)", label: "Red line" }
      : null,
  ].filter((l) => l !== null);

  return (
    <div className="relative h-18 overflow-hidden border border-line bg-panel">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        preserveAspectRatio="none"
        aria-hidden="true"
        className="absolute inset-0 h-full w-full"
      >
        {geometry.band ? (
          <path
            d={geometry.band}
            className="baseline-band"
            fill="var(--muted)"
            fillOpacity={0.13}
          />
        ) : null}
        <path d={geometry.range} className="series-range" fill="var(--accent)" fillOpacity={0.16} />
        {geometry.baseLine ? (
          <path
            d={geometry.baseLine}
            className="series-baseline"
            fill="none"
            stroke="var(--line-strong)"
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
        {limitLines.map((l) => (
          <line
            key={l.key}
            x1={0}
            x2={W}
            y1={geometry.y(l.v)}
            y2={geometry.y(l.v)}
            className={`limit-line ${l.key}`}
            stroke={l.color}
            strokeOpacity={0.8}
            strokeWidth={1}
            vectorEffect="non-scaling-stroke"
          />
        ))}
        <path
          d={geometry.line}
          className={seriesClass}
          fill="none"
          stroke="var(--series-prv)"
          strokeWidth={1.6}
          vectorEffect="non-scaling-stroke"
        />
        {geometry.yellow ? (
          <path
            d={geometry.yellow}
            className="past-yellow"
            fill="none"
            stroke="var(--yellow-mark)"
            strokeWidth={2.4}
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
        {geometry.red ? (
          <path
            d={geometry.red}
            className="past-red"
            fill="none"
            stroke="var(--red-mark)"
            strokeWidth={2.4}
            vectorEffect="non-scaling-stroke"
          />
        ) : null}
      </svg>
      <p className="absolute top-0.5 left-1.5 text-[0.625rem] leading-3.5 font-semibold">
        {name} <span className="font-normal text-muted">· {unit}</span>
      </p>
      {limitLines.map((l) => (
        <span
          key={l.key}
          className={`readout absolute right-9 text-[0.5625rem] leading-3.5 ${
            l.key === "red" ? "-mt-3.5" : ""
          }`}
          style={{ top: `${geometry.y(l.v)}%` }}
        >
          {l.label} {formatValue(l.v)}
        </span>
      ))}
      <span className="readout absolute top-0.5 right-1 text-[0.5625rem] leading-3.5 text-muted">
        {formatValue(Math.round(geometry.hi * 10) / 10)}
      </span>
      <span className="readout absolute right-1 bottom-0.5 text-[0.5625rem] leading-3.5 text-muted">
        {formatValue(Math.round(geometry.lo * 10) / 10)}
      </span>
      <span
        aria-hidden="true"
        className="pointer-events-none absolute -mt-1 -ml-1 h-2 w-2 border-2 border-panel bg-ink"
        style={{
          left: `${(geometry.x(point.t) / W) * 100}%`,
          top: `${geometry.y(point.mean)}%`,
        }}
      />
    </div>
  );
}

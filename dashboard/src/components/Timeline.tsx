import { useCallback, useRef, useState, type PointerEvent } from "react";
import { api } from "../api/client";
import type { Events, History } from "../api/types.gen";
import { useSelectedCrew } from "../hooks/CrewSelection";
import { useStatusContext } from "../hooks/StatusContext";
import { usePolling } from "../hooks/usePolling";
import { clockTime, formatDuration, formatValue } from "../lib/format";
import { nearestIndex, signed, windowStats } from "../lib/series";
import { StripChart } from "./StripChart";

const WINDOW_HOURS = 6;
const REFRESH_MS = 15_000;
const STEP_S = 60;

export type Series = "prv" | "hr_resid";

const SERIES_LABEL: Record<Series, { name: string; unit: string }> = {
  prv: { name: "Pulse-rate variability", unit: "ms" },
  hr_resid: { name: "HR residual", unit: "bpm" },
};

interface TimelineData {
  co2: History;
  crewSeries: History;
  temp: History;
  cpm: History;
  events: Events;
}

interface Strip {
  key: string;
  name: string;
  seriesClass: string;
  history: History;
}

function LegendSwatch({ className }: { className: string }) {
  return <span aria-hidden="true" className={`inline-block w-4.5 ${className}`} />;
}

/** The numbers for one strip: the reading under the cursor, its baseline, and the window. */
function StripReadout({
  strip,
  cursorT,
  range,
}: {
  strip: Strip;
  cursorT: number | null;
  range: [number, number] | null;
}) {
  const { points, baseline, limits, unit } = strip.history;
  if (points.length === 0) {
    return (
      <div className="flex h-18 items-center border border-line bg-panel px-2 text-muted">—</div>
    );
  }
  const point = points[cursorT === null ? points.length - 1 : nearestIndex(points, cursorT)];
  const base = baseline && baseline.length > 0 ? baseline[nearestIndex(baseline, point.t)] : null;
  const stats = range ? windowStats(points, range[0], range[1]) : null;
  const past =
    limits?.red != null && point.mean >= limits.red
      ? { word: "At the red line", mark: "var(--red-mark)" }
      : limits?.yellow != null && point.mean >= limits.yellow
        ? { word: "At the yellow line", mark: "var(--yellow-mark)" }
        : null;
  return (
    <div
      data-testid={`readout-${strip.key}`}
      className="readout flex h-18 flex-col justify-center gap-0.5 overflow-hidden border border-line bg-panel px-2 text-[0.625rem] leading-3.5 whitespace-nowrap text-muted"
    >
      <p className="flex items-baseline gap-1">
        <span className="text-base leading-5 font-semibold text-ink">
          {formatValue(point.mean)}
        </span>
        <span className="truncate">{unit}</span>
      </p>
      {past ? (
        <p className="flex items-center gap-1 text-ink">
          <svg width="7" height="7" viewBox="0 0 12 12" aria-hidden="true">
            <path d="M6 0.8 L11.6 11 L0.4 11 Z" fill={past.mark} />
          </svg>
          {past.word}
        </p>
      ) : (
        <p className="truncate">
          {base ? (
            <>
              baseline {formatValue(base.mean)}{" "}
              <span className="text-ink">{signed(point.mean - base.mean)}</span>
            </>
          ) : (
            <>
              range {formatValue(point.min)} to {formatValue(point.max)}
            </>
          )}
        </p>
      )}
      {stats ? (
        <p className="truncate">
          window mean <span className="text-ink">{formatValue(stats.mean)}</span> · change{" "}
          <span className="text-ink">{signed(stats.change)}</span>
        </p>
      ) : null}
      {stats ? (
        <p className="truncate">
          low {formatValue(stats.low)} · high {formatValue(stats.high)}
        </p>
      ) : null}
    </div>
  );
}

/**
 * The home timeline: one strip per measure on a shared time axis, never two scales on one plot.
 * Moving over any strip moves one cursor through all of them; pressing and dragging marks a
 * window, and each strip then reports what happened inside it. Every number shown under the
 * cursor is also in the column on the right, so nothing is readable only by hovering.
 */
export function Timeline() {
  const { data: status } = useStatusContext();
  const [crew] = useSelectedCrew(status?.crew.map((c) => c.id) ?? []);
  const [series, setSeries] = useState<Series>("prv");
  const [cursorT, setCursorT] = useState<number | null>(null);
  const [range, setRange] = useState<[number, number] | null>(null);
  const dragFrom = useRef<number | null>(null);

  const fetcher = useCallback(
    async (signal: AbortSignal): Promise<TimelineData> => {
      const h = { hours: WINDOW_HOURS, step: STEP_S };
      const [co2, crewSeries, temp, cpm, events] = await Promise.all([
        api.history({ metric: "co2_mmhg", ...h }, signal),
        api.history({ metric: series, crew, ...h }, signal),
        api.history({ metric: "temp_c", ...h }, signal),
        api.history({ metric: "cpm", ...h }, signal),
        api.events({ limit: 200 }, signal),
      ]);
      return { co2, crewSeries, temp, cpm, events };
    },
    [crew, series],
  );
  const { data, error } = usePolling(fetcher, REFRESH_MS, 45_000, `${crew}/${series}`);

  const label = SERIES_LABEL[series];
  const tEnd = status?.t ?? data?.co2.points.at(-1)?.t ?? 0;
  const tStart = tEnd - WINDOW_HOURS * 3600;
  const left = (t: number) => `${((t - tStart) / (tEnd - tStart || 1)) * 100}%`;
  const events = (data?.events.events ?? []).filter((e) => e.t >= tStart && e.t <= tEnd);
  const correlation = status?.correlation ?? null;
  const corrEvent = events.find((e) => e.kind === "correlation");

  const strips: Strip[] = data
    ? [
        { key: "co2", name: "Cabin CO2", seriesClass: "series-co2", history: data.co2 },
        {
          key: "crew",
          name: crew ? `${label.name} · ${crew}` : label.name,
          seriesClass: "series-crew",
          history: data.crewSeries,
        },
        { key: "temp", name: "Cabin temperature", seriesClass: "series-temp", history: data.temp },
        { key: "cpm", name: "Radiation proxy", seriesClass: "series-cpm", history: data.cpm },
      ]
    : [];

  function timeAt(e: PointerEvent<HTMLDivElement>): number | null {
    const box = e.currentTarget.getBoundingClientRect();
    if (box.width === 0) return null;
    const f = Math.max(0, Math.min(1, (e.clientX - box.left) / box.width));
    return tStart + f * (tEnd - tStart);
  }
  function onPointerDown(e: PointerEvent<HTMLDivElement>) {
    const t = timeAt(e);
    if (t === null) return;
    dragFrom.current = t;
    setCursorT(t);
    setRange(null); // a press without a drag clears the window
    try {
      // Keep the drag alive when the pointer leaves the plot; not every pointer can be captured.
      e.currentTarget.setPointerCapture?.(e.pointerId);
    } catch {
      /* the drag still works inside the plot */
    }
  }
  function onPointerMove(e: PointerEvent<HTMLDivElement>) {
    const t = timeAt(e);
    if (t === null) return;
    setCursorT(t);
    const from = dragFrom.current;
    if (from !== null && Math.abs(t - from) >= STEP_S) {
      setRange([Math.min(from, t), Math.max(from, t)]);
    }
  }
  function onPointerUp() {
    dragFrom.current = null;
  }
  function onPointerLeave() {
    if (dragFrom.current === null) setCursorT(null);
  }

  const steps = data?.co2.points ?? [];
  const cursorIndex =
    steps.length === 0 ? 0 : cursorT === null ? steps.length - 1 : nearestIndex(steps, cursorT);
  const shownT = cursorT ?? steps.at(-1)?.t ?? tEnd;
  const ticks: number[] = [];
  for (let t = Math.ceil(tStart / 3600) * 3600; t <= tEnd; t += 3600) {
    const f = (t - tStart) / (tEnd - tStart || 1);
    if (f > 0.04 && f < 0.96) ticks.push(t);
  }

  return (
    <section aria-label="Timeline" className="pane pane-flush flex flex-col">
      <div className="panel-head flex-wrap">
        <h2 className="min-w-0 truncate text-xs font-semibold">
          <span className="micro-label mr-2">Last {WINDOW_HOURS} h · UTC</span>
          Cabin CO2 and {label.name.toLowerCase()}
          {crew ? <span className="text-muted"> · {crew}</span> : null}
        </h2>
        <div
          role="group"
          aria-label="Series"
          className="flex overflow-hidden border border-line-strong"
        >
          {(Object.keys(SERIES_LABEL) as Series[]).map((key, i) => (
            <button
              key={key}
              type="button"
              aria-pressed={series === key}
              onClick={() => setSeries(key)}
              className={`h-5.5 px-2 text-[0.6875rem] font-semibold ${i > 0 ? "border-l border-line-strong" : ""} ${
                series === key ? "bg-ink text-surface" : "text-ink hover:bg-panel-2"
              }`}
            >
              {SERIES_LABEL[key].name}
            </button>
          ))}
        </div>
      </div>

      <div className="flex flex-1 flex-col px-2 pt-1 pb-1.5">
        <p data-testid="lag-label" className="h-4 truncate text-xs leading-4">
          {correlation ? correlation.sentence : ""}
        </p>

        <div className="mt-0.5 flex min-h-6 flex-wrap items-center justify-between gap-x-3 gap-y-1 text-[0.6875rem]">
          <p data-testid="cursor-time" className="readout">
            <span className="micro-label mr-1.5">{cursorT === null ? "Latest" : "Cursor"}</span>
            <span className="font-semibold">{clockTime(shownT)} UTC</span>
            {cursorT !== null ? (
              <span className="text-muted"> · {formatDuration(tEnd - shownT)} before now</span>
            ) : null}
          </p>
          {range ? (
            <p data-testid="window-summary" className="readout flex items-center gap-2">
              <span>
                <span className="micro-label mr-1.5">Window</span>
                <span className="font-semibold">
                  {clockTime(range[0])} to {clockTime(range[1])}
                </span>
                <span className="text-muted"> · {formatDuration(range[1] - range[0])}</span>
              </span>
              <button
                type="button"
                onClick={() => setRange(null)}
                className="h-5.5 border border-line-strong px-2 font-sans font-semibold hover:border-accent hover:bg-panel-2"
              >
                Clear window
              </button>
            </p>
          ) : (
            <p className="text-muted">Move across a strip to read it. Press and drag to measure.</p>
          )}
        </div>

        <div
          role="group"
          aria-label={`Last ${WINDOW_HOURS} hours: cabin CO2 in mmHg, ${label.name.toLowerCase()} in ${label.unit}, cabin temperature and the radiation proxy, with event markers`}
          data-testid="timeline-chart"
          className="mt-1"
        >
          {data ? (
            <div className="grid grid-cols-[minmax(0,1fr)_7.25rem] gap-x-1.5 sm:grid-cols-[minmax(0,1fr)_11.5rem]">
              <div
                data-testid="timeline-plot"
                onPointerDown={onPointerDown}
                onPointerMove={onPointerMove}
                onPointerUp={onPointerUp}
                onPointerCancel={onPointerUp}
                onPointerLeave={onPointerLeave}
                className="relative flex cursor-crosshair touch-none flex-col gap-1 pt-3.5 select-none"
              >
                {strips.map((s) => (
                  <StripChart
                    key={s.key}
                    history={s.history}
                    name={s.name}
                    seriesClass={s.seriesClass}
                    tStart={tStart}
                    tEnd={tEnd}
                    cursorT={cursorT}
                  />
                ))}
                <div aria-hidden="true" className="pointer-events-none absolute inset-0">
                  {correlation && corrEvent ? (
                    <span
                      className="lag-band absolute inset-y-0 bg-ink/8"
                      style={{
                        left: left(corrEvent.t - correlation.lag_min * 60),
                        width: `${((correlation.lag_min * 60) / (tEnd - tStart || 1)) * 100}%`,
                      }}
                    />
                  ) : null}
                  {events.map((e) => (
                    <span
                      key={e.id}
                      className={`event-marker ${e.simulated ? "simulated" : "observed"} absolute inset-y-0 border-l border-muted/70 ${
                        e.simulated ? "border-dashed" : ""
                      }`}
                      style={{ left: left(e.t) }}
                    >
                      <span className="absolute -top-0.5 -left-1 text-[0.5625rem] leading-3.5 text-ink">
                        {e.simulated ? "□" : "■"}
                      </span>
                    </span>
                  ))}
                  {range ? (
                    <span
                      className="timeline-window absolute top-3.5 bottom-0 border-x border-ink bg-accent/15"
                      style={{
                        left: left(range[0]),
                        width: `${((range[1] - range[0]) / (tEnd - tStart || 1)) * 100}%`,
                      }}
                    />
                  ) : null}
                  <span
                    className="timeline-cursor absolute top-3.5 bottom-0 w-px bg-ink"
                    style={{ left: left(shownT) }}
                  />
                </div>
              </div>
              <div className="flex flex-col gap-1 pt-3.5">
                {strips.map((s) => (
                  <StripReadout key={s.key} strip={s} cursorT={cursorT} range={range} />
                ))}
              </div>
              <div className="relative mt-0.5 h-3.5">
                {ticks.map((t) => (
                  <span
                    key={t}
                    className="readout absolute -ml-5 w-10 text-center text-[0.625rem] leading-3.5 text-muted"
                    style={{ left: left(t) }}
                  >
                    {clockTime(t)}
                  </span>
                ))}
              </div>
              <span />
              <input
                type="range"
                aria-label="Time cursor"
                min={0}
                max={Math.max(steps.length - 1, 0)}
                step={1}
                value={cursorIndex}
                onChange={(e) => setCursorT(steps[Number(e.target.value)]?.t ?? null)}
                className="mt-1 h-4 w-full accent-accent"
              />
              <span />
            </div>
          ) : (
            <p className="py-16 text-center text-muted">
              {error ? "Could not load the timeline." : "Loading the timeline."}
            </p>
          )}
        </div>
        <ul className="mt-1.5 flex flex-wrap gap-x-3.5 gap-y-0.5 text-[0.6875rem] text-muted">
          <li className="inline-flex items-center gap-1.5">
            <LegendSwatch className="h-0.5 bg-series-prv" />
            Measured
          </li>
          <li className="inline-flex items-center gap-1.5">
            <LegendSwatch className="h-2 bg-accent/25" />
            Lowest to highest in each minute
          </li>
          <li className="inline-flex items-center gap-1.5">
            <LegendSwatch className="h-2 bg-muted/25" />
            Baseline range
          </li>
          <li className="inline-flex items-center gap-1.5">
            <span aria-hidden="true">■</span> Solid lines mark events seen by the monitor;{" "}
            <span aria-hidden="true">□</span> dashed lines mark simulated events.
          </li>
        </ul>
      </div>
    </section>
  );
}

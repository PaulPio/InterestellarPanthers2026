import { useCallback, useState } from "react";
import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceArea,
  ReferenceLine,
  ResponsiveContainer,
  Tooltip,
  XAxis,
  YAxis,
} from "recharts";
import { api } from "../api/client";
import type { Events, History } from "../api/types.gen";
import { useSelectedCrew } from "../hooks/CrewSelection";
import { useStatusContext } from "../hooks/StatusContext";
import { usePolling } from "../hooks/usePolling";
import { clockTime, formatValue } from "../lib/format";

const WINDOW_HOURS = 6;
const REFRESH_MS = 15_000;

export type Series = "prv" | "hr_resid";

const SERIES_LABEL: Record<Series, { name: string; unit: string }> = {
  prv: { name: "Pulse-rate variability", unit: "ms" },
  hr_resid: { name: "HR residual", unit: "bpm" },
};

const TICK = { fontFamily: "var(--font-mono)", fontSize: 10 };

interface TimelineData {
  co2: History;
  crewSeries: History;
  events: Events;
}

type Point = { t: number; mean: number };

function nearest(points: Point[], t: number): Point | null {
  let best: Point | null = null;
  for (const p of points) if (!best || Math.abs(p.t - t) < Math.abs(best.t - t)) best = p;
  return best;
}

/** The crosshair readout: both series at the hovered time, in one box. */
function Readout({
  active,
  label,
  data,
  series,
}: {
  active?: boolean;
  label?: number | string;
  data: TimelineData;
  series: { name: string; unit: string };
}) {
  if (!active || typeof label !== "number") return null;
  const co2 = nearest(data.co2.points, label);
  const crew = nearest(data.crewSeries.points, label);
  return (
    <div className="readout flex flex-col gap-0.5 border border-line-strong bg-surface px-2 py-1.5 text-[0.6875rem] leading-4">
      <span className="text-muted">{clockTime(label)} UTC</span>
      <span>
        <span className="text-series-co2">━</span> CO2 {co2 ? formatValue(co2.mean) : "—"} mmHg
      </span>
      <span>
        <span className="text-series-prv">━</span> {series.name}{" "}
        {crew ? formatValue(crew.mean) : "—"} {series.unit}
      </span>
    </div>
  );
}

function LegendSwatch({ className }: { className: string }) {
  return <span aria-hidden="true" className={`inline-block w-4.5 ${className}`} />;
}

/** Cabin CO2 (left axis) over the selected crew member's pulse-rate variability (right axis). */
export function Timeline() {
  const { data: status } = useStatusContext();
  const [crew] = useSelectedCrew(status?.crew.map((c) => c.id) ?? []);
  const [series, setSeries] = useState<Series>("prv");

  const fetcher = useCallback(
    async (signal: AbortSignal): Promise<TimelineData> => {
      const [co2, crewSeries, events] = await Promise.all([
        api.history({ metric: "co2_mmhg", hours: WINDOW_HOURS, step: 60 }, signal),
        api.history({ metric: series, crew, hours: WINDOW_HOURS, step: 60 }, signal),
        api.events({ limit: 200 }, signal),
      ]);
      return { co2, crewSeries, events };
    },
    [crew, series],
  );
  const { data, error } = usePolling(fetcher, REFRESH_MS, 45_000, `${crew}/${series}`);

  const label = SERIES_LABEL[series];
  const tEnd = status?.t ?? data?.co2.points.at(-1)?.t ?? 0;
  const tStart = tEnd - WINDOW_HOURS * 3600;
  const events = (data?.events.events ?? []).filter((e) => e.t >= tStart && e.t <= tEnd);
  const correlation = status?.correlation ?? null;
  const corrEvent = events.find((e) => e.kind === "correlation");
  const crewBaseline = data?.crewSeries.baseline ?? null;

  return (
    <section aria-label="Timeline" className="pane pane-flush flex flex-col">
      <div className="panel-head flex-wrap">
        <h2 className="min-w-0 truncate text-xs font-semibold">
          <span className="micro-label mr-2">Last {WINDOW_HOURS} h · UTC</span>
          Cabin CO2 and {label.name.toLowerCase()}
          {crew ? <span className="text-muted"> · {crew}</span> : null}
        </h2>
        <div role="group" aria-label="Series" className="flex border border-line-strong">
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

        <div className="mt-0.5 flex justify-between gap-2 text-[0.625rem] font-semibold">
          <span className="text-series-co2">Cabin CO2 · mmHg</span>
          <span className="text-right text-series-prv">
            {label.name} · {label.unit}
          </span>
        </div>
        <div
          role="img"
          aria-label={`Last ${WINDOW_HOURS} hours: cabin CO2 in mmHg and ${label.name.toLowerCase()} in ${label.unit}, with event markers`}
          data-testid="timeline-chart"
          className="h-52 w-full"
        >
          {data ? (
            <ResponsiveContainer width="100%" height="100%">
              <ComposedChart margin={{ top: 14, right: 0, bottom: 0, left: 0 }}>
                <CartesianGrid stroke="var(--line)" />
                <XAxis
                  dataKey="t"
                  type="number"
                  domain={[tStart, tEnd]}
                  tickFormatter={clockTime}
                  stroke="var(--line-strong)"
                  tick={{ ...TICK, fill: "var(--muted)" }}
                  allowDuplicatedCategory={false}
                />
                <YAxis
                  yAxisId="co2"
                  orientation="left"
                  width={36}
                  stroke="var(--line-strong)"
                  tick={{ ...TICK, fill: "var(--series-co2)" }}
                />
                <YAxis
                  yAxisId="crew"
                  orientation="right"
                  width={36}
                  stroke="var(--line-strong)"
                  tick={{ ...TICK, fill: "var(--series-prv)" }}
                />
                {correlation && corrEvent ? (
                  <ReferenceArea
                    yAxisId="co2"
                    x1={corrEvent.t - correlation.lag_min * 60}
                    x2={corrEvent.t}
                    className="lag-band"
                    fill="var(--ink)"
                    fillOpacity={0.07}
                  />
                ) : null}
                {events.map((e) => (
                  <ReferenceLine
                    key={e.id}
                    yAxisId="co2"
                    x={e.t}
                    className={`event-marker ${e.simulated ? "simulated" : "observed"}`}
                    stroke="var(--muted)"
                    strokeDasharray={e.simulated ? "4 4" : undefined}
                    strokeWidth={1.5}
                    label={{
                      value: e.simulated ? "□" : "■",
                      position: "top",
                      fill: "var(--ink)",
                      fontSize: 10,
                    }}
                  />
                ))}
                {crewBaseline ? (
                  <Line
                    yAxisId="crew"
                    data={crewBaseline}
                    dataKey="mean"
                    name={`Baseline ${label.name.toLowerCase()}`}
                    stroke="var(--series-prv)"
                    strokeWidth={1}
                    strokeDasharray="5 4"
                    strokeOpacity={0.8}
                    dot={false}
                    activeDot={false}
                    isAnimationActive={false}
                    className="series-baseline"
                  />
                ) : null}
                <Line
                  yAxisId="co2"
                  data={data.co2.points}
                  dataKey="mean"
                  name="Cabin CO2 (mmHg)"
                  stroke="var(--series-co2)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                  className="series-co2"
                />
                <Line
                  yAxisId="crew"
                  data={data.crewSeries.points}
                  dataKey="mean"
                  name={`${label.name} (${label.unit})`}
                  stroke="var(--series-prv)"
                  strokeWidth={2}
                  dot={false}
                  isAnimationActive={false}
                  className="series-crew"
                />
                <Tooltip
                  cursor={{ stroke: "var(--ink)", strokeOpacity: 0.7, strokeWidth: 1 }}
                  isAnimationActive={false}
                  content={({ active, label: at }) => (
                    <Readout active={active} label={at as number} data={data} series={label} />
                  )}
                />
              </ComposedChart>
            </ResponsiveContainer>
          ) : (
            <p className="pt-8 text-center text-muted">
              {error ? "Could not load the timeline." : "Loading the timeline."}
            </p>
          )}
        </div>
        <ul className="mt-1 flex flex-wrap gap-x-3.5 gap-y-0.5 text-[0.6875rem] text-muted">
          <li className="inline-flex items-center gap-1.5">
            <LegendSwatch className="h-0.5 bg-series-co2" />
            Cabin CO2, measured
          </li>
          <li className="inline-flex items-center gap-1.5">
            <LegendSwatch className="h-0.5 bg-series-prv" />
            {label.name}, measured
          </li>
          <li className="inline-flex items-center gap-1.5">
            <LegendSwatch className="h-0 border-t border-dashed border-series-prv" />
            Baseline
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

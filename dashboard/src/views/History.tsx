import { useCallback, useState } from "react";
import { api } from "../api/client";
import type { History as HistoryData } from "../api/types.gen";
import { CusumChart } from "../components/CusumChart";
import { MetricChart } from "../components/MetricChart";
import { useSelectedCrew } from "../hooks/CrewSelection";
import { useStatusContext } from "../hooks/StatusContext";
import { usePolling } from "../hooks/usePolling";

type Metric = HistoryData["metric"];

const METRICS: { metric: Metric; label: string; crew: boolean }[] = [
  { metric: "prv", label: "Pulse-rate variability", crew: true },
  { metric: "hr", label: "Heart rate", crew: true },
  { metric: "hr_resid", label: "Heart-rate residual", crew: true },
  { metric: "spo2", label: "SpO2", crew: true },
  { metric: "skin_c", label: "Skin temperature", crew: true },
  { metric: "act", label: "Activity", crew: true },
  { metric: "co2_mmhg", label: "Cabin CO2", crew: false },
  { metric: "temp_c", label: "Cabin temperature", crew: false },
  { metric: "rh", label: "Cabin humidity", crew: false },
  { metric: "voc", label: "VOC index", crew: false },
  { metric: "pm25", label: "Fine particles (PM2.5)", crew: false },
  { metric: "cpm", label: "Radiation proxy, counts per minute", crew: false },
];

const RANGES = [
  { hours: 24, step: 600, label: "24 hours" },
  { hours: 168, step: 3600, label: "7 days" },
] as const;

export default function History() {
  const { data: status } = useStatusContext();
  const crewIds = status?.crew.map((c) => c.id) ?? [];
  const [crew, selectCrew] = useSelectedCrew(crewIds);
  const [metric, setMetric] = useState<Metric>("prv");
  const [rangeIndex, setRangeIndex] = useState(1);

  const spec = METRICS.find((m) => m.metric === metric)!;
  const range = RANGES[rangeIndex];
  const fetcher = useCallback(
    (signal: AbortSignal) =>
      api.history(
        { metric, crew: spec.crew ? crew : null, hours: range.hours, step: range.step },
        signal,
      ),
    [metric, spec.crew, crew, range.hours, range.step],
  );
  const { data, error } = usePolling(fetcher, 30_000, 90_000, `${metric}/${crew}/${rangeIndex}`);

  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-base font-semibold">History</h1>
      <form
        aria-label="What to show"
        className="flex flex-wrap items-end gap-3 pane px-2.5 py-2"
        onSubmit={(e) => e.preventDefault()}
      >
        <div className="flex flex-col gap-0.5">
          <label htmlFor="metric" className="micro-label">
            Measure
          </label>
          <select
            id="metric"
            value={metric}
            onChange={(e) => setMetric(e.target.value as Metric)}
            className="h-7 border border-line-strong bg-surface px-1.5 text-xs"
          >
            {METRICS.map((m) => (
              <option key={m.metric} value={m.metric}>
                {m.label}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-0.5">
          <label htmlFor="crew" className="micro-label">
            Crew member
          </label>
          <select
            id="crew"
            value={crew ?? ""}
            disabled={!spec.crew}
            onChange={(e) => selectCrew(e.target.value)}
            className="h-7 border border-line-strong bg-surface px-1.5 text-xs disabled:text-muted"
          >
            {crewIds.map((id) => (
              <option key={id} value={id}>
                {id}
              </option>
            ))}
          </select>
        </div>
        <div className="flex flex-col gap-0.5">
          <label htmlFor="range" className="micro-label">
            Time span
          </label>
          <select
            id="range"
            value={rangeIndex}
            onChange={(e) => setRangeIndex(Number(e.target.value))}
            className="h-7 border border-line-strong bg-surface px-1.5 text-xs"
          >
            {RANGES.map((r, i) => (
              <option key={r.hours} value={i}>
                {r.label}
              </option>
            ))}
          </select>
        </div>
        {!spec.crew ? (
          <p className="text-xs text-muted">Cabin measures are not per crew member.</p>
        ) : null}
      </form>

      <section aria-label="Trend" className="pane p-2.5">
        {data ? (
          <MetricChart
            history={data}
            title={`${spec.label}${spec.crew && crew ? ` · ${crew}` : ""}, last ${range.label}`}
            height="h-72"
          />
        ) : (
          <p className="text-muted">
            {error ? "Could not load the history." : "Loading the history."}
          </p>
        )}
      </section>

      <section aria-label="Drift detector" className="pane p-2.5">
        {data?.cusum ? (
          <CusumChart cusum={data.cusum} />
        ) : (
          <p className="text-muted">
            {data ? "There is no drift detector for this measure." : "Loading the drift detector."}
          </p>
        )}
      </section>
    </div>
  );
}

import { useCallback, useState } from "react";
import { Link, useParams } from "react-router";
import { api } from "../api/client";
import type { History, Status } from "../api/types.gen";
import { CusumChart } from "../components/CusumChart";
import { LearningBadge, LevelBadge } from "../components/LevelBadge";
import { MetricChart } from "../components/MetricChart";
import { COLS, NumberRow, deviation } from "../components/RiskCards";
import { useStatusContext } from "../hooks/StatusContext";
import { usePolling } from "../hooks/usePolling";
import { clockTimeSeconds, formatDuration } from "../lib/format";
import { DOMAIN_NAME } from "../lib/levels";
import { KIND_LABEL, KindGlyph } from "./Events";

type Member = Status["crew"][number];

/** The per-person measures, in the order a flight surgeon reads them. */
const VITALS: { metric: History["metric"]; label: string }[] = [
  { metric: "hr", label: "Resting HR" },
  { metric: "prv", label: "Pulse-rate variability" },
  { metric: "hr_resid", label: "HR residual" },
  { metric: "spo2", label: "SpO2" },
  { metric: "skin_c", label: "Skin temperature" },
  { metric: "act", label: "Activity" },
];

const RANGES = [
  { hours: 6, step: 60, label: "6 h" },
  { hours: 24, step: 600, label: "24 h" },
  { hours: 168, step: 3600, label: "7 d" },
] as const;

const SOURCE_WORD = { hw: "Hardware", sim: "Simulator" } as const;

/** The latest reading and the baseline it is judged against, as a card-style number. */
function latest(h: History) {
  const point = h.points.at(-1) ?? null;
  const base = h.baseline?.at(-1) ?? null;
  return { value: point?.mean ?? null, baseline: base?.mean ?? null };
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-px">
      <span className="micro-label">{label}</span>
      <span className="readout text-xs font-semibold text-ink">{children}</span>
    </div>
  );
}

function Header({ member, now }: { member: Member; now: number }) {
  const learning = member.baseline === "learning";
  return (
    <header className="pane flex flex-wrap items-center gap-x-6 gap-y-2 px-3 py-2">
      <div className="flex items-center gap-3">
        <h1 className="text-lg leading-6 font-semibold">{member.name}</h1>
        {learning ? <LearningBadge /> : <LevelBadge level={member.level} />}
      </div>
      <div className="flex flex-wrap gap-x-5 gap-y-1.5">
        <Fact label="Crew id">{member.id}</Fact>
        <Fact label="Source">{SOURCE_WORD[member.src]}</Fact>
        <Fact label="Link">
          <span className="inline-flex items-center gap-1">
            {member.online ? (
              <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
                <circle cx="4" cy="4" r="3" fill="var(--green-mark)" />
              </svg>
            ) : (
              <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
                <path d="M1 1 L7 7 M7 1 L1 7" stroke="var(--yellow-mark)" strokeWidth="1.6" />
              </svg>
            )}
            {member.online ? "Online" : "Offline"}
          </span>
        </Fact>
        <Fact label="Baseline">
          {learning && member.learning_ends_t !== null
            ? `Learning, ${formatDuration(member.learning_ends_t - now)} left`
            : learning
              ? "Learning"
              : "Ready"}
        </Fact>
        <Fact label="Last seen">
          {member.last_seen_t === null ? "—" : `${clockTimeSeconds(member.last_seen_t)} UTC`}
        </Fact>
      </div>
    </header>
  );
}

/** One crew member: their vitals against their own baseline, every series, and their events. */
export default function Crew() {
  const { id } = useParams();
  const { data: status } = useStatusContext();
  const [rangeIndex, setRangeIndex] = useState(0);
  const range = RANGES[rangeIndex];
  const member = status?.crew.find((c) => c.id === id) ?? null;

  const fetcher = useCallback(
    async (signal: AbortSignal) => {
      if (!id) return null;
      const [series, events] = await Promise.all([
        Promise.all(
          VITALS.map((v) =>
            api.history(
              { metric: v.metric, crew: id, hours: range.hours, step: range.step },
              signal,
            ),
          ),
        ),
        api.events({ limit: 500 }, signal),
      ]);
      return { series, events };
    },
    [id, range.hours, range.step],
  );
  const { data, error } = usePolling(fetcher, 15_000, 45_000, `${id}/${rangeIndex}`);

  if (!id || (status && !member)) {
    return (
      <section>
        <h1 className="text-base font-semibold">Crew member not found</h1>
        <p className="mt-2 text-muted">There is no crew member called “{id}”.</p>
        <p className="mt-4">
          <Link to="/" className="font-medium text-accent underline">
            Back to the overview
          </Link>
        </p>
      </section>
    );
  }

  const events = [...(data?.events.events ?? [])]
    .filter((e) => e.crew === id)
    .sort((a, b) => b.t - a.t)
    .slice(0, 40);
  const level = member?.level ?? "green";

  return (
    <div className="flex flex-col gap-2">
      <p>
        <Link to="/" className="text-xs font-medium text-accent underline">
          ← Overview
        </Link>
      </p>
      {member && status ? <Header member={member} now={status.t} /> : null}

      <div className="grid grid-cols-1 items-start gap-2 xl:grid-cols-[minmax(0,2fr)_minmax(0,3fr)]">
        <section aria-label="Vitals" className="pane pane-flush">
          <div className="panel-head">
            <h2 className="text-xs font-semibold">
              <span className="micro-label mr-2">Now</span>
              Vitals against {id}'s baseline
            </h2>
            <div role="group" aria-label="Range" className="flex border border-line-strong">
              {RANGES.map((r, i) => (
                <button
                  key={r.hours}
                  type="button"
                  aria-pressed={rangeIndex === i}
                  onClick={() => setRangeIndex(i)}
                  className={`h-5.5 px-2 text-[0.6875rem] font-semibold ${i > 0 ? "border-l border-line-strong" : ""} ${
                    rangeIndex === i ? "bg-ink text-surface" : "text-ink hover:bg-panel-2"
                  }`}
                >
                  {r.label}
                </button>
              ))}
            </div>
          </div>
          <div className="px-3 pt-1 pb-2">
            <div aria-hidden="true" className={`${COLS} h-3.5 border-b border-line`}>
              <span className="micro-label text-[0.5625rem]">Reading</span>
              <span className="micro-label text-right text-[0.5625rem]">Value</span>
              <span className="micro-label text-right text-[0.5625rem]">vs baseline</span>
            </div>
            {data ? (
              <dl data-testid="crew-vitals" className="mt-0.5 flex flex-col">
                {data.series.map((h, i) => {
                  const n = { label: VITALS[i].label, unit: h.unit, ...latest(h) };
                  const d = Math.abs(deviation(n) ?? 0);
                  return (
                    <NumberRow
                      key={h.metric}
                      n={n}
                      flagged={level !== "green" && d > 0.1}
                      level={level}
                    />
                  );
                })}
              </dl>
            ) : (
              <p className="py-2 text-muted">
                {error ? "Could not load the vitals." : "Loading the vitals."}
              </p>
            )}
          </div>
        </section>

        <section aria-label="Recent events" className="pane pane-flush">
          <div className="panel-head">
            <h2 className="text-xs font-semibold">
              <span className="micro-label mr-2">Log</span>
              Events for {id}
            </h2>
            <span className="readout text-[0.6875rem] text-muted">{events.length} shown</span>
          </div>
          {!data ? (
            <p className="p-2 text-muted">{error ? "Could not load the events." : "Loading."}</p>
          ) : events.length === 0 ? (
            <p className="p-2 text-muted">No events for {id} yet.</p>
          ) : (
            <ol className="max-h-56 divide-y divide-line overflow-y-auto text-xs">
              {events.map((e) => (
                <li
                  key={e.id}
                  data-testid={`event-${e.id}`}
                  data-kind={e.kind}
                  className={`flex min-h-6 flex-wrap items-center gap-x-3 gap-y-0.5 px-2.5 py-0.5 ${
                    e.kind === "checkin" ? "text-muted" : ""
                  }`}
                >
                  <time
                    className="readout w-16 shrink-0 text-[0.6875rem]"
                    dateTime={new Date(e.t * 1000).toISOString()}
                  >
                    {clockTimeSeconds(e.t)}
                  </time>
                  <span className="inline-flex w-28 shrink-0 items-center gap-1.5 font-semibold">
                    <KindGlyph kind={e.kind} />
                    {KIND_LABEL[e.kind]}
                  </span>
                  <span className="min-w-0 flex-1">{e.sentence}</span>
                  {e.domain ? (
                    <span className="readout text-[0.6875rem] text-muted">
                      {DOMAIN_NAME[e.domain]}
                    </span>
                  ) : null}
                </li>
              ))}
            </ol>
          )}
        </section>
      </div>

      <section aria-label="Series" className="grid grid-cols-1 gap-2 xl:grid-cols-3">
        {data ? (
          data.series.map((h, i) => (
            <div key={h.metric} className="pane p-2.5">
              <MetricChart
                history={h}
                title={`${VITALS[i].label} · ${id}, last ${range.label}`}
                height="h-44"
              />
            </div>
          ))
        ) : (
          <p className="text-muted">
            {error ? "Could not load the series." : "Loading the series."}
          </p>
        )}
      </section>

      {data?.series[1]?.cusum ? (
        <section aria-label="Drift detector" className="pane p-2.5">
          <CusumChart cusum={data.series[1].cusum} />
        </section>
      ) : null}
    </div>
  );
}

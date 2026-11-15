import type { Status } from "../api/types.gen";
import { useCloudInfo } from "../hooks/useCloudInfo";
import { useStatusContext } from "../hooks/StatusContext";
import { clockTime } from "../lib/format";
import { useDataSource } from "../lib/useDataSource";
import { EarthGlobe } from "./EarthGlobe";

const SOURCE_WORD: Record<Status["source_mode"], string> = { live: "Live", mock: "Simulated" };

/** What the Source fact says. On the Pi and the local mock this is Live or Simulated. */
export function sourceLabel(
  mode: Status["source_mode"],
  choice: "live" | "demo",
  onPublicSite: boolean,
): string {
  if (!onPublicSite) return SOURCE_WORD[mode];
  if (choice === "live" && mode === "mock") return "Demo, the Pi did not answer";
  return choice === "demo" ? "Demo" : "Live";
}
const THRESHOLD_WORD: Record<Status["thresholds_status"], string> = {
  provisional: "Provisional",
  final: "Final",
};

/** YYYY-MM-DD in UTC from unix seconds. */
function dateUtc(t: number): string {
  return new Date(t * 1000).toISOString().slice(0, 10);
}

function Fact({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex flex-col gap-px">
      <span className="micro-label">{label}</span>
      <span className="readout text-xs font-semibold text-ink">{children}</span>
    </div>
  );
}

/**
 * The mission card: Earth, the vehicle's home, beside the facts a crew member glances at first.
 * Everything here is already in the status payload; the card adds no data.
 */
export function MissionCard() {
  const { data: status } = useStatusContext();
  const choice = useDataSource();
  const cloud = useCloudInfo();
  const crew = status?.crew ?? [];
  const online = crew.filter((c) => c.online).length;
  const learning = crew.filter((c) => c.baseline === "learning").length;
  return (
    <section
      aria-label="Mission"
      data-testid="mission-card"
      className="pane pane-flush relative flex min-h-40 items-center gap-3 py-3 pr-3 pl-4 xl:h-40 xl:py-0"
    >
      <span
        aria-hidden="true"
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(22rem 12rem at 100% 50%, rgba(63, 169, 255, 0.22), transparent 70%)",
        }}
      />
      <div className="relative z-10 flex min-w-0 flex-1 flex-col gap-2">
        <div>
          <p className="micro-label">Crew Context Monitor</p>
          <h2 className="mt-0.5 text-lg leading-6 font-semibold">
            {status ? `Good shift, crew.` : "Waiting for the monitor."}
          </h2>
          <p className="mt-0.5 text-xs text-muted">
            {status
              ? `${crew.length} aboard · ${online} online · ${dateUtc(status.t)} ${clockTime(status.t)} UTC`
              : "The first reading has not arrived yet."}
          </p>
        </div>
        {status ? (
          <div className="flex flex-wrap gap-x-5 gap-y-1.5">
            <Fact label="Source">{sourceLabel(status.source_mode, choice, cloud !== null)}</Fact>
            <Fact label="Thresholds">{THRESHOLD_WORD[status.thresholds_status]}</Fact>
            <Fact label="Baselines">{learning === 0 ? "All ready" : `${learning} learning`}</Fact>
            <Fact label="Check-in">{status.checkin_due ? "Due" : "Done"}</Fact>
          </div>
        ) : null}
      </div>
      <div className="relative z-10 shrink-0">
        <EarthGlobe size={150} />
      </div>
    </section>
  );
}

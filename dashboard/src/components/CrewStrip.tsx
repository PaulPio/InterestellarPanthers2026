import { Link } from "react-router";
import { useSelectedCrew } from "../hooks/CrewSelection";
import { useStatusContext } from "../hooks/StatusContext";
import { LevelBadge } from "./LevelBadge";

const SOURCE_WORD = { hw: "Hardware", sim: "Simulator" } as const;

/**
 * The crew roster: one row per crew member with level, online state and where the data comes
 * from. The row's button picks who the timeline follows; the link opens that person's page.
 */
export function CrewStrip() {
  const { data: status } = useStatusContext();
  const ids = status?.crew.map((c) => c.id) ?? [];
  const [selected, select] = useSelectedCrew(ids);
  if (!status) return <div data-testid="crew-strip" className="pane h-40" />;
  return (
    <section aria-label="Crew" className="pane pane-flush flex h-40 flex-col">
      <div className="panel-head">
        <h2 className="text-xs font-semibold">
          <span className="micro-label mr-2">Roster</span>
          Crew
        </h2>
        <span className="readout text-[0.6875rem] text-muted">
          {status.crew.filter((c) => c.online).length}/{status.crew.length} online
        </span>
      </div>
      <ul
        data-testid="crew-strip"
        className="flex flex-1 flex-col divide-y divide-line overflow-y-auto"
      >
        {status.crew.map((c) => {
          const isSelected = selected === c.id;
          return (
            <li key={c.id} className="flex items-center gap-1.5 pr-1.5">
              <button
                type="button"
                aria-pressed={isSelected}
                onClick={() => select(c.id)}
                className={`flex h-8 min-w-0 flex-1 items-center gap-2 border-l-2 px-2 text-left whitespace-nowrap hover:bg-panel-2 ${
                  isSelected
                    ? "border-accent bg-[linear-gradient(90deg,rgba(124,196,255,0.16),transparent)]"
                    : "border-transparent"
                }`}
              >
                <span className="min-w-0 flex-1 truncate text-xs font-semibold">{c.name}</span>
                <LevelBadge level={c.level} />
                <span className="flex items-center gap-2 text-[0.6875rem]">
                  <span
                    data-testid={`source-${c.id}`}
                    className="readout inline-flex h-4.5 items-center border border-line-strong px-1 text-[0.625rem]"
                  >
                    {SOURCE_WORD[c.src]}
                  </span>
                  <span className="inline-flex items-center gap-1 sm:w-14">
                    {c.online ? (
                      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
                        <circle cx="4" cy="4" r="3" fill="var(--green-mark)" />
                      </svg>
                    ) : (
                      <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
                        <path
                          d="M1 1 L7 7 M7 1 L1 7"
                          stroke="var(--yellow-mark)"
                          strokeWidth="1.6"
                        />
                      </svg>
                    )}
                    <span className="hidden sm:inline">{c.online ? "Online" : "Offline"}</span>
                  </span>
                  <span className="hidden w-[4.75rem] items-center gap-1 font-semibold sm:inline-flex">
                    {isSelected ? (
                      <>
                        <svg width="10" height="10" viewBox="0 0 10 10" aria-hidden="true">
                          <path
                            d="M1 5.2 L4 8 L9 2"
                            fill="none"
                            stroke="var(--ink)"
                            strokeWidth="1.8"
                          />
                        </svg>
                        On timeline
                      </>
                    ) : null}
                  </span>
                </span>
              </button>
              <Link
                to={`/crew/${c.id}`}
                aria-label={`Open ${c.name}'s page`}
                className="inline-flex h-6 shrink-0 items-center border border-line-strong px-2 text-[0.6875rem] font-semibold hover:border-accent hover:bg-panel-2"
              >
                Open →
              </Link>
            </li>
          );
        })}
      </ul>
    </section>
  );
}

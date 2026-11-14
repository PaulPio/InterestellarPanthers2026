import type { ReactNode } from "react";
import type { Attribution, Level, Status } from "../api/types.gen";
import { useStatusContext } from "../hooks/StatusContext";
import { clockTimeSeconds, formatDuration } from "../lib/format";
import { DOMAIN_NAME, LEVEL_WORD } from "../lib/levels";
import { LevelGlyph } from "./LevelBadge";

const ATTRIBUTION_LABEL: Record<Exclude<Attribution, null>, string> = {
  cabin: "Cabin",
  individual: "Individual",
  unclear: "Unclear",
};

/** Seconds until alerts start while any baseline is still learning, else null. */
export function learningRemaining(status: Status): number | null {
  const ends = status.crew
    .filter((c) => c.baseline === "learning" && c.learning_ends_t !== null)
    .map((c) => (c.learning_ends_t as number) - status.t);
  return ends.length > 0 ? Math.max(...ends) : null;
}

// One fixed size for every state, so a change of level never moves the page. Level-dependent
// styling lives on children only; this frame's classes never change.
const FRAME = "relative flex h-14 items-center gap-3 overflow-hidden pane pane-flush pr-3 pl-2";

const RAIL: Record<Level, string> = {
  green: "w-0.5 bg-level-green-mark",
  yellow: "w-1 bg-level-yellow-mark",
  red: "w-1 bg-level-red-mark",
};

const ANNUNCIATOR: Record<Level, string> = {
  green: "border-level-green-mark bg-level-green-bg",
  yellow: "border-level-yellow-mark bg-level-yellow-bg",
  red: "border-level-red-mark bg-level-red-bg",
};

/** The master annunciator: overall level as word, shape and color in a fixed-size block. */
function Annunciator({ level }: { level: Level }) {
  return (
    <div
      data-level={level}
      className={`ml-1 flex h-11 w-28 shrink-0 flex-col justify-center gap-0.5 border px-2.5 text-ink ${ANNUNCIATOR[level]}`}
    >
      <span className="text-[0.5625rem] leading-3 font-semibold tracking-[0.14em] uppercase">
        Overall
      </span>
      <span className="flex items-center gap-1.5 text-base leading-5 font-semibold">
        <LevelGlyph level={level} size={13} />
        {LEVEL_WORD[level]}
      </span>
    </div>
  );
}

function ReadoutRow({ label, children }: { label: string; children: ReactNode }) {
  return (
    <p className="flex gap-2 leading-4 whitespace-nowrap">
      <span className="micro-label w-[4.5rem] shrink-0 leading-4">{label}</span>{" "}
      <span className="truncate">{children}</span>
    </p>
  );
}

/** The worst card's sentence, its attribution and the suggested action, in one row. */
export function StatusBar() {
  const { data: status } = useStatusContext();
  if (!status) {
    return (
      <section aria-label="Crew status" data-testid="status-bar" className={FRAME}>
        <p className="pl-2 text-muted">Waiting for the first reading.</p>
      </section>
    );
  }
  const { crew_status: cs } = status;
  const remaining = learningRemaining(status);
  const learning = remaining !== null;
  return (
    <section
      aria-label="Crew status"
      data-testid="status-bar"
      data-state={learning ? "learning" : cs.level}
      className={FRAME}
    >
      <span aria-hidden="true" className={`absolute inset-y-0 left-0 ${RAIL[cs.level]}`} />
      <Annunciator level={cs.level} />
      <div className="flex min-w-0 flex-[1_1_11rem] flex-col gap-0.5">
        <p className="line-clamp-2 text-[0.8125rem] leading-[1.125rem] font-semibold sm:line-clamp-1">
          {cs.sentence}
        </p>
        {cs.action ? (
          <p className="truncate text-xs leading-4 text-muted">
            <span className="font-semibold text-ink">Next step:</span> {cs.action}
          </p>
        ) : null}
      </div>
      <div className="readout hidden min-w-[5.25rem] flex-[0_3_15rem] flex-col sm:flex justify-center self-stretch overflow-hidden border-l border-line pl-3 text-[0.6875rem]">
        {learning ? (
          <div className="flex items-center gap-1.5 border border-dashed border-level-learn-mark bg-level-learn-bg px-1.5 py-1 font-sans text-xs text-ink">
            <LevelGlyph level="learning" size={11} />
            <p>
              <span className="font-semibold">Learning.</span> Alerts start in{" "}
              <span className="readout">{formatDuration(remaining)}</span>
            </p>
          </div>
        ) : (
          <>
            {cs.domain ? <ReadoutRow label="Domain">{DOMAIN_NAME[cs.domain]}</ReadoutRow> : null}
            {cs.attribution ? (
              <ReadoutRow label="Attribution:">{ATTRIBUTION_LABEL[cs.attribution]}</ReadoutRow>
            ) : null}
            <ReadoutRow label="Updated">
              <span className="text-muted">{clockTimeSeconds(status.t)} UTC</span>
            </ReadoutRow>
          </>
        )}
      </div>
    </section>
  );
}

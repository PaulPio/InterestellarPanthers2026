import { useState } from "react";
import { api } from "../api/client";
import { useSelectedCrew } from "../hooks/CrewSelection";
import { useStatusContext } from "../hooks/StatusContext";

const MOOD_WORDS = ["Low", "Down", "Okay", "Good", "Great"];
const SLEEP_WORDS = ["Poor", "Short", "Okay", "Good", "Great"];

function Scale({
  label,
  words,
  value,
  onPick,
}: {
  label: string;
  words: string[];
  value: number | null;
  onPick: (n: number) => void;
}) {
  return (
    <div role="group" aria-label={`${label}, 1 to 5`} className="flex flex-col gap-1">
      <p className="micro-label">{label}</p>
      <div className="flex gap-1">
        {words.map((word, i) => {
          const n = i + 1;
          return (
            <button
              key={n}
              type="button"
              aria-pressed={value === n}
              onClick={() => onPick(n)}
              aria-label={`${label} ${n} ${word}`}
              className={`flex h-10 min-w-0 flex-1 flex-col items-center justify-center border text-xs ${
                value === n
                  ? "border-ink bg-panel-2 ring-1 ring-ink"
                  : "border-line-strong hover:bg-panel-2"
              }`}
            >
              <span className="readout text-[0.8125rem] leading-4 font-semibold">{n}</span>{" "}
              <span className="text-[0.625rem] leading-3 text-muted">{word}</span>
            </button>
          );
        })}
      </div>
    </div>
  );
}

/**
 * The daily check-in: mood and sleep on a 1 to 5 scale and an optional note. It floats at the
 * bottom of the screen so it never moves the page, and it is context only: sending it changes
 * no level and no color.
 */
export function CheckinPrompt() {
  const { data: status, refresh } = useStatusContext();
  const [crew] = useSelectedCrew(status?.crew.map((c) => c.id) ?? []);
  const [mood, setMood] = useState<number | null>(null);
  const [sleep, setSleep] = useState<number | null>(null);
  const [note, setNote] = useState("");
  const [showNote, setShowNote] = useState(false);
  const [dismissed, setDismissed] = useState(false);
  const [sending, setSending] = useState(false);
  const [error, setError] = useState<string | null>(null);

  if (!status?.checkin_due || dismissed || !crew) return null;

  async function send() {
    if (mood === null || sleep === null || crew === null) return;
    setSending(true);
    setError(null);
    try {
      await api.checkin({ crew, mood, sleep, note: note.trim() === "" ? null : note.trim() });
      setDismissed(true);
      refresh();
    } catch {
      setError("Could not send the check-in. Try again.");
    } finally {
      setSending(false);
    }
  }

  return (
    <aside
      role="dialog"
      aria-label="Daily check-in"
      className="fixed right-3 bottom-3 z-40 flex w-80 max-w-[calc(100vw-1.5rem)] flex-col gap-2 border border-line-strong bg-panel p-2.5 shadow-[0_8px_24px_rgb(0_0_0/0.35)]"
    >
      <div className="flex items-center justify-between gap-2">
        <h2 className="text-[0.8125rem] font-semibold">Daily check-in for {crew}</h2>
        <button
          type="button"
          onClick={() => setDismissed(true)}
          className="h-7 shrink-0 border border-line-strong px-2.5 text-xs font-semibold"
        >
          Not now
        </button>
      </div>
      <p className="text-[0.6875rem] text-muted">
        Fifteen seconds. This adds context; it never changes a level.
      </p>
      <Scale label="Mood" words={MOOD_WORDS} value={mood} onPick={setMood} />
      <Scale label="Sleep" words={SLEEP_WORDS} value={sleep} onPick={setSleep} />
      {showNote ? (
        <div className="flex flex-col gap-1">
          <label htmlFor="checkin-note" className="micro-label">
            Note (optional)
          </label>
          <textarea
            id="checkin-note"
            value={note}
            maxLength={500}
            onChange={(e) => setNote(e.target.value)}
            rows={2}
            className="border border-line-strong bg-surface px-2 py-1 text-xs"
          />
        </div>
      ) : (
        <button
          type="button"
          onClick={() => setShowNote(true)}
          className="h-6 self-start text-xs text-ink underline underline-offset-3"
        >
          Add a note
        </button>
      )}
      {error ? (
        <p role="alert" className="text-xs text-level-red">
          {error}
        </p>
      ) : null}
      <button
        type="button"
        disabled={mood === null || sleep === null || sending}
        onClick={() => void send()}
        className="h-8 bg-accent text-xs font-semibold text-accent-ink disabled:bg-panel-2 disabled:text-muted"
      >
        Send check-in
      </button>
    </aside>
  );
}

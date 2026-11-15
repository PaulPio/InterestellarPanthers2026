import { useState, type FormEvent } from "react";
import { api } from "../api/client";
import type { Settings as SettingsData } from "../api/types.gen";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { useCloudInfo } from "../hooks/useCloudInfo";
import { useStatusContext } from "../hooks/StatusContext";
import { STALE_MS, usePolling } from "../hooks/usePolling";
import { setDataSource } from "../lib/dataSource";
import { clockTime } from "../lib/format";
import { useDataSource } from "../lib/useDataSource";

type Tier = keyof SettingsData["auto_approve"];

const TIERS: { key: Tier; label: string; help: string }[] = [
  { key: "crew_only", label: "Crew only", help: "Summaries that stay with the crew" },
  { key: "flight_surgeon", label: "Flight surgeon", help: "Summaries for the flight surgeon" },
  { key: "urgent", label: "Urgent", help: "Anything marked urgent" },
];

export default function Settings() {
  const source = useDataSource();
  const cloud = useCloudInfo();
  const { data: status } = useStatusContext();
  const { data, error } = usePolling((s) => api.settings(s), 30_000, STALE_MS, source);
  const [latest, setLatest] = useState<SettingsData | null>(null);
  const settings = latest ?? data;

  const [toggleError, setToggleError] = useState<string | null>(null);
  const [resetting, setResetting] = useState<string | null>(null);
  const [resetDone, setResetDone] = useState<string | null>(null);
  const [resetError, setResetError] = useState<string | null>(null);
  const [id, setId] = useState("");
  const [name, setName] = useState("");
  const [formError, setFormError] = useState<string | null>(null);
  const [enrolled, setEnrolled] = useState<string | null>(null);

  async function toggle(tier: Tier, on: boolean) {
    if (!settings) return;
    const before = settings;
    const next = { ...settings.auto_approve, [tier]: on };
    setToggleError(null);
    setLatest({ ...settings, auto_approve: next }); // optimistic
    try {
      setLatest(await api.putAutoApprove(next));
    } catch {
      setLatest(before); // roll back
      setToggleError("Could not save that change. The setting is back to what it was.");
    }
  }

  async function enroll(e: FormEvent) {
    e.preventDefault();
    setEnrolled(null);
    if (!/^W[0-9]+$/.test(id)) {
      setFormError("Crew id must be W followed by a number, for example W4.");
      return;
    }
    if (name.trim().length === 0 || name.trim().length > 40) {
      setFormError("Enter a name of 1 to 40 characters.");
      return;
    }
    setFormError(null);
    try {
      setLatest(await api.enroll({ id, name: name.trim() }));
      setEnrolled(`${name.trim()} (${id}) is enrolled.`);
      setId("");
      setName("");
    } catch {
      setFormError("Could not enroll that crew member. Try again.");
    }
  }

  async function confirmReset() {
    if (!resetting) return;
    const crew = resetting;
    setResetting(null);
    setResetError(null);
    setResetDone(null);
    try {
      setLatest(await api.resetBaseline({ crew, metric: null }));
      setResetDone(
        `${crew}'s baseline was reset. Alerts for ${crew} resume after it has learned again.`,
      );
    } catch {
      setResetError(`Could not reset ${crew}'s baseline. Nothing was changed.`);
    }
  }

  return (
    <div className="flex flex-col gap-2">
      <h1 className="text-base font-semibold">Settings</h1>
      {cloud ? (
        <section aria-label="Data source" className="pane p-2.5">
          <h2 className="micro-label">Data source</h2>
          <p className="mt-0.5 text-xs text-muted">
            Live reads the cabin node through the Pi. Demo plays a scripted scenario in this browser
            only. Heart rate stays simulated until the wearable sends its own data.
          </p>
          <div className="mt-1.5 flex flex-col">
            {(
              [
                ["live", "Live"],
                ["demo", "Demo"],
              ] as const
            ).map(([value, label]) => (
              <label key={value} className="flex min-h-7 items-center gap-2 text-xs">
                <input
                  type="radio"
                  name="data-source"
                  value={value}
                  checked={source === value}
                  onChange={() => setDataSource(value)}
                  className="h-4 w-4 accent-accent"
                />
                <span className="font-semibold">{label}</span>
              </label>
            ))}
          </div>
          {source === "live" && !cloud.pi_configured ? (
            <p role="status" className="mt-1 text-xs">
              The public site is not connected to the Pi yet, so Live shows the demo.
            </p>
          ) : null}
          {source === "live" && cloud.pi_configured && status?.source_mode === "mock" ? (
            <p role="status" className="mt-1 text-xs">
              The Pi did not answer, so this browser is showing the demo.
            </p>
          ) : null}
        </section>
      ) : null}
      {!settings ? (
        <p className="text-muted">
          {error ? "Could not load the settings." : "Loading the settings."}
        </p>
      ) : (
        <>
          <section aria-label="Crew" className="pane p-2.5">
            <h2 className="micro-label">Crew</h2>
            <ul className="mt-1 divide-y divide-line">
              {settings.crew.map((c) => (
                <li
                  key={c.id}
                  className="flex min-h-8 flex-wrap items-center justify-between gap-2 py-0.5"
                >
                  <span>
                    <span className="font-semibold">{c.name}</span>
                    <span className="readout text-xs text-muted">
                      {" "}
                      · {c.id} · enrolled {clockTime(c.enrolled_t)} UTC
                    </span>
                  </span>
                  <button
                    type="button"
                    onClick={() => setResetting(c.id)}
                    aria-label={`Reset baseline for ${c.id}`}
                    className="h-6.5 border border-line-strong px-2.5 text-[0.6875rem] font-semibold hover:bg-panel-2"
                  >
                    Reset baseline
                  </button>
                </li>
              ))}
            </ul>
            {resetDone ? (
              <p role="status" className="mt-1 text-xs">
                {resetDone}
              </p>
            ) : null}
            {resetError ? (
              <p role="alert" className="mt-1 text-xs text-level-red">
                {resetError}
              </p>
            ) : null}

            <form
              onSubmit={enroll}
              aria-label="Enroll a crew member"
              className="mt-2.5 flex flex-wrap items-end gap-2"
            >
              <div className="flex flex-col gap-0.5">
                <label htmlFor="enroll-id" className="micro-label">
                  Crew id
                </label>
                <input
                  id="enroll-id"
                  value={id}
                  onChange={(e) => setId(e.target.value)}
                  placeholder="W4"
                  className="readout h-7 w-24 border border-line-strong bg-surface px-1.5 text-xs"
                />
              </div>
              <div className="flex flex-col gap-0.5">
                <label htmlFor="enroll-name" className="micro-label">
                  Name
                </label>
                <input
                  id="enroll-name"
                  value={name}
                  onChange={(e) => setName(e.target.value)}
                  className="h-7 w-52 border border-line-strong bg-surface px-1.5 text-xs"
                />
              </div>
              <button
                type="submit"
                className="h-7 border border-accent px-3 text-xs font-semibold hover:bg-panel-2"
              >
                Enroll
              </button>
            </form>
            {formError ? (
              <p role="alert" className="mt-1 text-xs text-level-red">
                {formError}
              </p>
            ) : null}
            {enrolled ? (
              <p role="status" className="mt-1 text-xs">
                {enrolled}
              </p>
            ) : null}
          </section>

          <section aria-label="Auto-approve" className="pane p-2.5">
            <h2 className="micro-label">Send to Earth without asking</h2>
            <p className="mt-0.5 text-xs text-muted">
              When a tier is on, new summaries in it are approved automatically. Everything else
              waits for approval in the Earth queue.
            </p>
            <ul className="mt-1.5 flex flex-col">
              {TIERS.map((t) => (
                <li key={t.key}>
                  <label className="flex min-h-7 items-center gap-2 text-xs">
                    <input
                      type="checkbox"
                      checked={settings.auto_approve[t.key]}
                      onChange={(e) => void toggle(t.key, e.target.checked)}
                      className="h-4 w-4 accent-accent"
                    />
                    <span>
                      <span className="font-semibold">{t.label}</span>
                      <span className="text-muted"> · {t.help}</span>
                    </span>
                  </label>
                </li>
              ))}
            </ul>
            {toggleError ? (
              <p role="alert" className="mt-1 text-xs text-level-red">
                {toggleError}
              </p>
            ) : null}
          </section>
        </>
      )}

      {resetting ? (
        <ConfirmDialog
          title={`Reset ${resetting}'s baseline?`}
          confirmLabel="Reset baseline"
          onConfirm={() => void confirmReset()}
          onCancel={() => setResetting(null)}
        >
          <p>
            The monitor forgets what is normal for {resetting} and learns it again. Alerts for{" "}
            {resetting} are held back while it learns.
          </p>
        </ConfirmDialog>
      ) : null}
    </div>
  );
}

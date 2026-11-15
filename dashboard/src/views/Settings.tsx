import { useState, type FormEvent, type ReactNode } from "react";
import { api } from "../api/client";
import type { Settings as SettingsData, Status } from "../api/types.gen";
import { ConfirmDialog } from "../components/ConfirmDialog";
import { LearningBadge } from "../components/LevelBadge";
import { useStatusContext } from "../hooks/StatusContext";
import { usePolling } from "../hooks/usePolling";
import { clockTime } from "../lib/format";

type Tier = keyof SettingsData["auto_approve"];

const TIERS: { key: Tier; label: string; help: string }[] = [
  { key: "crew_only", label: "Crew only", help: "Summaries that stay with the crew" },
  { key: "flight_surgeon", label: "Flight surgeon", help: "Summaries for the flight surgeon" },
  { key: "urgent", label: "Urgent", help: "Anything marked urgent" },
];

const SECTIONS = [
  { id: "settings-crew", label: "Crew roster" },
  { id: "settings-earth", label: "Earth link" },
  { id: "settings-system", label: "System" },
];

const SOURCE_WORD: Record<Status["source_mode"], string> = { live: "Live", mock: "Simulated" };
const THRESHOLD_WORD: Record<Status["thresholds_status"], string> = {
  provisional: "Provisional",
  final: "Final",
};
const NODE_WORD = { wearable: "Wearable", cabin: "Cabin sensor" } as const;

/** One settings section: a pane with a title strip, a one-line description and its content. */
function Section({
  id,
  label,
  title,
  description,
  aside,
  children,
}: {
  id: string;
  label: string;
  title: string;
  description: string;
  aside?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section id={id} aria-label={label} className="pane pane-flush scroll-mt-28">
      <div className="flex flex-wrap items-start justify-between gap-x-4 gap-y-2 border-b border-line bg-[linear-gradient(90deg,rgba(124,196,255,0.13),rgba(169,149,255,0.05)_55%,transparent)] px-4 py-3">
        <div className="min-w-0">
          <h2 className="text-sm leading-5 font-semibold">{title}</h2>
          <p className="mt-0.5 text-xs text-muted">{description}</p>
        </div>
        {aside}
      </div>
      {children}
    </section>
  );
}

function Chip({ children }: { children: ReactNode }) {
  return (
    <span className="readout inline-flex h-6 shrink-0 items-center border border-line-strong bg-panel px-2.5 text-[0.6875rem] whitespace-nowrap">
      {children}
    </span>
  );
}

/** Online as a dot, offline as a cross, each beside its word. */
function LinkState({ online }: { online: boolean }) {
  return (
    <span className="inline-flex items-center gap-1.5 text-xs">
      {online ? (
        <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
          <circle cx="4" cy="4" r="3" fill="var(--green-mark)" />
        </svg>
      ) : (
        <svg width="8" height="8" viewBox="0 0 8 8" aria-hidden="true">
          <path d="M1 1 L7 7 M7 1 L1 7" stroke="var(--yellow-mark)" strokeWidth="1.6" />
        </svg>
      )}
      {online ? "Online" : "Offline"}
    </span>
  );
}

function Fact({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="flex flex-col gap-1 border border-line bg-[rgba(4,9,26,0.45)] px-3 py-2.5">
      <dt className="micro-label">{label}</dt>
      <dd className="text-sm font-semibold">{children}</dd>
    </div>
  );
}

export default function Settings() {
  const { data, error } = usePolling((s) => api.settings(s), 30_000);
  const { data: status } = useStatusContext();
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

  const tiersOn = settings ? TIERS.filter((t) => settings.auto_approve[t.key]).length : 0;

  return (
    <div className="mx-auto flex w-full max-w-[72rem] flex-col gap-4">
      <header className="flex flex-col gap-1 pt-1">
        <p className="micro-label">Configuration</p>
        <h1 className="brand-text text-2xl leading-8 font-semibold">Settings</h1>
        <p className="text-xs text-muted">
          Who the monitor watches, what goes to Earth without asking, and the state of the system.
        </p>
      </header>

      {!settings ? (
        <p className="pane px-4 py-6 text-muted">
          {error ? "Could not load the settings." : "Loading the settings."}
        </p>
      ) : (
        <div className="grid grid-cols-1 items-start gap-4 lg:grid-cols-[13rem_minmax(0,1fr)]">
          <nav
            aria-label="Settings sections"
            className="pane flex gap-1 overflow-x-auto p-1.5 lg:sticky lg:top-16 lg:flex-col"
          >
            {SECTIONS.map((s) => (
              <a
                key={s.id}
                href={`#${s.id}`}
                className="inline-flex h-9 shrink-0 items-center px-3 text-xs font-semibold text-muted hover:bg-[rgba(124,158,240,0.12)] hover:text-ink"
              >
                {s.label}
              </a>
            ))}
          </nav>

          <div className="flex min-w-0 flex-col gap-4">
            <Section
              id="settings-crew"
              label="Crew"
              title="Crew roster"
              description="Everyone the monitor follows. Each person has a personal baseline that alerts are measured against."
              aside={<Chip>{settings.crew.length} enrolled</Chip>}
            >
              {settings.crew.length === 0 ? (
                <p className="px-4 py-4 text-xs text-muted">No one is enrolled yet.</p>
              ) : (
                <ul className="divide-y divide-line">
                  {settings.crew.map((c) => {
                    const live = status?.crew.find((m) => m.id === c.id);
                    return (
                      <li
                        key={c.id}
                        className="flex flex-wrap items-center gap-x-4 gap-y-2 px-4 py-2.5"
                      >
                        <span
                          aria-hidden="true"
                          className="flex h-9 w-9 shrink-0 items-center justify-center bg-[linear-gradient(135deg,#2b4fb8,#6d5cf0)] text-sm font-semibold shadow-[inset_0_1px_0_rgba(255,255,255,0.25)]"
                        >
                          {c.name.trim().charAt(0).toUpperCase()}
                        </span>
                        <span className="flex min-w-0 flex-1 basis-40 flex-col">
                          <span className="truncate text-sm font-semibold">{c.name}</span>
                          <span className="readout text-[0.6875rem] text-muted">
                            {c.id} · enrolled {clockTime(c.enrolled_t)} UTC
                          </span>
                        </span>
                        {live ? (
                          <span className="flex items-center gap-3">
                            <LinkState online={live.online} />
                            {live.baseline === "learning" ? (
                              <LearningBadge />
                            ) : (
                              <span className="text-xs text-muted">Baseline ready</span>
                            )}
                          </span>
                        ) : null}
                        <button
                          type="button"
                          onClick={() => setResetting(c.id)}
                          aria-label={`Reset baseline for ${c.id}`}
                          className="btn"
                        >
                          Reset baseline
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
              {resetDone ? (
                <p role="status" className="border-t border-line px-4 py-2 text-xs">
                  {resetDone}
                </p>
              ) : null}
              {resetError ? (
                <p role="alert" className="border-t border-line px-4 py-2 text-xs text-level-red">
                  {resetError}
                </p>
              ) : null}

              <div className="border-t border-line bg-[rgba(4,9,26,0.35)] px-4 py-4">
                <h3 className="text-xs font-semibold">Enroll a crew member</h3>
                <p id="enroll-id-hint" className="mt-0.5 text-xs text-muted">
                  The id is the wearable's: W followed by a number, for example W4.
                </p>
                <form
                  onSubmit={enroll}
                  aria-label="Enroll a crew member"
                  className="mt-3 flex flex-wrap items-end gap-3"
                >
                  <div className="flex flex-col gap-1">
                    <label htmlFor="enroll-id" className="micro-label">
                      Crew id
                    </label>
                    <input
                      id="enroll-id"
                      value={id}
                      onChange={(e) => setId(e.target.value)}
                      placeholder="W4"
                      autoComplete="off"
                      aria-describedby="enroll-id-hint"
                      className="field readout w-24"
                    />
                  </div>
                  <div className="flex min-w-0 flex-1 basis-48 flex-col gap-1 sm:max-w-72">
                    <label htmlFor="enroll-name" className="micro-label">
                      Name
                    </label>
                    <input
                      id="enroll-name"
                      value={name}
                      onChange={(e) => setName(e.target.value)}
                      autoComplete="off"
                      className="field w-full"
                    />
                  </div>
                  <button type="submit" className="btn btn-primary h-9 px-5">
                    Enroll
                  </button>
                </form>
                {formError ? (
                  <p role="alert" className="mt-2 text-xs text-level-red">
                    {formError}
                  </p>
                ) : null}
                {enrolled ? (
                  <p role="status" className="mt-2 text-xs">
                    {enrolled}
                  </p>
                ) : null}
              </div>
            </Section>

            <Section
              id="settings-earth"
              label="Auto-approve"
              title="Send to Earth without asking"
              description="When a tier is on, new summaries in it are approved automatically. Everything else waits for approval in the Earth queue."
              aside={
                <Chip>
                  {tiersOn} of {TIERS.length} on
                </Chip>
              }
            >
              <ul className="divide-y divide-line">
                {TIERS.map((t) => {
                  const on = settings.auto_approve[t.key];
                  return (
                    <li key={t.key}>
                      <label className="flex min-h-14 cursor-pointer items-center gap-4 px-4 py-2 hover:bg-[rgba(124,158,240,0.07)]">
                        <span className="flex min-w-0 flex-1 flex-col">
                          <span className="text-sm font-semibold">{t.label}</span>
                          <span className="text-xs text-muted">{t.help}</span>
                        </span>
                        <span aria-hidden="true" className="readout w-6 text-right text-xs">
                          {on ? "On" : "Off"}
                        </span>
                        <input
                          type="checkbox"
                          checked={on}
                          onChange={(e) => void toggle(t.key, e.target.checked)}
                          className="switch"
                        />
                      </label>
                    </li>
                  );
                })}
              </ul>
              {toggleError ? (
                <p role="alert" className="border-t border-line px-4 py-2 text-xs text-level-red">
                  {toggleError}
                </p>
              ) : null}
            </Section>

            <Section
              id="settings-system"
              label="System"
              title="System"
              description="Where the readings come from and which sensor nodes are reporting. Read only."
            >
              {!status ? (
                <p className="px-4 py-4 text-xs text-muted">Waiting for the monitor.</p>
              ) : (
                <>
                  <dl className="grid grid-cols-1 gap-3 px-4 py-4 sm:grid-cols-3">
                    <Fact label="Data source">{SOURCE_WORD[status.source_mode]}</Fact>
                    <Fact label="Thresholds">{THRESHOLD_WORD[status.thresholds_status]}</Fact>
                    <Fact label="Nodes reporting">
                      <span className="readout">
                        {status.nodes.filter((n) => n.state === "online").length}/
                        {status.nodes.length}
                      </span>
                    </Fact>
                  </dl>
                  <ul className="divide-y divide-line border-t border-line">
                    {status.nodes.map((n) => (
                      <li
                        key={`${n.node}-${n.id}`}
                        className="flex min-h-10 flex-wrap items-center gap-x-4 gap-y-1 px-4 py-1.5"
                      >
                        <span className="flex min-w-0 flex-1 items-baseline gap-2">
                          <span className="readout text-xs font-semibold">{n.id}</span>
                          <span className="text-xs text-muted">{NODE_WORD[n.node]}</span>
                        </span>
                        <span className="readout text-[0.6875rem] text-muted">
                          {n.last_seen_t === null
                            ? "never seen"
                            : `last seen ${clockTime(n.last_seen_t)} UTC`}
                        </span>
                        <span className="w-16">
                          <LinkState online={n.state === "online"} />
                        </span>
                      </li>
                    ))}
                  </ul>
                </>
              )}
            </Section>
          </div>
        </div>
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

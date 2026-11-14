import type { Polled } from "../hooks/usePolling";
import type { Status } from "../api/types.gen";

/** What the banner slot should say, or null when everything is fine. */
export function bannerMessage(polled: Polled<Status>): string | null {
  if (polled.data === null) {
    return polled.error ? "Cannot reach the monitor. Retrying." : "Waiting for the monitor.";
  }
  if (polled.stale) {
    const seconds = Math.round((Date.now() - (polled.lastSuccessAt ?? Date.now())) / 1000);
    return `Showing the last data, ${seconds} s old. The monitor has not answered.`;
  }
  const offline = polled.data.nodes.filter((n) => n.state === "offline").map((n) => n.id);
  if (offline.length > 0) return `Offline: ${offline.join(", ")}. Data may have gaps.`;
  return null;
}

/** How serious the banner is: info while waiting, warning when unreachable, caution otherwise. */
export type BannerTone = "info" | "warning" | "caution";

export function bannerTone(polled: Polled<Status>): BannerTone {
  if (polled.data === null) return polled.error ? "warning" : "info";
  return "caution";
}

const TONE: Record<BannerTone, string> = {
  info: "border-accent bg-panel-2",
  warning: "border-level-red-mark bg-level-red-bg",
  caution: "border-level-yellow-mark bg-level-yellow-bg",
};

function ToneGlyph({ tone }: { tone: BannerTone }) {
  if (tone === "warning")
    return (
      <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
        <path d="M2.5 2.5 L9.5 9.5 M9.5 2.5 L2.5 9.5" stroke="var(--red-mark)" strokeWidth="2.2" />
      </svg>
    );
  return (
    <svg width="12" height="12" viewBox="0 0 12 12" aria-hidden="true">
      <circle
        cx="6"
        cy="6"
        r="4.2"
        fill="none"
        stroke={tone === "info" ? "var(--accent)" : "var(--yellow-mark)"}
        strokeWidth="2"
        strokeDasharray={tone === "info" ? "2.5 2" : undefined}
      />
    </svg>
  );
}

export const BANNER_HEIGHT = "1.75rem";

/**
 * The reserved banner slot. It has the same height with or without a message, so a stale or
 * offline state never shifts the page (the layout never jumps).
 */
export function Banner({ polled }: { polled: Polled<Status> }) {
  const message = bannerMessage(polled);
  const tone = bannerTone(polled);
  return (
    <div
      data-testid="banner-slot"
      style={{ height: BANNER_HEIGHT }}
      className="flex items-center overflow-hidden"
    >
      {message ? (
        <p
          role="status"
          className={`flex h-full w-full min-w-0 items-center gap-2 border px-2.5 text-xs font-semibold text-ink ${TONE[tone]}`}
        >
          <ToneGlyph tone={tone} />
          <span className="truncate">{message}</span>
        </p>
      ) : null}
    </div>
  );
}

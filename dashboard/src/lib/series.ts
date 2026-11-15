/** One history sample: the step's mean with the extremes seen inside the step. */
export interface Sample {
  t: number;
  mean: number;
  min: number;
  max: number;
}

/** Index of the entry closest in time to `t`, or -1 for an empty list. Entries are in time order. */
export function nearestIndex(entries: readonly { t: number }[], t: number): number {
  if (entries.length === 0) return -1;
  let lo = 0;
  let hi = entries.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    if (entries[mid].t < t) lo = mid + 1;
    else hi = mid;
  }
  if (lo > 0 && Math.abs(entries[lo - 1].t - t) <= Math.abs(entries[lo].t - t)) return lo - 1;
  return lo;
}

export interface WindowStats {
  count: number;
  /** The first and last means inside the window, and the difference between them. */
  start: number;
  end: number;
  change: number;
  mean: number;
  /** The lowest and highest reading inside the window, from each step's own extremes. */
  low: number;
  high: number;
  /** Time between the first and last sample inside the window. */
  seconds: number;
}

/** What happened between `from` and `to` (unix seconds, inclusive), or null if nothing did. */
export function windowStats(
  points: readonly Sample[],
  from: number,
  to: number,
): WindowStats | null {
  const inside = points.filter((p) => p.t >= from && p.t <= to);
  if (inside.length === 0) return null;
  const first = inside[0];
  const last = inside[inside.length - 1];
  let sum = 0;
  let low = Infinity;
  let high = -Infinity;
  for (const p of inside) {
    sum += p.mean;
    if (p.min < low) low = p.min;
    if (p.max > high) high = p.max;
  }
  return {
    count: inside.length,
    start: first.mean,
    end: last.mean,
    change: last.mean - first.mean,
    mean: sum / inside.length,
    low,
    high,
    seconds: last.t - first.t,
  };
}

/** A difference for display: "+1.5", "−0.3" or "0", to one decimal at most. */
export function signed(value: number): string {
  const rounded = Math.round(value * 10) / 10;
  if (rounded === 0) return "0";
  const size = Math.abs(rounded);
  const text = Number.isInteger(size) ? String(size) : size.toFixed(1);
  return `${rounded > 0 ? "+" : "−"}${text}`;
}

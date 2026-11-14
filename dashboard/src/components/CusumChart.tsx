import {
  CartesianGrid,
  ComposedChart,
  Line,
  ReferenceLine,
  ResponsiveContainer,
  XAxis,
  YAxis,
} from "recharts";
import type { History } from "../api/types.gen";
import { clockTime } from "../lib/format";

const TICK = { fontFamily: "var(--font-mono)", fontSize: 10, fill: "var(--muted)" };

type Cusum = NonNullable<History["cusum"]>;

/** The drift detector's two sums and its alarm line, as the API reports them. */
export function CusumChart({ cusum }: { cusum: Cusum }) {
  const h = cusum[0]?.h ?? 5;
  const tMin = cusum[0]?.t ?? 0;
  const tMax = cusum.at(-1)?.t ?? 1;
  return (
    <figure className="m-0">
      <figcaption className="mb-1 text-xs">
        <span className="font-semibold">Drift detector (CUSUM)</span>
        <span className="text-muted">
          {" "}
          · each line adds up how far readings stay above or below the baseline; crossing the alarm
          line means a sustained shift, not a single odd reading
        </span>
      </figcaption>
      <div
        role="img"
        aria-label="Drift detector sums for readings above and below the baseline, with the alarm line"
        data-testid="cusum-chart"
        className="h-48 w-full"
      >
        <ResponsiveContainer width="100%" height="100%">
          <ComposedChart margin={{ top: 6, right: 8, bottom: 0, left: 0 }}>
            <CartesianGrid stroke="var(--line)" strokeDasharray="3 3" />
            <XAxis
              dataKey="t"
              type="number"
              domain={[tMin, tMax]}
              tickFormatter={clockTime}
              stroke="var(--line-strong)"
              tick={TICK}
              allowDuplicatedCategory={false}
            />
            <YAxis width={36} stroke="var(--line-strong)" tick={TICK} domain={[0, "auto"]} />
            <ReferenceLine
              y={h}
              className="alarm-line"
              stroke="var(--red-mark)"
              strokeWidth={2}
              strokeDasharray="6 4"
              ifOverflow="extendDomain"
              label={{ value: "Alarm line", position: "insideTopRight", fill: "var(--ink)" }}
            />
            <Line
              data={cusum}
              dataKey="pos"
              name="Above baseline"
              className="cusum-pos"
              stroke="var(--series-co2)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
            <Line
              data={cusum}
              dataKey="neg"
              name="Below baseline"
              className="cusum-neg"
              stroke="var(--series-prv)"
              strokeWidth={2}
              dot={false}
              isAnimationActive={false}
            />
          </ComposedChart>
        </ResponsiveContainer>
      </div>
      <p className="mt-0.5 text-[0.6875rem] text-muted">
        <span className="font-medium text-ink">Orange:</span> above baseline.{" "}
        <span className="font-medium text-ink">Blue:</span> below baseline.
      </p>
    </figure>
  );
}

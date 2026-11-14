/**
 * A pixel-art Earth on a 28x28 grid, drawn inline so the product stays offline and ships no
 * image assets. Each cell is one crisp rectangle; decorative only.
 *
 * Legend: `.` space, `o` ocean, `l` land, `i` ice, `c` cloud. Shading (a sunlit rim at the
 * upper left and a night side at the lower right) is applied per cell from its position.
 */
const MAP = [
  "............................",
  "...........iiiiii...........",
  ".........iiooooooii.........",
  ".......ioooollloooooi.......",
  "......ioolllllllooooooi.....",
  ".....ioollllllllooooooo.....",
  "....iolllllllloooollooooo...",
  "....oolllllllooooolllooooo..",
  "...ooollllllooooolllllooooo.",
  "...oooolllloooooollllloooooo",
  "...ooooollooooooolllllooooo.",
  "...oooooooooooooooolllooooo.",
  "...ooooooollooooooooooooooo.",
  "...oooooollllooooooooooooo..",
  "...ooooollllllooooooolloooo.",
  "...ooooollllllooooooollooo..",
  "....oooollllloooooooooooo...",
  "....oooooolllooooooollooo...",
  ".....oooooolloooooolllooo...",
  ".....ooooooolooooooooooo....",
  "......oooooooooooooooooo....",
  ".......oooooooooooooooi.....",
  ".........ooooooooooii.......",
  "...........iiiiiii..........",
  "............................",
  "............................",
  "............................",
  "............................",
];

const CLOUDS: [number, number][] = [
  [7, 5],
  [8, 5],
  [9, 5],
  [15, 8],
  [16, 8],
  [17, 8],
  [18, 8],
  [6, 12],
  [7, 12],
  [14, 15],
  [15, 15],
  [16, 15],
  [9, 18],
  [10, 18],
  [11, 18],
  [12, 18],
  [19, 12],
  [20, 12],
];

const STARS: [number, number][] = [
  [1, 2],
  [25, 1],
  [27, 9],
  [0, 16],
  [3, 25],
  [24, 26],
  [12, 26],
  [26, 20],
];

const COLORS = {
  o: ["#5fc0ff", "#2a7fe0", "#153f8f", "#0a2152"],
  l: ["#a3ec8a", "#4fbf62", "#2b7d45", "#184a2d"],
  i: ["#ffffff", "#dceeff", "#a8c4e6", "#6f8fb5"],
  c: ["#ffffff", "#eef6ff", "#c6d9f2", "#8aa3c4"],
} as const;

const CX = 13.5;
const CY = 12.5;

/** 0 = sunlit rim (upper left), 1 = lit, 2 = dusk, 3 = night (lower right). */
function shade(x: number, y: number): 0 | 1 | 2 | 3 {
  const dx = (x + 0.5 - CX) / 11.5;
  const dy = (y + 0.5 - CY) / 11.5;
  const r = Math.hypot(dx, dy);
  const light = -(dx + dy) / Math.SQRT2; // +1 toward the upper left, -1 toward the lower right
  if (r > 0.86 && light > 0.35) return 0;
  if (light > 0.05) return 1;
  if (light > -0.45) return 2;
  return 3;
}

const CELLS: { x: number; y: number; fill: string }[] = [];
MAP.forEach((row, y) => {
  [...row].forEach((ch, x) => {
    if (ch === ".") return;
    const key = ch as keyof typeof COLORS;
    CELLS.push({ x, y, fill: COLORS[key][shade(x, y)] });
  });
});
for (const [x, y] of CLOUDS) {
  const s = shade(x, y);
  if (s < 3) CELLS.push({ x, y, fill: COLORS.c[s] });
}

export function EarthGlobe({ size = 150 }: { size?: number }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 28 28"
      aria-hidden="true"
      shapeRendering="crispEdges"
      style={{ display: "block" }}
    >
      {STARS.map(([x, y]) => (
        <rect key={`s${x}-${y}`} x={x} y={y} width="1" height="1" fill="#dbe9ff" opacity="0.8" />
      ))}
      {CELLS.map((c) => (
        <rect key={`${c.x}-${c.y}`} x={c.x} y={c.y} width="1" height="1" fill={c.fill} />
      ))}
    </svg>
  );
}

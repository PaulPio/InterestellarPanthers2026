import type { Level } from "../api/types.gen";
import { LEVEL_WORD } from "../lib/levels";

/** A level's shape: circle for Green, triangle for Yellow, square-cornered diamond for Red. */
export function LevelGlyph({ level, size = 9 }: { level: Level | "learning"; size?: number }) {
  const common = { width: size, height: size, viewBox: "0 0 12 12", "aria-hidden": true } as const;
  if (level === "green")
    return (
      <svg {...common}>
        <circle cx="6" cy="6" r="5" fill="var(--green-mark)" />
      </svg>
    );
  if (level === "yellow")
    return (
      <svg {...common}>
        <path d="M6 0.8 L11.6 11 L0.4 11 Z" fill="var(--yellow-mark)" />
      </svg>
    );
  if (level === "red")
    return (
      <svg {...common}>
        <rect
          x="2.1"
          y="2.1"
          width="7.8"
          height="7.8"
          transform="rotate(45 6 6)"
          fill="var(--red-mark)"
        />
      </svg>
    );
  return (
    <svg {...common}>
      <circle
        cx="6"
        cy="6"
        r="4.6"
        fill="none"
        stroke="var(--learn-mark)"
        strokeWidth="1.6"
        strokeDasharray="2.2 1.6"
      />
    </svg>
  );
}

const BADGE_BG: Record<Level, string> = {
  green: "border-level-green-mark bg-level-green-bg",
  yellow: "border-level-yellow-mark bg-level-yellow-bg",
  red: "border-level-red-mark bg-level-red-bg",
};

/**
 * A level as word, shape and color, so color is never the only cue. Size and layout are
 * identical for every level (D14): the badge has a fixed width, so a change of level never moves
 * anything.
 */
export function LevelBadge({ level, className = "" }: { level: Level; className?: string }) {
  return (
    <span
      data-level={level}
      className={`inline-flex h-5 w-[4.5rem] shrink-0 items-center gap-1 border px-1.5 text-[0.6875rem] font-semibold text-ink ${BADGE_BG[level]} ${className}`}
    >
      <LevelGlyph level={level} />
      {LEVEL_WORD[level]}
    </span>
  );
}

/** The learning state: not a level, so it never looks like Green (dashed ring, slate tone). */
export function LearningBadge({ className = "" }: { className?: string }) {
  return (
    <span
      data-level="learning"
      className={`inline-flex h-5 w-[4.5rem] shrink-0 items-center gap-1 border border-level-learn-mark bg-level-learn-bg px-1.5 text-[0.6875rem] font-semibold text-ink ${className}`}
    >
      <LevelGlyph level="learning" />
      Learning
    </span>
  );
}

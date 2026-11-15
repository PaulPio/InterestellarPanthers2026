import { describe, expect, it } from "vitest";
import { nearestIndex, signed, windowStats } from "../src/lib/series";

const points = [
  { t: 0, mean: 1, min: 0.5, max: 1.5 },
  { t: 60, mean: 2, min: 1.5, max: 2.5 },
  { t: 120, mean: 4, min: 3, max: 6 },
  { t: 180, mean: 3, min: 2, max: 3.5 },
];

describe("nearestIndex", () => {
  it("finds the entry closest in time, clamping at both ends", () => {
    expect(nearestIndex(points, -100)).toBe(0);
    expect(nearestIndex(points, 29)).toBe(0);
    expect(nearestIndex(points, 31)).toBe(1);
    expect(nearestIndex(points, 119)).toBe(2);
    expect(nearestIndex(points, 9999)).toBe(3);
    expect(nearestIndex([], 5)).toBe(-1);
  });
});

describe("windowStats", () => {
  it("describes only the samples inside the window", () => {
    expect(windowStats(points, 60, 180)).toEqual({
      count: 3,
      start: 2,
      end: 3,
      change: 1,
      mean: 3,
      low: 1.5,
      high: 6,
      seconds: 120,
    });
  });

  it("is null when the window holds no sample", () => {
    expect(windowStats(points, 61, 119)).toBeNull();
  });
});

describe("signed", () => {
  it("writes a difference with its sign, and zero without one", () => {
    expect(signed(1.5)).toBe("+1.5");
    expect(signed(-0.34)).toBe("−0.3");
    expect(signed(2)).toBe("+2");
    expect(signed(0.04)).toBe("0");
  });
});

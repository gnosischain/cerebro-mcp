import { describe, expect, it } from "vitest";
import { sparkPoints } from "../components/SparkLine";

function parsePoints(points: string): Array<[number, number]> {
  if (!points) return [];
  return points.split(" ").map((pair) => {
    const [x, y] = pair.split(",").map(Number);
    return [x, y];
  });
}

describe("sparkPoints", () => {
  it("returns empty for no data or all-invalid data", () => {
    expect(sparkPoints([], 120, 28)).toBe("");
    expect(sparkPoints([Number.NaN, Number.POSITIVE_INFINITY], 120, 28)).toBe("");
  });

  it("maps min to the bottom and max to the top with 1px padding", () => {
    const points = parsePoints(sparkPoints([0, 10], 100, 30));
    expect(points).toHaveLength(2);
    const [[x0, y0], [x1, y1]] = points;
    expect(x0).toBe(0);
    expect(x1).toBe(100);
    expect(y0).toBe(29); // min value -> height - pad
    expect(y1).toBe(1); // max value -> pad
  });

  it("renders a flat series as a midline, not NaN", () => {
    const points = parsePoints(sparkPoints([5, 5, 5], 100, 30));
    expect(points).toHaveLength(3);
    for (const [, y] of points) expect(y).toBe(15);
    expect(sparkPoints([5, 5, 5], 100, 30)).not.toContain("NaN");
  });

  it("renders a single point as a short visible midline segment", () => {
    const points = parsePoints(sparkPoints([7], 100, 30));
    expect(points).toHaveLength(2);
    expect(points[0][0]).toBeLessThan(points[1][0]);
    expect(points[0][1]).toBe(points[1][1]);
  });

  it("skips non-finite entries without shifting the survivors' x positions", () => {
    const points = parsePoints(sparkPoints([1, Number.NaN, 3], 100, 30));
    expect(points).toHaveLength(2);
    expect(points[0][0]).toBe(0);
    expect(points[1][0]).toBe(100);
  });
});

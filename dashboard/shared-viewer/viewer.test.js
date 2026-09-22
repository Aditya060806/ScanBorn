import { describe, it, expect } from "vitest";
import { mapToRender, renderToMap } from "./coordinates.js";
import { decodePoints, traceLine } from "./geometry.js";

describe("shared scene coordinates", () => {
  it("uses a right handed transform with a nonzero floor", () => {
    expect(mapToRender([2, 3, 6], 5)).toEqual([2, 1, -3]);
    expect(renderToMap([2, 1, -3], 5)).toEqual([2, 3, 6]);
  });
  it("decodes packed RGB into linear vertex colors", () => {
    const buffer = new ArrayBuffer(16), data = new DataView(buffer);
    [2, 3, 6].forEach((v, i) => data.setFloat32(i * 4, v, true));
    [255, 128, 0].forEach((v, i) => data.setUint8(12 + i, v));
    const decoded = decodePoints(buffer, 5);
    expect([...decoded.positions]).toEqual([2, 1, -3]);
    expect(decoded.colors[1]).toBeCloseTo(0.21586, 4);
    expect(() => decodePoints(new ArrayBuffer(3))).toThrow();
  });
  it("keeps one bounded trace allocation across updates", () => {
    const line = traceLine(0xffffff), attribute = line.geometry.getAttribute("position");
    line.update([[1, 2], [3, 4]]);
    line.update(Array.from({ length: 25000 }, (_, i) => [i, i]));
    expect(line.geometry.getAttribute("position")).toBe(attribute);
    expect(line.geometry.drawRange.count).toBe(20000);
    expect(attribute.getX(0)).toBe(5000);
    line.geometry.dispose(); line.material.dispose();
  });
});

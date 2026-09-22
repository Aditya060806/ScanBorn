import { describe, expect, it } from "vitest";

import {
  PALETTE,
  STATE,
  cellOf,
  decodeCells,
  defaultStart,
  gridPixels,
  headingFromDrag,
  mapFrame,
  nearestValid,
  refusal,
  stateAt,
} from "./navmesh.js";

// A 3 x 2 grid with 0.5 m cells from (-1, 2): row 0 (minimum y) is free, blocked, unknown;
// row 1 is all free.
const NAV = { origin: [-1, 2], cell: 0.5, width: 3, height: 2 };
const CELLS = Uint8Array.from([0, 1, 2, 0, 0, 0]);
const BAND = Uint8Array.from([0, 0, 0, 1, 0, 0]);
const b64 = (bytes) => btoa(String.fromCharCode(...bytes));

describe("reading the grid the orchestrator serves", () => {
  it("decodes base64 cells", () => {
    expect([...decodeCells(b64(CELLS))]).toEqual([...CELLS]);
  });

  it("puts row 0 at the minimum y", () => {
    expect(stateAt(NAV, CELLS, -0.75, 2.25)).toBe(STATE.FREE);
    expect(stateAt(NAV, CELLS, -0.25, 2.25)).toBe(STATE.BLOCKED);
    expect(stateAt(NAV, CELLS, 0.25, 2.25)).toBe(STATE.UNKNOWN);
    expect(stateAt(NAV, CELLS, -0.25, 2.75)).toBe(STATE.FREE);
  });

  it("treats everything off the grid as blocked", () => {
    expect(cellOf(NAV, -1.01, 2.0)).toEqual({ row: 0, col: -1 });
    expect(stateAt(NAV, CELLS, -1.01, 2.25)).toBe(STATE.BLOCKED);
    expect(stateAt(NAV, CELLS, 0, 3.01)).toBe(STATE.BLOCKED);
  });
});

describe("refusing a point before asking the orchestrator", () => {
  it("gives the planner's reason", () => {
    expect(refusal(NAV, CELLS, BAND, -0.25, 2.25)).toBe("on an obstacle");
    expect(refusal(NAV, CELLS, BAND, 0.25, 2.25)).toBe("on floor the scan never saw");
    expect(refusal(NAV, CELLS, BAND, -0.75, 2.75)).toMatch(/closer/);
    expect(refusal(NAV, CELLS, BAND, 5, 5)).toBe("outside the scanned floor");
    expect(refusal(NAV, CELLS, BAND, -0.25, 2.75)).toBeNull();
  });

  it("offers the nearest point the robot fits", () => {
    // (-0.3, 2.9) is nearest the centre of row 1, column 1; row 1, column 0 is in the band.
    expect(nearestValid(NAV, CELLS, BAND, -0.3, 2.9)).toEqual([-0.25, 2.75]);
    expect(nearestValid(NAV, CELLS, BAND, -0.9, 2.9)).toEqual([-0.75, 2.25]); // a tie: row 0 first
    expect(nearestValid(NAV, Uint8Array.from([1, 1, 1, 2, 2, 2]), null, 0, 0)).toBeNull();
  });

  it("starts at the home pose, or else in the middle of the room", () => {
    const served = { ...NAV, cells_b64: b64(CELLS), band_b64: b64(BAND), home_pose: null };
    expect(defaultStart(served)).toEqual([-0.25, 2.75]);
    expect(defaultStart({ ...served, home_pose: [1, 2, 0.5] })).toEqual([1, 2, 0.5]);
    expect(defaultStart({ ...served, width: 0 })).toBeNull();
  });
});

describe("drawing the map", () => {
  it("writes the top screen row first and colours each state", () => {
    const pixels = gridPixels(NAV, CELLS, BAND);
    const at = (screenRow, col) => [...pixels.slice((screenRow * 3 + col) * 4, (screenRow * 3 + col + 1) * 4)];
    expect(at(1, 1)).toEqual([...PALETTE.blocked]); // map row 0 is the bottom screen row
    expect(at(0, 0)).toEqual([...PALETTE.band]);
    expect(at(0, 1)).toEqual([...PALETTE.free]);
    expect([[...PALETTE.unknown], [...PALETTE.unknownAlt]]).toContainEqual(at(1, 2));
  });

  it("projects map metres to SVG units and back", () => {
    const frame = mapFrame([], NAV, 0);
    expect(frame.place(-1, 3)).toEqual([0, 0]);
    const [x, y] = frame.unplace(...frame.place(0.3, 2.4));
    expect(x).toBeCloseTo(0.3);
    expect(y).toBeCloseTo(2.4);
    expect(mapFrame([], null)).toBeNull();
  });

  it("reads a drag as a heading and a tiny one as a click", () => {
    expect(headingFromDrag([0, 0], [0, 1])).toBeCloseTo(Math.PI / 2);
    expect(headingFromDrag([0, 0], [0.01, 0])).toBeNull();
  });
});

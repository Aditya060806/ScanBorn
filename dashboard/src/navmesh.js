/**
 * The twin's occupancy grid as GET /twins/{id}/navmesh serves it, and the plan-view projection.
 *
 * Cells are uint8, base64, row-major, with row 0 at the minimum y. The map frame's +y points up
 * the screen, so rows are flipped exactly twice — in gridPixels and in mapFrame — and nowhere
 * else. States and refusal reasons mirror twin/navmesh.py and policy/planning.py, so a click
 * on a wall is refused on the spot with the same words the orchestrator would use.
 */

export const STATE = Object.freeze({ FREE: 0, BLOCKED: 1, UNKNOWN: 2 });

export const PALETTE = Object.freeze({
  free: Object.freeze([248, 249, 245, 255]),
  band: Object.freeze([255, 221, 204, 255]),
  blocked: Object.freeze([34, 48, 126, 255]),
  unknown: Object.freeze([206, 211, 202, 255]),
  unknownAlt: Object.freeze([232, 235, 228, 255]),
});

const MIN_DRAG = 0.05; // m: shorter than this is a click, not a drag

export function decodeCells(b64) {
  const binary = atob(b64);
  const cells = new Uint8Array(binary.length);
  for (let i = 0; i < binary.length; i += 1) cells[i] = binary.charCodeAt(i);
  return cells;
}

const inside = (nav, row, col) => row >= 0 && col >= 0 && row < nav.height && col < nav.width;

/** { row, col } holding map point (x, y). May be outside the grid. */
export function cellOf(nav, x, y) {
  return {
    row: Math.floor((y - nav.origin[1]) / nav.cell),
    col: Math.floor((x - nav.origin[0]) / nav.cell),
  };
}

export const centreOf = (nav, row, col) => [
  nav.origin[0] + (col + 0.5) * nav.cell,
  nav.origin[1] + (row + 0.5) * nav.cell,
];

/** State at (x, y). Off the grid counts as blocked, as it does for the planner. */
export function stateAt(nav, cells, x, y) {
  const { row, col } = cellOf(nav, x, y);
  return inside(nav, row, col) ? cells[row * nav.width + col] : STATE.BLOCKED;
}

/** Why (x, y) cannot be a start, via point or goal — or null when it can. */
export function refusal(nav, cells, band, x, y) {
  const { row, col } = cellOf(nav, x, y);
  if (!inside(nav, row, col)) return "outside the scanned floor";
  const i = row * nav.width + col;
  if (cells[i] === STATE.BLOCKED) return "on an obstacle";
  if (cells[i] === STATE.UNKNOWN) return "on floor the scan never saw";
  if (band?.[i]) return "closer to an obstacle than the robot fits";
  return null;
}

/** Centre of the cell nearest (x, y) where the robot fits, or null when there is none. */
export function nearestValid(nav, cells, band, x, y) {
  let best = null;
  let bestD2 = Infinity;
  for (let row = 0; row < nav.height; row += 1) {
    for (let col = 0; col < nav.width; col += 1) {
      const i = row * nav.width + col;
      if (cells[i] !== STATE.FREE || band?.[i]) continue;
      const [cx, cy] = centreOf(nav, row, col);
      const d2 = (cx - x) ** 2 + (cy - y) ** 2;
      if (d2 < bestD2) {
        best = [cx, cy];
        bestD2 = d2;
      }
    }
  }
  return best;
}

const round3 = (v) => Math.round(v * 1000) / 1000;

/**
 * Where a new mission starts: the scan's home pose when it has one, otherwise the spot nearest
 * the middle of the room where the robot fits — a suggestion the operator drags into place.
 */
export function defaultStart(nav) {
  if (!nav?.width || !nav?.height) return null;
  if (nav.home_pose) return [...nav.home_pose];
  const point = nearestValid(
    nav, decodeCells(nav.cells_b64), decodeCells(nav.band_b64),
    nav.origin[0] + (nav.width * nav.cell) / 2,
    nav.origin[1] + (nav.height * nav.cell) / 2,
  );
  return point && point.map(round3);
}

/**
 * RGBA pixels, one per cell, top screen row (maximum y) first, ready for ImageData. Unknown
 * cells are hatched so they never read as free at a glance.
 */
export function gridPixels(nav, cells, band, palette = PALETTE) {
  const { width, height } = nav;
  const out = new Uint8ClampedArray(width * height * 4);
  for (let row = 0; row < height; row += 1) {
    const screenRow = height - 1 - row;
    for (let col = 0; col < width; col += 1) {
      const i = row * width + col;
      let colour = palette.free;
      if (cells[i] === STATE.BLOCKED) colour = palette.blocked;
      else if (cells[i] === STATE.UNKNOWN) colour = (row + col) % 4 < 2 ? palette.unknown : palette.unknownAlt;
      else if (band?.[i]) colour = palette.band;
      out.set(colour, (screenRow * width + col) * 4);
    }
  }
  return out;
}

/**
 * The plan view's frame: every object and the whole navmesh, plus a margin, in metres.
 * `place` maps map (x, y) to SVG units (y grows down the screen); `unplace` inverts it.
 */
export function mapFrame(objects, nav, pad = 0.5) {
  const xs = [];
  const ys = [];
  for (const { bbox3d } of objects) {
    xs.push(bbox3d[0], bbox3d[3]);
    ys.push(bbox3d[1], bbox3d[4]);
  }
  if (nav?.width && nav?.height) {
    xs.push(nav.origin[0], nav.origin[0] + nav.width * nav.cell);
    ys.push(nav.origin[1], nav.origin[1] + nav.height * nav.cell);
  }
  if (!xs.length) return null;
  const minX = Math.min(...xs) - pad;
  const maxX = Math.max(...xs) + pad;
  const minY = Math.min(...ys) - pad;
  const maxY = Math.max(...ys) + pad;
  return {
    minX, maxX, minY, maxY,
    width: maxX - minX,
    height: maxY - minY,
    place: (x, y) => [x - minX, maxY - y],
    unplace: (u, v) => [u + minX, maxY - v],
  };
}

/** Map heading (radians, counter-clockwise from +x) of a drag, or null for a click. */
export function headingFromDrag(from, to) {
  const dx = to[0] - from[0];
  const dy = to[1] - from[1];
  return Math.hypot(dx, dy) < MIN_DRAG ? null : Math.atan2(dy, dx);
}

import React, { useMemo, useRef, useState } from "react";

import {
  PALETTE,
  decodeCells,
  gridPixels,
  headingFromDrag,
  mapFrame,
  refusal,
} from "./navmesh.js";

// Structure, not furniture. Mirrors twin/navmesh.py's WALKABLE_LABELS.
const SUBSTRATE = new Set(["floor", "ceiling"]);

const rgb = ([r, g, b]) => `rgb(${r} ${g} ${b})`;
const round3 = (v) => Math.round(v * 1000) / 1000;
const pointsAttr = (points, place) => points.map(([x, y]) => place(x, y).join(",")).join(" ");
const sameSpot = (a, b) => Boolean(a && b) && Math.hypot(a[0] - b[0], a[1] - b[1]) < 1e-6;

/** The occupancy grid as a PNG data URL, one pixel per cell. */
function useGridImage(navmesh, decoded) {
  return useMemo(() => {
    if (!navmesh?.width || !navmesh?.height || !decoded) return null;
    const canvas = document.createElement("canvas");
    canvas.width = navmesh.width;
    canvas.height = navmesh.height;
    const context = canvas.getContext("2d");
    if (!context) return null;
    const pixels = gridPixels(navmesh, decoded.cells, decoded.band);
    context.putImageData(new ImageData(pixels, navmesh.width, navmesh.height), 0, 0);
    return canvas.toDataURL("image/png");
  }, [navmesh, decoded]);
}

/**
 * Plan view of the twin for choosing a mission: the navmesh (free, too close for the robot,
 * blocked, never scanned), the scanned objects, the planned route, and the traces driven.
 *
 * Click sets the goal. Drag the start marker to move it; drag its arrow to aim it. Alt-click
 * adds a via point; click a via point to remove it. A point the robot cannot occupy is refused
 * here, before any request, with the reason the planner would give. Every change is reported
 * through onChange(draft) once, on release — never on mouse move — so the console plans on
 * discrete events.
 *
 * Coordinates are map metres (Z-up frame, x/y top-down). Screen y grows downward, so every
 * point goes through `place()` rather than flipping the SVG, which would mirror the labels.
 */
export default function MissionMap({
  objects = [], navmesh = null, draft = null, mission = null,
  simTrace = [], realTrace = [], roverPose = null, realLabel = "driven by the rover",
  disabled = false, onChange,
}) {
  const svgRef = useRef(null);
  const [note, setNote] = useState(null);
  const [drag, setDrag] = useState(null); // { mode: "move" | "aim", from, to }

  const decoded = useMemo(() => (navmesh?.cells_b64
    ? { cells: decodeCells(navmesh.cells_b64), band: decodeCells(navmesh.band_b64) }
    : null), [navmesh]);
  const frame = useMemo(() => mapFrame(objects, navmesh), [objects, navmesh]);
  const image = useGridImage(navmesh, decoded);

  if (!frame) {
    return (
      <div className="plan empty-plan">
        <p>No map yet.</p>
        <p className="hint">Run stages 03 and 04 — the twin's navmesh is what missions are planned on.</p>
      </div>
    );
  }

  const { place, unplace, width, height, minX, maxY } = frame;
  const unit = Math.max(width, height) / 100; // hairlines stay hairlines at any room size
  const radius = navmesh?.robot_radius ?? 0;

  // A live console passes the operator's draft; a replayed fixture has only the mission.
  const start = draft ? draft.start : mission?.start ?? null;
  const goal = draft ? draft.goal : mission?.goal ?? null;
  const via = draft ? draft.via : mission?.via ?? [];
  const planned = mission && start && sameSpot(mission.start, start) ? mission : null;
  const heading = start?.[2] ?? planned?.start?.[2] ?? null;

  const toMap = (event) => {
    const matrix = svgRef.current?.getScreenCTM();
    if (!matrix) return null;
    const point = new DOMPoint(event.clientX, event.clientY).matrixTransform(matrix.inverse());
    return unplace(point.x, point.y).map(round3);
  };

  const refuse = (role, [x, y]) => {
    if (!decoded) return `Generate the twin first: the ${role} is checked against its navmesh.`;
    const why = refusal(navmesh, decoded.cells, decoded.band, x, y);
    return why && `That ${role} is ${why}.`;
  };

  const current = { start: draft?.start ?? null, goal: draft?.goal ?? null, via: draft?.via ?? [] };
  const commit = (next) => {
    setNote(null);
    onChange?.(next);
  };

  const beginDrag = (mode) => (event) => {
    if (disabled || !start) return;
    event.stopPropagation();
    event.preventDefault();
    svgRef.current?.setPointerCapture?.(event.pointerId);
    setDrag({ mode, from: [start[0], start[1]], to: [start[0], start[1]] });
  };

  const onPointerMove = (event) => {
    if (!drag) return;
    const point = toMap(event);
    if (point) setDrag({ ...drag, to: point });
  };

  const onPointerUp = (event) => {
    if (disabled) return;
    if (drag) {
      const done = drag;
      setDrag(null);
      const aim = headingFromDrag(done.from, done.to);
      if (aim === null) return; // pressed and released in place: nothing to change
      if (done.mode === "aim") {
        commit({ ...current, start: [done.from[0], done.from[1], round3(aim)] });
        return;
      }
      const why = refuse("start", done.to);
      if (why) {
        setNote(why);
        return;
      }
      commit({ ...current, start: start.length > 2 ? [...done.to, start[2]] : done.to });
      return;
    }
    if (event.button !== 0) return;
    const point = toMap(event);
    if (!point) return;
    const role = event.altKey ? "via point" : "goal";
    const why = refuse(role, point);
    if (why) {
      setNote(why);
      return;
    }
    commit(event.altKey ? { ...current, via: [...current.via, point] } : { ...current, goal: point });
  };

  const removeVia = (index) => (event) => {
    event.stopPropagation();
    if (!disabled && draft) commit({ ...current, via: current.via.filter((_, i) => i !== index) });
  };

  const gridlines = [];
  for (let x = Math.ceil(minX); x < frame.maxX; x += 1) gridlines.push(["v", x - minX]);
  for (let y = Math.ceil(frame.minY); y < maxY; y += 1) gridlines.push(["h", maxY - y]);

  const shownStart = drag?.mode === "move" ? drag.to : start;
  const shownHeading = drag?.mode === "aim" ? headingFromDrag(drag.from, drag.to) ?? heading : heading;
  const aimLength = Math.max(radius * 2.5, unit * 7);
  const handle = shownStart && [
    shownStart[0] + aimLength * Math.cos(shownHeading ?? 0),
    shownStart[1] + aimLength * Math.sin(shownHeading ?? 0),
  ];
  const route = mission?.waypoints ?? [];

  return (
    <div className="mission-map">
      <svg
        ref={svgRef}
        className={`plan mission-map__svg${navmesh ? " has-grid" : ""}${disabled ? " is-disabled" : ""}`}
        viewBox={`0 0 ${width} ${height}`}
        role="application"
        aria-label="Mission map. Click to set the goal, drag the start marker to move it or its arrow to aim it, Alt-click to add a via point."
        onPointerMove={onPointerMove}
        onPointerUp={onPointerUp}
        onPointerCancel={() => setDrag(null)}
      >
        {image && (
          <image
            className="mission-map__grid"
            href={image}
            x={navmesh.origin[0] - minX}
            y={maxY - (navmesh.origin[1] + navmesh.height * navmesh.cell)}
            width={navmesh.width * navmesh.cell}
            height={navmesh.height * navmesh.cell}
            preserveAspectRatio="none"
          />
        )}

        <g strokeWidth={unit / 4}>
          {gridlines.map(([axis, at], i) => (
            <line
              key={i}
              className="gridline"
              x1={axis === "v" ? at : 0}
              x2={axis === "v" ? at : width}
              y1={axis === "v" ? 0 : at}
              y2={axis === "v" ? height : at}
            />
          ))}
        </g>

        {objects.map((obj) => {
          const [x, y] = place(obj.bbox3d[0], obj.bbox3d[4]);
          const substrate = SUBSTRATE.has(obj.label);
          return (
            <g key={obj.id} className={substrate ? "substrate" : "prop"}>
              <rect x={x} y={y} width={obj.bbox3d[3] - obj.bbox3d[0]}
                    height={obj.bbox3d[4] - obj.bbox3d[1]} strokeWidth={unit / 2} />
              {!substrate && <text x={x} y={y - unit * 1.5} fontSize={unit * 2.6}>{obj.label}</text>}
            </g>
          );
        })}

        {route.length > 1 && (
          <>
            <polyline className="mission-path" points={pointsAttr(route, place)} strokeWidth={unit * 0.9} />
            {route.slice(1, -1).map(([x, y], i) => {
              const [cx, cy] = place(x, y);
              return <circle key={i} className="mission-waypoint" cx={cx} cy={cy} r={unit * 0.9} />;
            })}
          </>
        )}
        {simTrace.length > 1 && (
          <polyline className="trace trace--sim" points={pointsAttr(simTrace, place)} strokeWidth={unit * 0.5} />
        )}
        {realTrace.length > 1 && (
          <polyline className="trace trace--real" points={pointsAttr(realTrace, place)} strokeWidth={unit * 0.6} />
        )}

        {via.map(([x, y], i) => {
          const [cx, cy] = place(x, y);
          return (
            <g key={`${x},${y},${i}`} className="mission-via" onPointerUp={removeVia(i)}>
              <title>Via point {i + 1} — click to remove</title>
              <circle cx={cx} cy={cy} r={unit * 2.2} strokeWidth={unit * 0.4} />
              <text x={cx} y={cy} fontSize={unit * 2.4} dy="0.35em" textAnchor="middle">{i + 1}</text>
            </g>
          );
        })}

        {goal && (() => {
          const [cx, cy] = place(goal[0], goal[1]);
          return (
            <g className="mission-goal">
              <title>Goal</title>
              <circle cx={cx} cy={cy} r={unit * 2.4} strokeWidth={unit * 0.6} />
              <circle className="mission-goal__dot" cx={cx} cy={cy} r={unit * 0.8} />
            </g>
          );
        })()}

        {shownStart && (() => {
          const [cx, cy] = place(shownStart[0], shownStart[1]);
          const [hx, hy] = place(handle[0], handle[1]);
          return (
            <g className={`mission-start-group${drag ? " is-dragging" : ""}`}>
              {radius > 0 && (
                <circle className="mission-footprint" cx={cx} cy={cy} r={radius}
                        strokeWidth={unit * 0.35} strokeDasharray={`${unit} ${unit * 0.6}`} />
              )}
              <line className={`mission-heading${shownHeading == null ? " is-unset" : ""}`}
                    x1={cx} y1={cy} x2={hx} y2={hy} strokeWidth={unit * 0.6} />
              <circle className="mission-start" cx={cx} cy={cy} r={unit * 1.9}
                      onPointerDown={beginDrag("move")}>
                <title>Start — drag to move</title>
              </circle>
              <circle className="mission-aim" cx={hx} cy={hy} r={unit * 1.3}
                      onPointerDown={beginDrag("aim")}>
                <title>Heading — drag to aim{shownHeading == null ? " (set by the route until aimed)" : ""}</title>
              </circle>
            </g>
          );
        })()}

        {roverPose && (() => {
          // Where the rover reports it is: its own estimate, drawn over everything else and
          // transparent to clicks so the map stays usable during a drive.
          const [cx, cy] = place(roverPose[0], roverPose[1]);
          const reach = Math.max(radius * 1.6, unit * 5);
          const [nx, ny] = place(roverPose[0] + reach * Math.cos(roverPose[2]),
            roverPose[1] + reach * Math.sin(roverPose[2]));
          return (
            <g className="mission-rover" pointerEvents="none">
              <title>Rover, at its reported position</title>
              {radius > 0 && (
                <circle className="mission-rover__footprint" cx={cx} cy={cy} r={radius} strokeWidth={unit * 0.4} />
              )}
              <line className="mission-rover__heading" x1={cx} y1={cy} x2={nx} y2={ny} strokeWidth={unit * 0.8} />
              <circle className="mission-rover__body" cx={cx} cy={cy} r={unit * 1.6} />
            </g>
          );
        })()}
      </svg>

      <ul className="mission-legend">
        <li><i style={{ background: rgb(PALETTE.free) }} />free</li>
        <li><i style={{ background: rgb(PALETTE.band) }} />too close for the robot</li>
        <li><i style={{ background: rgb(PALETTE.blocked) }} />blocked</li>
        <li><i style={{ background: rgb(PALETTE.unknown) }} />never scanned</li>
        <li><b className="swatch-line swatch-line--path" />planned route</li>
        <li><b className="swatch-line swatch-line--sim" />driven in simulation</li>
        {realTrace.length > 1 && <li><b className="swatch-line swatch-line--real" />{realLabel}</li>}
      </ul>

      {mission?.status === "stale" && (
        <p className="mission-note" role="status">
          This mission is stale: {mission.stale_reason}. Generate a twin from the new scan and plan again.
        </p>
      )}
      {note ? (
        <p className="mission-note" role="status">{note}</p>
      ) : (
        <p className="hint">
          {navmesh
            ? "Click to set the goal. Drag the start to move it, or its arrow to aim it. Alt-click adds a via point; click one to remove it."
            : "Generate the twin to see where the robot fits."}
        </p>
      )}
    </div>
  );
}

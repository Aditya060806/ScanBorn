/** One-metre sides, counterclockwise, aligned with the starting heading. */
export function squareDraft(start) {
  if (!Array.isArray(start) || start.length < 2 || !start.every(Number.isFinite))
    throw new Error("Choose a finite start position first");
  const [x, y, heading = 0] = start, c = Math.cos(heading), s = Math.sin(heading);
  const at = (a, b) => [x + a*c - b*s, y + a*s + b*c];
  return { start: [x, y, heading], via: [at(1,0), at(1,1), at(0,1)], goal: [x,y] };
}

export function squareReport(mission, trace = [], samples = []) {
  if (!mission?.start || mission.via?.length !== 3 || !mission.goal) return null;
  const corners = [mission.start, ...mission.via, mission.goal];
  if (!corners.every(p => Array.isArray(p) && p.length >= 2 && p.slice(0,2).every(Number.isFinite))) return null;
  const edges = corners.slice(1).map((p,i) => [p[0]-corners[i][0],p[1]-corners[i][1]]);
  if (Math.hypot(mission.goal[0]-mission.start[0],mission.goal[1]-mission.start[1]) > .001
    || edges.some(e=>Math.abs(Math.hypot(...e)-1)>.001)
    || edges.some((e,i)=>Math.abs(e[0]*edges[(i+1)%4][0]+e[1]*edges[(i+1)%4][1])>.001)) return null;
  const points = trace.filter(p => Array.isArray(p) && p.length >= 2 && p.slice(0,2).every(Number.isFinite));
  if (points.length < 2) return null;
  const last = points.at(-1), dx = last[0]-mission.start[0], dy = last[1]-mission.start[1];
  const heading = samples.at(-1)?.th, startHeading = mission.start[2] ?? Math.atan2(edges[0][1],edges[0][0]);
  return { dx, dy, closure_m: Math.hypot(dx,dy),
    reported_travel_m: points.slice(1).reduce((sum,p,i)=>sum+Math.hypot(p[0]-points[i][0],p[1]-points[i][1]),0),
    final_heading_change_deg: Number.isFinite(heading) ? Math.atan2(Math.sin(heading-startHeading),Math.cos(heading-startHeading))*180/Math.PI : null,
    planned_length_m: mission.length_m, detoured: Math.abs(mission.length_m-4)>.02 };
}

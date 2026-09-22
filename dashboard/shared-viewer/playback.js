/** Timestamped poses use seconds; legacy XY traces are explicitly four samples/second. */
export function timeline(trace = [], samples = []) {
  const timed = samples.length > 0 && samples.every((p, i) =>
    [p.t, p.x, p.y, p.th].every(Number.isFinite) && (i === 0 || p.t > samples[i - 1].t));
  const poses = timed ? samples.map(p => ({ ...p, t: p.t - samples[0].t })) :
    trace.filter(p => Number.isFinite(p[0]) && Number.isFinite(p[1])).map((p, i) => ({
      t: i / 4, x: p[0], y: p[1], th: Number.isFinite(p[2]) ? p[2] : null,
    }));
  return { timed, poses, duration: poses.at(-1)?.t ?? 0 };
}

export function poseAt(track, time) {
  const poses = track.poses;
  if (!poses.length) return null;
  let low = 0, high = poses.length - 1;
  while (low < high) {
    const mid = Math.ceil((low + high) / 2);
    if (poses[mid].t <= time) low = mid; else high = mid - 1;
  }
  const a = poses[low], b = poses[Math.min(low + 1, poses.length - 1)];
  const fraction = a === b ? 0 : Math.max(0, Math.min(1, (time - a.t) / (b.t - a.t)));
  const heading = a.th ?? Math.atan2(b.y - a.y, b.x - a.x);
  const turn = b.th == null ? 0 : Math.atan2(Math.sin(b.th - heading), Math.cos(b.th - heading));
  return { x: a.x + (b.x - a.x) * fraction, y: a.y + (b.y - a.y) * fraction,
    th: heading + turn * fraction };
}

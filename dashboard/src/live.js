/**
 * A rover drive, live: the WebSocket client for WS /ws/deployments/{id} and the fold that turns
 * its messages into what the console draws.
 *
 * The orchestrator sends a snapshot on connect (the trace so far), one message per telemetry frame,
 * then `finished` (orchestrator/service.py:ws_deployment). A socket that drops before the drive has
 * finished reconnects, and the next snapshot restores the trace, so a network hiccup loses nothing
 * but the frames in between. Nothing here commands the rover: E-STOP and Stop go over HTTP
 * (api.js), which does not depend on this socket.
 */

export const EMPTY_LIVE = Object.freeze({
  deploymentId: null,
  state: "starting",
  reason: null,
  trace: [],
  samples: [],
  pose: null, // [x, y, heading]: where the rover believes it is
  truth: null, // the fake rover's true pose; a real rover has none
  finished: false,
  fake: false,
  frames: 0,
  connected: false,
});

const MAX_POINTS = 20000; // orchestrator/deployments.py:MAX_TRACE_POINTS

const finite = (value) => typeof value === "number" && Number.isFinite(value);
const validPoint = (point) => Array.isArray(point) && finite(point[0]) && finite(point[1]);
const poseOf = (frame) =>
  frame && finite(frame.x) && finite(frame.y) ? [frame.x, frame.y, finite(frame.th) ? frame.th : 0] : null;

/** One socket message folded into the live state. Pure: tested without a socket. */
export function foldMessage(live, message) {
  if (!message || typeof message !== "object") return live;
  switch (message.type) {
    case "snapshot":
    case "finished": {
      const last = message.last ?? null;
      return {
        ...live,
        state: message.state ?? live.state,
        reason: message.reason ?? null,
        trace: Array.isArray(message.trace) ? message.trace.filter(validPoint) : live.trace,
        samples: Array.isArray(message.pose_samples) ? message.pose_samples : live.samples,
        pose: poseOf(last) ?? live.pose,
        truth: Array.isArray(last?.truth) ? last.truth : live.truth,
        finished: message.type === "finished" || Boolean(message.finished),
        fake: live.fake || Boolean(message.fake),
        frames: finite(message.frames) ? message.frames : live.frames,
      };
    }
    case "telemetry": {
      const pose = poseOf(message);
      if (!pose) return live;
      const fake = Array.isArray(message.truth);
      return {
        ...live,
        state: message.state ?? live.state,
        reason: message.reason ?? null,
        trace: live.trace.length < MAX_POINTS ? [...live.trace, [pose[0], pose[1]]] : live.trace,
        samples: finite(message.t) && live.samples.length < MAX_POINTS
          ? [...live.samples, { t: message.t, x: pose[0], y: pose[1], th: pose[2] }] : live.samples,
        pose,
        truth: fake ? message.truth : live.truth,
        fake: live.fake || fake,
        frames: live.frames + 1,
      };
    }
    case "missing":
      return { ...live, state: "missing", reason: "the orchestrator has no such deployment", finished: true };
    default:
      return live;
  }
}

/**
 * Whether a keydown is the E-STOP key, Space. `code` alone is not enough: synthesized and
 * assistive-technology key events often carry only `key`, and an E-STOP key must never be ignored.
 */
export const isEstopKey = (event) =>
  event.code === "Space" || event.key === " " || event.key === "Spacebar";

const defaultBase = () => {
  const where = globalThis.location ?? { protocol: "http:", host: "localhost:5173" };
  return `${where.protocol === "https:" ? "wss:" : "ws:"}//${where.host}`;
};

/**
 * Follow a deployment. `onChange(live)` receives the folded state: at once when the state, the
 * connection or `finished` changes, otherwise at most every `minIntervalMs` — telemetry arrives
 * twenty times a second and the console need not re-render that often. The socket reconnects until
 * the drive has finished or `close()` is called.
 */
export function watchDeployment(deploymentId, onChange, {
  WebSocketImpl = globalThis.WebSocket,
  base = defaultBase(),
  retryMs = 1000,
  minIntervalMs = 100,
  now = () => Date.now(),
} = {}) {
  let live = { ...EMPTY_LIVE, deploymentId };
  let socket = null;
  let retry = null;
  let flush = null;
  let lastEmit = -Infinity;
  let closed = false;

  const deliver = () => {
    flush = null;
    lastEmit = now();
    if (!closed) onChange(live);
  };

  const update = (next) => {
    const urgent = next.state !== live.state || next.finished !== live.finished
      || next.connected !== live.connected;
    live = next;
    if (closed) return;
    const wait = lastEmit + minIntervalMs - now();
    if (urgent || wait <= 0) {
      clearTimeout(flush);
      deliver();
    } else if (flush === null) {
      flush = setTimeout(deliver, wait);
    }
  };

  const connect = () => {
    if (closed) return;
    socket = new WebSocketImpl(`${base}/ws/deployments/${encodeURIComponent(deploymentId)}`);
    socket.onopen = () => update({ ...live, connected: true });
    socket.onmessage = (event) => {
      let message;
      try {
        message = JSON.parse(event.data);
      } catch {
        return;
      }
      update(foldMessage(live, message));
    };
    socket.onerror = () => {}; // onclose follows and decides whether to try again
    socket.onclose = () => {
      socket = null;
      if (closed) return;
      update({ ...live, connected: false });
      if (!live.finished) retry = setTimeout(connect, retryMs);
    };
  };

  connect();
  return {
    close() {
      closed = true;
      clearTimeout(retry);
      clearTimeout(flush);
      socket?.close();
    },
  };
}

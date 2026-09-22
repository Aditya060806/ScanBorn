import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { EMPTY_LIVE, foldMessage, isEstopKey, watchDeployment } from "./live.js";

describe("the E-STOP key", () => {
  it("is Space however the key event describes it, and nothing else", () => {
    expect(isEstopKey({ code: "Space", key: " " })).toBe(true);
    expect(isEstopKey({ key: " " })).toBe(true); // no `code`: a synthesized key press
    expect(isEstopKey({ key: "Spacebar" })).toBe(true);
    expect(isEstopKey({ code: "Enter", key: "Enter" })).toBe(false);
    expect(isEstopKey({ code: "KeyS", key: "s" })).toBe(false);
  });
});

const frame = (x, y, extra = {}) =>
  ({ type: "telemetry", session: "s1", x, y, th: 0.5, state: "running", reason: null, ...extra });

describe("folding a drive's messages", () => {
  it("starts from the snapshot, grows the trace frame by frame, and ends on finished", () => {
    let live = foldMessage(EMPTY_LIVE, {
      type: "snapshot", state: "running", reason: null, finished: false, fake: false, frames: 2,
      trace: [[0, 0], [0.1, 0], [null, null]], last: { x: 0.1, y: 0, th: 0 },
    });
    expect(live.trace).toEqual([[0, 0], [0.1, 0]]);
    expect(live.pose).toEqual([0.1, 0, 0]);

    live = foldMessage(live, frame(0.2, 0.05));
    expect(live.trace.at(-1)).toEqual([0.2, 0.05]);
    expect(live.pose).toEqual([0.2, 0.05, 0.5]);
    expect(live.frames).toBe(3);
    expect(live.fake).toBe(false);

    // Only the fake rover sends its true pose, and that is what marks the drive as fake.
    live = foldMessage(live, frame(0.3, 0.1, { truth: [0.31, 0.1, 0.5] }));
    expect(live.fake).toBe(true);
    expect(live.truth).toEqual([0.31, 0.1, 0.5]);

    live = foldMessage(live, {
      type: "finished", state: "done", reason: null, finished: true, fake: true, frames: 4,
      trace: live.trace, last: { x: 0.3, y: 0.1, th: 0.5 },
    });
    expect(live).toMatchObject({ finished: true, state: "done", fake: true, frames: 4 });
    expect(live.trace).toHaveLength(4);
  });

  it("keeps an E-STOP's reason and ignores what it cannot read", () => {
    const live = foldMessage(EMPTY_LIVE, frame(1, 1, { state: "estop", reason: "dashboard" }));
    expect(live).toMatchObject({ state: "estop", reason: "dashboard" });
    expect(foldMessage(live, { type: "telemetry", x: "?", y: 1 })).toBe(live);
    expect(foldMessage(live, null)).toBe(live);
    expect(foldMessage(live, { type: "missing" })).toMatchObject({ finished: true, state: "missing" });
  });
});

/** A WebSocket the test drives by hand. */
class FakeSocket {
  static opened = [];

  constructor(url) {
    this.url = url;
    FakeSocket.opened.push(this);
  }

  open() { this.onopen?.(); }

  deliver(message) { this.onmessage?.({ data: JSON.stringify(message) }); }

  close() { this.onclose?.(); }
}

describe("following a deployment", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    FakeSocket.opened = [];
  });
  afterEach(() => vi.useRealTimers());

  const follow = (onChange) => watchDeployment("dep/1", onChange, {
    WebSocketImpl: FakeSocket, base: "ws://test", retryMs: 1000, minIntervalMs: 100,
  });

  it("reconnects after a dropped socket until the drive has finished", () => {
    const seen = [];
    follow((live) => seen.push(live));
    const first = FakeSocket.opened[0];
    expect(first.url).toBe("ws://test/ws/deployments/dep%2F1");
    first.open();
    first.deliver({ type: "snapshot", state: "running", finished: false, trace: [[0, 0]],
      last: { x: 0, y: 0, th: 0 } });
    first.close(); // the network drops mid-drive
    expect(seen.at(-1).connected).toBe(false);

    vi.advanceTimersByTime(1000);
    expect(FakeSocket.opened).toHaveLength(2);
    const second = FakeSocket.opened[1];
    second.open();
    // The new snapshot carries the whole trace, so nothing driven before the drop is lost.
    second.deliver({ type: "finished", state: "done", finished: true, trace: [[0, 0], [0.5, 0], [1, 0]],
      last: { x: 1, y: 0, th: 0 } });
    second.close();

    vi.advanceTimersByTime(10000);
    expect(FakeSocket.opened).toHaveLength(2); // a finished drive is not followed again
    expect(seen.at(-1)).toMatchObject({ finished: true, state: "done" });
    expect(seen.at(-1).trace).toHaveLength(3);
  });

  it("delivers state changes at once and holds telemetry to the latest frame", () => {
    const seen = [];
    follow((live) => seen.push(live));
    const socket = FakeSocket.opened[0];
    socket.open();
    socket.deliver(frame(0.1, 0)); // starting -> running: delivered at once
    const delivered = seen.length;

    for (let i = 2; i <= 5; i += 1) socket.deliver(frame(i / 10, 0));
    expect(seen).toHaveLength(delivered);
    vi.advanceTimersByTime(100);
    expect(seen).toHaveLength(delivered + 1);
    expect(seen.at(-1).pose).toEqual([0.5, 0, 0.5]);
    expect(seen.at(-1).trace).toHaveLength(5);

    socket.deliver(frame(0.6, 0, { state: "estop", reason: "dashboard" }));
    expect(seen.at(-1)).toMatchObject({ state: "estop", reason: "dashboard" });
  });

  it("stops for good when closed", () => {
    const seen = [];
    const watcher = follow((live) => seen.push(live));
    FakeSocket.opened[0].open();
    const count = seen.length;
    watcher.close();
    vi.advanceTimersByTime(10000);
    expect(FakeSocket.opened).toHaveLength(1);
    expect(seen).toHaveLength(count);
  });
});

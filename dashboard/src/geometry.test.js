import { expect, it } from "vitest";
import { decodePoints } from "../shared-viewer/geometry.js";
import { mapToRender, renderToMap } from "../shared-viewer/coordinates.js";
import { ScanViewer } from "../shared-viewer/scene.js";

it("maps measured axes and floor elevation consistently and linearizes RGB", () => {
  const buffer = new ArrayBuffer(16), view = new DataView(buffer);
  [1, 2, 3].forEach((value, i) => view.setFloat32(i * 4, value, true));
  [255, 128, 0].forEach((value, i) => view.setUint8(12 + i, value));
  const cloud = decodePoints(buffer, 2);
  expect([...cloud.positions]).toEqual([1, 1, -2]);
  expect([...cloud.colors]).toEqual([1, expect.closeTo(0.21586, 4), 0]);
  expect(renderToMap(mapToRender([1, 2, 3], 2), 2)).toEqual([1, 2, 3]);
  view.setFloat32(0, NaN, true);
  expect(() => decodePoints(buffer)).toThrow("Non-finite");
});

it("clamps scrubbing and speed and will not play an empty trace", () => {
  const viewer = { cursor: 0, speed: 1, playing: false, track: { duration: 2 },
    applyPlayback() {}, request() {}, onPlayback() {} };
  ScanViewer.prototype.playback.call(viewer, { progress: 9, speed: 100 });
  expect(viewer.cursor).toBe(2); expect(viewer.speed).toBe(4);
  ScanViewer.prototype.playback.call(viewer, { progress: NaN, speed: NaN });
  expect(viewer.cursor).toBe(2); expect(viewer.speed).toBe(4);
  viewer.track = null;
  ScanViewer.prototype.playback.call(viewer, { playing: true });
  expect(viewer.playing).toBe(false);
});

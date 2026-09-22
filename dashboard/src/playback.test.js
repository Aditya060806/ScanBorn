import { describe, it, expect } from "vitest";
import { timeline, poseAt } from "../shared-viewer/playback.js";

describe("recorded pose playback", () => {
  it("uses elapsed seconds and interpolates heading across the angle wrap", () => {
    const track = timeline([], [{t:20,x:0,y:0,th:3}, {t:22,x:2,y:4,th:-3}]);
    expect(track.timed).toBe(true);
    expect(track.duration).toBe(2);
    expect(poseAt(track, 1)).toEqual({x:1,y:2,th:Math.PI});
    expect(poseAt(track, 10)).toEqual({x:2,y:4,th:-3});
  });
  it("rejects stale timestamps and retains an explicitly untimed fallback", () => {
    const track = timeline([[0,0],[1,0]], [{t:1,x:0,y:0,th:0},{t:1,x:1,y:0,th:0}]);
    expect(track.timed).toBe(false);
    expect(track.duration).toBe(.25);
    expect(poseAt(track, .125).x).toBe(.5);
    expect(poseAt(timeline(), 0)).toBeNull();
  });
});

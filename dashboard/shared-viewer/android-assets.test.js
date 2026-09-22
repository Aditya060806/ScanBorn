import { describe, it, expect, vi } from "vitest";
import { androidAssets, cacheUrl } from "./android-assets.js";

describe("Android cached geometry", () => {
  it("accepts only local cache assets", () => {
    expect(cacheUrl("/cache/scene.obj").pathname).toBe("/cache/scene.obj");
    for (const url of ["https://example.com/cache/a.obj", "/cache/../assets/a", "/cache/a?x=1", "/cache/nested/a"])
      expect(() => cacheUrl(url)).toThrow();
  });
  it("loads geometry and the surface with its original coordinate transform", async () => {
    const viewer = { setGeometry: vi.fn(), setSurface: vi.fn() };
    const bytes = new ArrayBuffer(16), transform = [[1, 0, 0, 0]];
    const assets = androidAssets(viewer, async () => ({ ok: true, arrayBuffer: async () => bytes }));
    await assets.load({ geometry: "/cache/a.bin", surface: { url: "/cache/a.obj", format: "obj", to_map: transform } });
    expect(viewer.setGeometry).toHaveBeenCalledWith(bytes);
    expect(viewer.setSurface).toHaveBeenCalledWith(bytes, "obj", transform);
    assets.dispose();
  });
  it("ignores an old surface response after another scene is selected", async () => {
    const viewer = { setGeometry: vi.fn(), setSurface: vi.fn() };
    let resolve;
    const assets = androidAssets(viewer, () => new Promise(r => { resolve = r; }));
    const old = assets.load({ surface: { url: "/cache/old.obj", format: "obj" } });
    expect(await assets.load({})).toBe(true);
    resolve({ ok: true, arrayBuffer: async () => new ArrayBuffer(0) });
    expect(await old).toBe(false);
    expect(viewer.setSurface).not.toHaveBeenCalled();
  });
});

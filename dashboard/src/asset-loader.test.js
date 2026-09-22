import { expect, it, vi } from "vitest";
import { assetLoader } from "../shared-viewer/asset-loader.js";

it("never installs a slow previous scene after a newer scene or unmount", async () => {
  const waits = [], apply = vi.fn();
  const loader = assetLoader(apply, () => new Promise(resolve => waits.push(resolve)));
  const old = loader.load("old"), current = loader.load("new");
  waits[1]({ ok: true, arrayBuffer: async () => "new geometry" }); await current;
  waits[0]({ ok: true, arrayBuffer: async () => "old geometry" }); await old;
  expect(apply.mock.calls).toEqual([["new geometry"]]);
  const pending = loader.load("leaving"); loader.dispose();
  waits[2]({ ok: true, arrayBuffer: async () => "late geometry" }); await pending;
  expect(apply).toHaveBeenCalledTimes(1);
});

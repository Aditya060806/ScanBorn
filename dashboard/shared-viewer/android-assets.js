import { assetLoader } from "./asset-loader.js";

const origin = "https://appassets.androidplatform.net";
export function cacheUrl(value) {
  const url = new URL(value, origin);
  if (url.origin !== origin || !/^\/cache\/[a-zA-Z0-9_.-]+$/.test(url.pathname) || url.search || url.hash)
    throw new Error("Invalid cached asset URL");
  return url;
}

/** Both loads are invalidated together; an old mesh cannot land on a new scene. */
export function androidAssets(viewer, fetchAsset = fetch) {
  let surface, generation = 0;
  const geometry = assetLoader(buffer => viewer.setGeometry(buffer), fetchAsset);
  return {
    async load(data) {
      const current = ++generation;
      geometry.dispose(); surface?.dispose();
      const descriptor = data.surface;
      const geometryUrl = data.geometry ? cacheUrl(data.geometry) : null;
      const surfaceUrl = descriptor ? cacheUrl(descriptor.url) : null;
      if (descriptor && !["ply", "obj"].includes(descriptor.format)) throw new Error("Invalid surface format");
      surface = assetLoader(buffer => viewer.setSurface(buffer, descriptor.format, descriptor.to_map), fetchAsset);
      await Promise.all([geometry.load(geometryUrl), surface.load(surfaceUrl)]);
      return current === generation;
    },
    dispose() { generation++; geometry.dispose(); surface?.dispose(); },
  };
}

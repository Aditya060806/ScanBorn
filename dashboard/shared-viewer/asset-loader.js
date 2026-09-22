/** Only the latest selected scene may publish asynchronously loaded geometry. */
export function assetLoader(apply, fetchAsset = fetch) {
  let generation = 0, controller;
  return {
    async load(url) {
      const current = ++generation;
      controller?.abort(); controller = new AbortController();
      if (!url) return;
      try {
        const response = await fetchAsset(url, { signal: controller.signal });
        if (!response.ok) throw new Error("Cached geometry unavailable");
        const buffer = await response.arrayBuffer();
        if (current === generation) apply(buffer);
      } catch (error) {
        if (current === generation && error.name !== "AbortError") throw error;
      }
    },
    dispose() { generation++; controller?.abort(); },
  };
}

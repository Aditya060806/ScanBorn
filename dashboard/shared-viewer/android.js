import { ScanViewer } from "./scene.js";
import { androidAssets } from "./android-assets.js";

const origin = "https://appassets.androidplatform.net";
const send = data => window.scanborn?.postMessage(JSON.stringify(data));
let viewer;
try {
  viewer = new ScanViewer(document.getElementById("viewer"), {
    onError: message => send({ type: "renderer", error: message }),
    onSelect: selection => send({ type: "selection", ...selection }),
    onPlayback: playback => send({ type: "playback", ...playback }),
  });
} catch (error) { send({ type: "renderer", error: error.message }); }
const assets = androidAssets(viewer);
window.addEventListener("message", async event => {
  if (event.origin !== origin || !viewer) return;
  try {
    const data = typeof event.data === "string" ? JSON.parse(event.data) : event.data;
    if (data.type === "scene") {
      viewer.update(data.scene);
      if (await assets.load(data)) send({ type: "sceneLoaded", cache_id: data.cache_id });
    } else if (data.type === "view" && ["fit","inspect","top","perspective","follow"].includes(data.mode)) viewer.view(data.mode);
    else if (data.type === "telemetry") viewer.update({ livePose: data.finished ? null : data.pose,
      liveTrace: data.trace, poseSamples: data.finished ? data.samples : undefined });
    else if (data.type === "layers") Object.entries(data.layers).forEach(([key,value])=>viewer.setLayer(key, value === true));
    else if (data.type === "playback") viewer.playback(data.options);
    else if (data.type === "pointSize" && Number.isFinite(data.size)) viewer.setPointSize(Math.max(1, Math.min(8, data.size)));
  } catch (error) { send({ type: "renderer", error: error.message }); }
});
send({ type: "ready" });
window.addEventListener("pagehide", () => { assets.dispose(); viewer?.dispose(); }, { once: true });

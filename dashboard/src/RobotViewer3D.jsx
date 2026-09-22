import React, { useEffect, useRef, useState } from "react";
import { ScanViewer } from "../shared-viewer/scene.js";
import { FIXTURE_VISUALIZATION } from "./fixtures.js";
import { timeline } from "../shared-viewer/playback.js";

const EMPTY = Object.freeze([]);
export default function RobotViewer3D({
  meshId, twinId, objects = EMPTY, poseTrace = EMPTY, navmesh, spec, path = EMPTY,
  fixture, livePose, liveTrace = EMPTY, poseSamples = EMPTY, liveState, onSelect,
}) {
  const host = useRef(null), viewer = useRef(null), callback = useRef(onSelect);
  callback.current = onSelect;
  const [manifest, setManifest] = useState(null), [error, setError] = useState(null);
  const [geometryError, setGeometryError] = useState(null), [loading, setLoading] = useState(false);
  const [level, setLevel] = useState(() => window.innerWidth < 700 ? 100000 : 500000);
  const [selection, setSelection] = useState(null), [playing, setPlaying] = useState(false);
  const [progress, setProgress] = useState(0);
  const [retry, setRetry] = useState(0);
  const [size, setSize] = useState(3);
  const [layers, setLayers] = useState({ geometry: true, semantics: true, occupancy: false, surface: false, grid: true });
  useEffect(() => {
    try {
      viewer.current = new ScanViewer(host.current, {
        onError: setError, onSelect: result => { setSelection(result); callback.current?.(result); },
        onPlayback: result => { setPlaying(result.playing); setProgress(result.progress); },
      });
      for (const [name, visible] of Object.entries(layers)) viewer.current.setLayer(name, visible);
      viewer.current.setPointSize(size);
    } catch (e) { setError(e.message); }
    return () => { viewer.current?.dispose(); viewer.current = null; };
  }, [retry]);
  useEffect(() => {
    const controller = new AbortController();
    setManifest(null); setGeometryError(null); setLoading(false); setSelection(null);
    if (fixture) { setManifest(FIXTURE_VISUALIZATION ?? null); return () => controller.abort(); }
    if (!meshId) return () => controller.abort();
    setLoading(true);
    fetch(twinId ? `/twins/${twinId}/visualization` : `/meshes/${meshId}/visualization`, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`Geometry manifest: HTTP ${response.status}`); return response.json(); })
      .then(value => { if (!controller.signal.aborted) setManifest(value); })
      .catch(e => { if (e.name !== "AbortError") setGeometryError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [meshId, twinId, fixture, retry]);
  useEffect(() => {
    viewer.current?.update({ meshId, manifest, objects, poseTrace, poseSamples, navmesh, spec, path, livePose, liveTrace });
  }, [meshId, manifest, objects, poseTrace, poseSamples, navmesh, spec, path, livePose, liveTrace, retry]);
  useEffect(() => {
    const asset = manifest?.geometry?.find(g => g.level === level);
    if (!asset) return;
    const controller = new AbortController();
    setLoading(true); setGeometryError(null);
    fetch(asset.url, { signal: controller.signal })
      .then(async response => { if (!response.ok) throw new Error(`Geometry: HTTP ${response.status}`); return response.arrayBuffer(); })
      .then(buffer => { if (!controller.signal.aborted) viewer.current?.setGeometry(buffer); })
      .catch(e => { if (e.name !== "AbortError") setGeometryError(e.message); })
      .finally(() => { if (!controller.signal.aborted) setLoading(false); });
    return () => controller.abort();
  }, [manifest, level, retry]);
  useEffect(() => {
    if (!manifest?.surface || !layers.surface) return;
    const controller = new AbortController();
    fetch(manifest.surface.url, {signal:controller.signal}).then(response => {
      if (!response.ok) throw new Error("Imported surface unavailable");
      return response.arrayBuffer();
    }).then(buffer => {
      // to_map puts the kept original (its own units and up axis) into the map frame.
      if (!controller.signal.aborted) viewer.current?.setSurface(buffer, manifest.surface.format, manifest.surface.to_map);
    }).catch(error => { if (error.name !== "AbortError") setGeometryError(error.message); });
    return () => controller.abort();
  }, [manifest, layers.surface, retry]);
  const toggle = name => {
    const visible = !layers[name]; setLayers({ ...layers, [name]: visible });
    viewer.current?.setLayer(name, visible);
  };
  const bounds = manifest?.bounds;
  return <section className="scan-workspace" aria-label="3D scan workspace">
    <div className="scan-workspace__tools">
      {[["fit","Fit room"], ["inspect","Inspect rover"], ["top","Top"], ["perspective","Perspective"], ["follow","Follow rover"]]
        .map(([mode,label]) => <button key={mode} type="button" onClick={() => viewer.current?.view(mode)}>{label}</button>)}
    </div>
    <div className="scan-workspace__canvas" ref={host} aria-label="Interactive reconstructed scene" />
    {error && <div role="alert" className="scan-workspace__fallback">
      <p>{error}</p><p>2D route preview. Stop and E-STOP remain outside the renderer.</p>
      <button type="button" onClick={() => { setError(null); setRetry(value => value + 1); }}>Retry 3D viewer</button>
      <svg viewBox="0 0 400 240" aria-label="2D route fallback">
        {[path,poseTrace,liveTrace].map((trace,i) => {
          const points = trace.filter(p => Number.isFinite(p?.[0]) && Number.isFinite(p?.[1]));
          if (!points.length) return null;
          const all = [...path,...poseTrace,...liveTrace].filter(p => Number.isFinite(p?.[0]) && Number.isFinite(p?.[1]));
          const xs = all.map(p=>p[0]), ys=all.map(p=>p[1]);
          const x0=Math.min(...xs), y0=Math.min(...ys), scale=Math.min(360/Math.max(1,Math.max(...xs)-x0),200/Math.max(1,Math.max(...ys)-y0));
          return <polyline key={i} fill="none" stroke={["#14b09a","#ff5b2e","#7b2cbf"][i]} strokeWidth="2"
            points={points.map(p=>`${20+(p[0]-x0)*scale},${220-(p[1]-y0)*scale}`).join(" ")} />;
        })}
      </svg>
    </div>}
    <div className="scan-workspace__status" role="status">
      {loading ? "Loading reconstructed geometry…" : manifest ? `${manifest.point_count.toLocaleString()} reconstructed points${fixture ? " · recorded synthetic room" : ""}` :
        fixture ? "Recorded fixture · semantic geometry only" : meshId ? "Preparing geometry" : "Reconstruct a scan to inspect its measured geometry"}
      {geometryError && <span> · {geometryError}</span>}
      {liveState && <strong> · {liveState}</strong>}
    </div>
    <div className="scan-workspace__tools">
      {Object.entries({geometry:"Measured points",semantics:"Inferred objects",occupancy:"Occupancy",surface:"Imported mesh",grid:"1 m grid"}).map(([key,label]) =>
        <label key={key}><input type="checkbox" checked={layers[key]} onChange={() => toggle(key)} /> {label}</label>)}
      <label>Point size <input aria-label="Point size" type="range" min="1" max="8" step="0.5" value={size}
        onChange={e=>{setSize(+e.target.value); viewer.current?.setPointSize(+e.target.value);}} /></label>
      <label>Preview <select value={level} onChange={e=>setLevel(+e.target.value)}>
        <option value="100000">100k points</option><option value="500000">500k points</option>
      </select></label>
    </div>
    {(poseTrace.length > 0 || poseSamples.length > 0) && !livePose && <div className="scan-workspace__tools">
      <button type="button" onClick={()=>{setPlaying(!playing); viewer.current?.playback({playing:!playing});}}>{playing?"Pause":"Play"}</button>
      <input aria-label="Trace position" type="range" min="0" max="1" step="0.001" value={progress}
        onChange={e=>{setPlaying(false); viewer.current?.playback({playing:false, progress:+e.target.value});}} />
      <select aria-label="Playback speed" defaultValue="1" onChange={e=>viewer.current?.playback({speed:+e.target.value})}>
        {[0.25,0.5,1,2,4].map(speed=><option key={speed} value={speed}>{speed}×</option>)}
      </select><span>{timeline(poseTrace, poseSamples).timed ? "Timestamped trace · seconds and recorded headings" : "Untimed trace · playback at 4 samples/s"}</span>
    </div>}
    <div className="scan-workspace__legend">
      <span>Metres · Z-up · grid 1 m</span><span>Green: planned · orange: simulated · purple: reported</span>
      <span>Rover hardware detail illustrative · dimensions from {spec?.name ?? manifest?.robot_spec?.name ?? "rover"} ({spec?.source ?? manifest?.robot_spec?.source ?? "reference"})</span>
      {bounds && <span>Space {(bounds[1][0]-bounds[0][0]).toFixed(2)} × {(bounds[1][1]-bounds[0][1]).toFixed(2)} m · floor Z {manifest.floor_z.toFixed(2)} m</span>}
      {manifest?.registration && <span title={manifest.registration.reason}>{manifest.registration.physically_registered
        ? "Registered on the home marker" : "Registration unknown · inspect and simulate only"}</span>}
      {manifest?.surface && <span>Imported mesh in {manifest.surface.units}, {manifest.surface.up_axis.toUpperCase()}-up ({manifest.surface.transform_source})</span>}
      {selection?.kind === "point" && <span>Selected X {selection.position[0].toFixed(3)}, Y {selection.position[1].toFixed(3)} m</span>}
      {selection?.kind === "object" && <span>{selection.object.label} · inferred · confidence {selection.object.confidence?.toFixed(2)} ·
        {selection.object.bbox3d.slice(3).map((v,i)=>(v-selection.object.bbox3d[i]).toFixed(2)).join(" × ")} m</span>}
    </div>
  </section>;
}

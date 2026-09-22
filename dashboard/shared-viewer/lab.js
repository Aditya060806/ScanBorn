import { ScanViewer } from "./scene.js";

const host = document.getElementById("scene"), output = document.getElementById("result");
const viewer = new ScanViewer(host, { onError: message => {
  document.getElementById("fallback").hidden = !message;
  if (message) output.textContent = message;
  else output.textContent = "Context restored; renderer resumed.";
} });
let count = 100000, busy = false;
const gl = viewer.renderer.getContext();
const debug = gl.getExtension("WEBGL_debug_renderer_info");
const backend = {
  vendor: gl.getParameter(debug ? debug.UNMASKED_VENDOR_WEBGL : gl.VENDOR),
  renderer: gl.getParameter(debug ? debug.UNMASKED_RENDERER_WEBGL : gl.RENDERER),
  version: gl.getParameter(gl.VERSION),
};
document.getElementById("backend").textContent = JSON.stringify(backend, null, 2);
let submissions = [];
const renderScene = viewer.renderer.render.bind(viewer.renderer);
viewer.renderer.render = (...args) => {
  const start = performance.now();
  renderScene(...args);
  if (busy) submissions.push(performance.now() - start);
};
function room(size) {
  const buffer = new ArrayBuffer(size * 16), view = new DataView(buffer);
  for (let i = 0; i < size; i++) {
    const side = i % 5, u = ((i * 73) % 997) / 997, v = ((i * 137) % 991) / 991;
    let x = u * 8 - 4, y = v * 6 - 3, z = 2;
    if (side > 0) { z += v * 2.8; if (side < 3) y = side === 1 ? -3 : 3; else { x = side === 3 ? -4 : 4; y = u * 6 - 3; } }
    view.setFloat32(i*16,x,true);view.setFloat32(i*16+4,y,true);view.setFloat32(i*16+8,z,true);
    view.setUint8(i*16+12,side ? 168 : 128);view.setUint8(i*16+13,side ? 190 : 152);view.setUint8(i*16+14,180);
  }
  return buffer;
}
function scene(id, buffer) {
  viewer.update({meshId:id, manifest:{mesh_id:id,bounds:[[-4,-3,2],[4,3,4.8]],floor_z:2,objects:[]},
    path:[[-2,-1],[2,-1],[2,1]],liveTrace:[],livePose:{x:-2,y:-1,th:0}});
  viewer.setGeometry(buffer);
}
const next = () => new Promise((resolve, reject) => {
  const timeout = setTimeout(() => reject(new Error("No animation frame for 5 seconds. Keep this browser visible; frame-time acceptance is not measured.")), 5000);
  requestAnimationFrame(time => { clearTimeout(timeout); resolve(time); });
});
function report(value) { output.textContent = JSON.stringify(value, null, 2); }
async function run(size) {
  if (busy) return;
  busy=true; count=size;output.textContent="Warming shaders, then measuring 360 frames…";
  try {
    // Keep laptop runs comparable even when the app's browser pane changes width.
    host.style.width = size === 500000 ? "1240px" : "";
    host.style.height = size === 500000 ? "396px" : "";
    scene("benchmark",room(size));
    for(let i=0;i<45;i++){viewer.request();await next();}
    const intervals=[], trace=[]; submissions=[];
    let previous=await next();
    for(let i=0;i<360;i++) {
      if (document.hidden) throw new Error("Benchmark interrupted: page was hidden");
      const angle=i/360*Math.PI*2;
      viewer.camera.position.set(Math.cos(angle)*8,6,Math.sin(angle)*8);
      viewer.controls.target.set(0,1,0);viewer.controls.update();
      const pose={x:Math.cos(angle)*2,y:Math.sin(angle),th:angle+Math.PI/2};
      trace.push([pose.x,pose.y]); viewer.update({livePose:pose,liveTrace:trace});
      const time=await next(); intervals.push(time-previous);previous=time;
      if(i % 30 === 0) output.textContent=`Measuring frame ${i+1}/360…`;
    }
    intervals.sort((a,b)=>a-b);
    submissions.sort((a,b)=>a-b);
    report({test:"360-frame orbit and live trace, after 45 warm-up frames",points:size,
      viewport:[host.clientWidth,host.clientHeight],pixelRatio:viewer.renderer.getPixelRatio(),
      frame_ms_p50:+intervals[180].toFixed(2),frame_ms_p95:+intervals[Math.ceil(intervals.length*.95)-1].toFixed(2),
      target_ms:33,pass:intervals[Math.ceil(intervals.length*.95)-1]<=33,
      submission_ms_p95:+(submissions[Math.ceil(submissions.length*.95)-1] ?? 0).toFixed(2),
      draw:{...viewer.renderer.info.render},backend,
      memory:{...viewer.renderer.info.memory},synthetic:true});
  } catch(error){output.textContent=error.message;}finally{busy=false;}
}
document.getElementById("desktop").onclick=()=>run(500000);
document.getElementById("mobile").onclick=()=>run(100000);
document.getElementById("cadence").onclick=async()=>{
  if(busy)return;busy=true;
  try {
    output.textContent="Measuring 20 idle animation callbacks; scene rendering stays idle…";
    const intervals=[];let previous=await next();
    for(let i=0;i<20;i++){const time=await next();intervals.push(time-previous);previous=time;}
    intervals.sort((a,b)=>a-b);
    report({test:"idle browser animation cadence, no scene changes",callbacks:20,
      frame_ms_p50:+intervals[10].toFixed(2),frame_ms_p95:+intervals[18].toFixed(2),
      note:"If idle callbacks are throttled, the orbit result cannot isolate renderer performance."});
  }catch(error){output.textContent=error.message;}finally{busy=false;}
};
document.getElementById("cycle").onclick=async()=>{
  if(busy)return;busy=true;
  try {
    const buffer=room(count), samples=[];
    for(let i=0;i<12;i++) {
      scene(`cycle-${i}`,buffer);viewer.render(performance.now());
      await new Promise(resolve=>setTimeout(resolve,50));
      samples.push({...viewer.renderer.info.memory});
    }
    const baseline=samples[2],stable=samples.slice(3).every(s=>s.geometries===baseline.geometries&&s.textures===baseline.textures);
    report({test:"12 scene replacements",points:count,stable_resource_counts:stable,samples});
  }finally{busy=false;}
};
document.getElementById("loss").onclick=()=>{
  const extension=viewer.renderer.getContext().getExtension("WEBGL_lose_context");
  if(!extension){output.textContent="Context-loss extension unavailable";return;}
  extension.loseContext();setTimeout(()=>extension.restoreContext(),2000);
};
document.getElementById("mounts").onclick=async()=>{
  if(busy)return;busy=true;
  const samples=[];
  try {
    for(let i=0;i<8;i++) {
      output.textContent=`Mount and dispose ${i+1}/8…`;
      const container=document.createElement("div");
      container.style.cssText="position:fixed;top:0;left:0;width:640px;height:360px;z-index:100";host.after(container);
      let probe;
      try {
        probe=new ScanViewer(container);
        probe.update({meshId:`mount-${i}`,manifest:{mesh_id:`mount-${i}`,bounds:[[-4,-3,2],[4,3,4.8]],floor_z:2,objects:[]}});
        probe.setGeometry(room(100000));
        await next();await next();probe.render(performance.now());
        const memory={...probe.renderer.info.memory}, context=probe.renderer.getContext();
        probe.dispose();probe.dispose();
        samples.push({memory,canvasRemoved:container.querySelector("canvas")===null,contextReleased:context.isContextLost()});
      }finally{probe?.dispose();container.remove();}
    }
    const baseline=samples[1].memory;
    report({test:"8 viewer mounts and disposals, 100k points",samples,
      pass:samples.every(s=>s.memory.geometries>0&&s.canvasRemoved&&s.contextReleased)&&samples.slice(2).every(s=>s.memory.geometries===baseline.geometries&&s.memory.textures===baseline.textures),
      note:"Checks renderer resource counts, canvas removal and explicit context release; not a browser-process heap measurement."});
  }catch(error){output.textContent=error.message;}finally{busy=false;}
};
scene("initial",room(count));
window.addEventListener("pagehide",()=>viewer.dispose(),{once:true});

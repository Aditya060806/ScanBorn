import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { RoomEnvironment } from "three/examples/jsm/environments/RoomEnvironment.js";
import { sceneBounds } from "./coordinates.js";
import { pointCloud, traceLine } from "./geometry.js";
import { buildRover } from "./rover.js";
import { setView } from "./camera.js";
import { semanticLayer, occupancyLayer, markerLayer } from "./layers.js";
import { disposeTree } from "./lifecycle.js";
import { importedSurface } from "./surface.js";
import { pick } from "./picking.js";
import { timeline, poseAt } from "./playback.js";

/** Framework-independent renderer. It owns only rendering and selection, never mission actions. */
export class ScanViewer {
  constructor(host, { onError = () => {}, onSelect = () => {}, onPlayback = () => {} } = {}) {
    this.host = host; this.onError = onError; this.onSelect = onSelect;
    this.onPlayback = onPlayback;
    this.scene = new THREE.Scene(); this.scene.background = new THREE.Color(0xeef0ea);
    this.camera = new THREE.PerspectiveCamera(43, 1, 0.005, 200);
    this.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    this.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 1.75));
    this.renderer.outputColorSpace = THREE.SRGBColorSpace;
    this.renderer.toneMapping = THREE.ACESFilmicToneMapping;
    this.renderer.toneMappingExposure = 1.15;
    this.renderer.shadowMap.enabled = true; this.renderer.shadowMap.type = THREE.PCFShadowMap;
    host.append(this.renderer.domElement);
    this.rebuildEnvironment();
    this.scene.add(new THREE.HemisphereLight(0xffffff, 0x77806d, 1.8));
    const key = new THREE.DirectionalLight(0xfff5e8, 3.2);
    key.position.set(3, 6, 4); key.castShadow = true;
    key.shadow.mapSize.set(1024, 1024); key.shadow.bias = -0.0002;
    key.shadow.camera.left = key.shadow.camera.bottom = -5;
    key.shadow.camera.right = key.shadow.camera.top = 5;
    this.scene.add(key); this.key = key;
    this.controls = new OrbitControls(this.camera, this.renderer.domElement);
    this.controls.maxPolarAngle = Math.PI / 2 - 0.005;
    this.controls.minDistance = 0.12;
    this.request = () => {
      if (!this.disposed && !this.frame && !document.hidden && this.visible !== false && !this.lost)
        this.frame = requestAnimationFrame(t => this.render(t));
    };
    this.controls.addEventListener("change", this.request);
    this.scene.add(this.planned = traceLine(0x14b09a), this.sim = traceLine(0xff5b2e), this.live = traceLine(0x7b2cbf));
    this.layers = {}; this.floor = 0; this.bounds = [[-2,-2,0],[2,2,2]];
    this.playing = false; this.speed = 1; this.cursor = 0;
    this.resize = new ResizeObserver(() => {
      const { width, height } = host.getBoundingClientRect();
      if (!width || !height) return;
      this.renderer.setSize(width, height); this.camera.aspect = width / height;
      this.camera.updateProjectionMatrix(); this.request();
    }); this.resize.observe(host);
    this.visibility = new IntersectionObserver(([entry]) => { this.visible = entry.isIntersecting; this.request(); });
    this.visibility.observe(host);
    this.pageVisibility = () => { this.lastTime = null; this.request(); };
    document.addEventListener("visibilitychange", this.pageVisibility);
    this.lostHandler = e => { e.preventDefault(); this.lost = true; onError("WebGL context lost. The 2D view and native controls remain available."); };
    this.restoreHandler = () => {
      try {
        // Render-target lighting has no CPU image to upload after a lost context.
        this.rebuildEnvironment();
        this.lost = false; onError(null); this.request();
      } catch (error) { onError(`Renderer recovery failed: ${error.message}`); }
    };
    this.renderer.domElement.addEventListener("webglcontextlost", this.lostHandler);
    this.renderer.domElement.addEventListener("webglcontextrestored", this.restoreHandler);
    this.down = e => { this.pointer = [e.clientX, e.clientY]; };
    this.up = e => {
      if (!this.pointer || Math.hypot(e.clientX - this.pointer[0], e.clientY - this.pointer[1]) > 5) return;
      const selection = pick(e, this.renderer.domElement, this.camera, this.layers.semantics ?? new THREE.Group(), this.floor);
      if (selection) onSelect(selection);
    };
    this.renderer.domElement.addEventListener("pointerdown", this.down);
    this.renderer.domElement.addEventListener("pointerup", this.up);
    this.update({});
  }

  rebuildEnvironment() {
    this.environment?.dispose();
    const pmrem = new THREE.PMREMGenerator(this.renderer), room = new RoomEnvironment();
    try {
      this.environment = pmrem.fromScene(room, 0.04);
      this.scene.environment = this.environment.texture;
    } finally { disposeTree(room); pmrem.dispose(); }
  }

  replace(name, object) {
    if (this.layers[name]) disposeTree(this.layers[name]);
    this.layers[name] = object; this.scene.add(object);
    object.visible = this.layerVisibility?.[name] ?? (name !== "occupancy" && name !== "surface");
  }

  update(data) {
    const old = this.data ?? {}; this.data = { ...old, ...data };
    const d = this.data, identity = d.manifest?.mesh_id ?? d.meshId;
    const newScene = !this.rover || identity !== this.identity;
    if (newScene) for (const name of ["geometry", "surface"]) {
      if (this.layers[name]) { disposeTree(this.layers[name]); delete this.layers[name]; }
    }
    this.floor = d.manifest?.floor_z ?? d.navmesh?.floor_z ?? 0;
    this.bounds = sceneBounds(d.objects, d.manifest, d.navmesh);
    if (newScene || d.spec !== old.spec || d.manifest?.robot_spec !== old.manifest?.robot_spec) {
      if (this.rover) disposeTree(this.rover);
      this.rover = buildRover(d.spec ?? d.manifest?.robot_spec); this.scene.add(this.rover);
    }
    if (newScene || d.objects !== old.objects || d.manifest !== old.manifest)
      this.replace("semantics", semanticLayer(d.objects?.length ? d.objects : d.manifest?.objects, this.floor));
    if (newScene || d.navmesh !== old.navmesh) this.replace("occupancy", occupancyLayer(d.navmesh));
    if (newScene || d.path !== old.path || d.manifest !== old.manifest) {
      this.planned.update(d.path); this.replace("markers", markerLayer(d.path, d.manifest?.capture?.home_pose));
    }
    if (newScene || d.poseTrace !== old.poseTrace || d.poseSamples !== old.poseSamples) {
      this.sim.update(d.poseTrace, 0.04); this.cursor = 0;
      this.track = timeline(d.poseTrace, d.poseSamples);
      this.playing = false; this.lastTime = null;
      this.onPlayback({ playing: false, progress: 0 });
    }
    this.live.update(d.liveTrace, 0.06);
    if (d.livePose) this.setPose(d.livePose);
    else if (this.track?.duration > 0) this.applyPlayback();
    else if (d.path?.length) this.setPose({ x: d.path[0][0], y: d.path[0][1], th: d.path[0][2] ?? 0 });
    else if (newScene) this.setPose({ x: (this.bounds[0][0] + this.bounds[1][0]) / 2,
      y: (this.bounds[0][1] + this.bounds[1][1]) / 2, th: 0 });
    if (newScene || d.manifest !== old.manifest) {
      const size = Math.ceil(Math.max(this.bounds[1][0] - this.bounds[0][0], this.bounds[1][1] - this.bounds[0][1], 2)) + 2;
      const grid = new THREE.GridHelper(size, size, 0xb2bbb0, 0xd2d8ce);
      grid.position.set((this.bounds[0][0] + this.bounds[1][0]) / 2, -0.01, -(this.bounds[0][1] + this.bounds[1][1]) / 2);
      this.replace("grid", grid);
      const shadow = new THREE.Mesh(new THREE.PlaneGeometry(size, size), new THREE.ShadowMaterial({ opacity: 0.16 }));
      shadow.rotation.x = -Math.PI / 2; shadow.position.copy(grid.position); shadow.receiveShadow = true;
      this.replace("shadow", shadow);
    }
    if (newScene || (!old.manifest && d.manifest)) this.view("fit");
    this.identity = identity; this.request();
  }

  setGeometry(buffer) {
    this.replace("geometry", pointCloud(buffer, this.floor, this.pointSize ?? 3)); this.request();
  }
  setSurface(buffer, format, toMap) {
    this.replace("surface", importedSurface(buffer, format, this.floor, toMap)); this.request();
  }
  setPointSize(size) {
    this.pointSize = size;
    if (this.layers.geometry) this.layers.geometry.material.size = size;
    this.request();
  }
  setLayer(name, visible) {
    this.layerVisibility = { ...this.layerVisibility, [name]: visible };
    if (this.layers[name]) this.layers[name].visible = visible;
    this.request();
  }
  view(mode) { this.follow = mode === "follow"; setView(this.camera, this.controls, mode, this.bounds, this.floor, this.rover); this.request(); }
  setPose(p) {
    if (Array.isArray(p)) p = { x:p[0], y:p[1], th:p[2] };
    if (!Number.isFinite(p.x) || !Number.isFinite(p.y)) return;
    if (this.follow) {
      const delta = new THREE.Vector3(p.x, 0, -p.y).sub(this.rover.position);
      this.camera.position.add(delta); this.controls.target.add(delta);
    }
    this.rover.position.set(p.x, 0, -p.y);
    const heading = p.th ?? p.heading ?? 0;
    if (Number.isFinite(heading)) this.rover.rotation.y = heading;
  }
  playback({ playing = this.playing, speed = this.speed, progress } = {}) {
    if (playing && this.cursor >= (this.track?.duration ?? 0)) this.cursor = 0;
    this.playing = Boolean(playing) && (this.track?.duration ?? 0) > 0;
    if (Number.isFinite(speed)) this.speed = Math.max(0.25, Math.min(4, speed));
    if (Number.isFinite(progress)) this.cursor = Math.max(0, Math.min(1, progress)) * (this.track?.duration ?? 0);
    this.lastTime = null; this.applyPlayback(); this.request();
    this.onPlayback({playing:this.playing, progress:this.track?.duration ? this.cursor/this.track.duration : 0});
  }
  applyPlayback() {
    if (!this.track || this.data.livePose) return;
    const pose = poseAt(this.track, this.cursor);
    if (pose) this.setPose(pose);
  }
  render(time) {
    this.frame = null;
    if (this.disposed || this.lost || document.hidden || this.visible === false) { this.lastTime = null; return; }
    if (this.playing) {
      this.cursor = Math.min(this.cursor + (this.lastTime ? Math.min((time - this.lastTime) / 1000, 0.1) : 0) * this.speed,
        this.track?.duration ?? 0);
      this.applyPlayback();
      if (this.cursor >= (this.track?.duration ?? 0)) this.playing = false;
      if (!this.playing || !this.lastPlaybackEvent || time-this.lastPlaybackEvent > 100) {
        this.lastPlaybackEvent=time;
        this.onPlayback({playing:this.playing, progress:this.track?.duration ? this.cursor/this.track.duration : 0});
      }
    }
    this.lastTime = time; this.renderer.render(this.scene, this.camera);
    if (this.playing) this.request();
  }
  dispose() {
    if (this.disposed) return;
    this.disposed = true; cancelAnimationFrame(this.frame);
    this.resize.disconnect(); this.visibility.disconnect();
    document.removeEventListener("visibilitychange", this.pageVisibility);
    const canvas = this.renderer.domElement;
    canvas.removeEventListener("webglcontextlost", this.lostHandler);
    canvas.removeEventListener("webglcontextrestored", this.restoreHandler);
    canvas.removeEventListener("pointerdown", this.down); canvas.removeEventListener("pointerup", this.up);
    this.controls.dispose(); this.key.shadow.dispose();
    disposeTree(this.scene); this.environment.dispose(); this.renderer.dispose();
    this.renderer.forceContextLoss(); canvas.remove();
  }
}

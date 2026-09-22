import * as THREE from "three";
import { mapToRender } from "./coordinates.js";

export function semanticLayer(objects = [], floor = 0) {
  const group = new THREE.Group();
  for (const object of objects) {
    const b = object.bbox3d;
    if (!b || b.length !== 6) continue;
    const box = new THREE.BoxGeometry(Math.max(b[3] - b[0], 0.01), Math.max(b[5] - b[2], 0.01),
      Math.max(b[4] - b[1], 0.01));
    const mesh = new THREE.Mesh(box, new THREE.MeshBasicMaterial({ color: 0x22307e,
      transparent: true, opacity: 0.08, depthWrite: false }));
    mesh.position.set(...mapToRender([(b[0] + b[3]) / 2, (b[1] + b[4]) / 2, (b[2] + b[5]) / 2], floor));
    mesh.userData.object = object;
    const edge = new THREE.LineSegments(new THREE.EdgesGeometry(box), new THREE.LineBasicMaterial({
      color: 0x22307e, transparent: true, opacity: 0.5 }));
    mesh.add(edge); group.add(mesh);
  }
  return group;
}

export function occupancyLayer(navmesh) {
  const group = new THREE.Group();
  if (!navmesh?.cells_b64) return group;
  const bytes = Uint8Array.from(atob(navmesh.cells_b64), c => c.charCodeAt(0));
  const { width, height, cell, origin } = navmesh;
  if (bytes.length !== width * height) return group;
  const rgba = new Uint8Array(width * height * 4);
  const palette = [[112, 181, 151, 120], [49, 59, 71, 185], [185, 178, 161, 90]];
  bytes.forEach((v, i) => rgba.set(palette[v] ?? palette[2], i * 4));
  const texture = new THREE.DataTexture(rgba, width, height);
  texture.colorSpace = THREE.SRGBColorSpace; texture.needsUpdate = true;
  const plane = new THREE.Mesh(new THREE.PlaneGeometry(width * cell, height * cell),
    new THREE.MeshBasicMaterial({ map: texture, transparent: true, depthWrite: false, side: THREE.DoubleSide }));
  plane.rotation.x = -Math.PI / 2;
  plane.position.set(origin[0] + width * cell / 2, 0.005, -origin[1] - height * cell / 2);
  group.add(plane); return group;
}

export function markerLayer(path = [], home) {
  const group = new THREE.Group();
  for (const [p, color] of [[home, 0x22307e], [path[0], 0x14b09a], [path.at(-1), 0xff5b2e]]) {
    if (!p) continue;
    const marker = new THREE.Mesh(new THREE.RingGeometry(0.065, 0.09, 32),
      new THREE.MeshBasicMaterial({ color, side: THREE.DoubleSide, depthWrite: false }));
    marker.rotation.x = -Math.PI / 2; marker.position.set(p[0], 0.025, -p[1]);
    marker.renderOrder = 4; group.add(marker);
  }
  return group;
}

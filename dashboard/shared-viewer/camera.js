import * as THREE from "three";
import { mapToRender } from "./coordinates.js";

export function setView(camera, controls, mode, bounds, floor, rover) {
  const centre = bounds[0].map((n, i) => (n + bounds[1][i]) / 2);
  centre[2] = floor;
  const target = new THREE.Vector3(...mapToRender(centre, floor));
  let size = Math.max(bounds[1][0] - bounds[0][0], bounds[1][1] - bounds[0][1], 1);
  if (mode === "inspect" || mode === "follow") {
    target.copy(rover.position); size = mode === "inspect" ? 0.35 : 0.55;
    target.y += 0.05;
  }
  controls.target.copy(target);
  const aspectFactor = Math.max(1, 1 / camera.aspect);
  if (mode === "top") camera.position.copy(target).add(new THREE.Vector3(0, size * 1.6 * aspectFactor, 0.001));
  else camera.position.copy(target).add(new THREE.Vector3(size * 0.85, size * 0.9, size * 1.05).multiplyScalar(aspectFactor));
  camera.near = 0.005; camera.far = Math.max(100, size * 15);
  camera.updateProjectionMatrix(); controls.update();
}

import * as THREE from "three";
import { renderToMap } from "./coordinates.js";

export function pick(event, canvas, camera, objects, floor) {
  const rect = canvas.getBoundingClientRect();
  const pointer = new THREE.Vector2((event.clientX - rect.left) / rect.width * 2 - 1,
    -(event.clientY - rect.top) / rect.height * 2 + 1);
  const ray = new THREE.Raycaster(); ray.setFromCamera(pointer, camera);
  const hit = ray.intersectObjects(objects.children, true)[0];
  if (hit?.object.userData.object) return { kind: "object", object: hit.object.userData.object };
  const point = ray.ray.intersectPlane(new THREE.Plane(new THREE.Vector3(0, 1, 0), 0), new THREE.Vector3());
  return point ? { kind: "point", position: renderToMap(point.toArray(), floor) } : null;
}

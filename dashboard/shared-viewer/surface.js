import * as THREE from "three";
import { PLYLoader } from "three/examples/jsm/loaders/PLYLoader.js";
import { OBJLoader } from "three/examples/jsm/loaders/OBJLoader.js";
import { mapToRender } from "./coordinates.js";

export const IDENTITY = [[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]];

/** An export's coordinates to the map frame (metres, Z up) through the manifest's `to_map`. */
export function toMapPoint(m, x, y, z) {
  return [0, 1, 2].map(r => m[r][0] * x + m[r][1] * y + m[r][2] * z + m[r][3]);
}

const finiteAt = (positions, i) =>
  Number.isFinite(positions[i * 3]) && Number.isFinite(positions[i * 3 + 1]) &&
  Number.isFinite(positions[i * 3 + 2]);

/**
 * The kept original export, drawn in the map frame. A face that names a vertex the file does
 * not have, or a vertex that is not a finite number, is dropped on its own; the rest of the
 * surface stays. `root.userData` counts what was kept and dropped.
 */
export function importedSurface(buffer, format, floor, toMap = IDENTITY) {
  const root = new THREE.Group();
  root.userData = { triangles: 0, dropped: 0 };
  const geometries = [];
  if (format === "ply") {
    const geometry = new PLYLoader().parse(buffer);
    if (geometry.index?.count) geometries.push(geometry); else geometry.dispose();
  } else {
    const object = new OBJLoader().parse(new TextDecoder().decode(buffer));
    object.traverse(child => {
      if (child.isMesh) geometries.push(child.geometry);
      child.material?.dispose?.();
    });
  }
  for (const source of geometries) {
    const kept = keepValidTriangles(source);
    root.userData.dropped += kept.dropped;
    if (source !== kept.geometry) source.dispose();
    if (!kept.triangles) { kept.geometry.dispose(); continue; }
    root.userData.triangles += kept.triangles;
    const attribute = kept.geometry.getAttribute("position");
    for (let i = 0; i < attribute.count; i++) {
      const map = toMapPoint(toMap, attribute.getX(i), attribute.getY(i), attribute.getZ(i));
      attribute.setXYZ(i, ...mapToRender(map, floor));
    }
    kept.geometry.computeVertexNormals(); kept.geometry.computeBoundingSphere();
    // Three's importers already supply working-space colors. A white material avoids
    // tinting measured colors; colorless exports retain a neutral inspection material.
    const vertexColors = !!kept.geometry.getAttribute("color");
    const material = new THREE.MeshStandardMaterial({
      color: vertexColors ? 0xffffff : 0xb9c6bd, vertexColors,
      roughness: 0.88, side: THREE.DoubleSide,
    });
    const mesh = new THREE.Mesh(kept.geometry, material); mesh.receiveShadow = true; root.add(mesh);
  }
  return root;
}

/** -> {geometry, triangles, dropped}: only faces whose three vertices exist and are finite. */
export function keepValidTriangles(geometry) {
  const positions = geometry.getAttribute("position")?.array;
  if (!positions) return { geometry, triangles: 0, dropped: 0 };
  const count = positions.length / 3;
  if (geometry.index) {
    const index = geometry.index.array, valid = [];
    for (let t = 0; t + 2 < index.length; t += 3) {
      const face = [index[t], index[t + 1], index[t + 2]];
      if (face.every(i => Number.isInteger(i) && i >= 0 && i < count && finiteAt(positions, i))) valid.push(...face);
    }
    // Vertices no kept face uses may be non-finite; zero them so bounds stay finite.
    for (let i = 0; i < count; i++) if (!finiteAt(positions, i)) positions.fill(0, i * 3, i * 3 + 3);
    geometry.setIndex(valid);
    return { geometry, triangles: valid.length / 3, dropped: Math.floor(index.length / 3) - valid.length / 3 };
  }
  // A non-indexed mesh (OBJLoader) holds each face's vertices inline; a face that named a
  // missing vertex holds NaN there.
  const kept = [], vertices = [];
  for (let t = 0; t + 2 < count; t += 3) {
    if (finiteAt(positions, t) && finiteAt(positions, t + 1) && finiteAt(positions, t + 2)) {
      kept.push(...positions.subarray(t * 3, t * 3 + 9));
      vertices.push(t, t + 1, t + 2);
    }
  }
  const rebuilt = new THREE.BufferGeometry();
  rebuilt.setAttribute("position", new THREE.BufferAttribute(Float32Array.from(kept), 3));
  const colors = geometry.getAttribute("color");
  // OBJLoader can omit colors for missing vertices. A shortened attribute cannot
  // be aligned reliably: keep a neutral surface rather than misassign colors.
  if (colors && colors.count === count) {
    const values = new Float32Array(vertices.length * 3);
    vertices.forEach((source, target) => {
      values.set([colors.getX(source), colors.getY(source), colors.getZ(source)], target * 3);
    });
    rebuilt.setAttribute("color", new THREE.BufferAttribute(values, 3));
  }
  return { geometry: rebuilt, triangles: kept.length / 9, dropped: Math.floor(count / 3) - kept.length / 9 };
}

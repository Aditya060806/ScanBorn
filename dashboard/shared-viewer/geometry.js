import * as THREE from "three";
import { mapToRender } from "./coordinates.js";

const linear = Float32Array.from({ length: 256 }, (_, i) => {
  const v = i / 255;
  return v <= 0.04045 ? v / 12.92 : ((v + 0.055) / 1.055) ** 2.4;
});

export function decodePoints(buffer, floor = 0) {
  if (buffer.byteLength % 16 || buffer.byteLength > 500000 * 16)
    throw new Error("Invalid point preview length");
  const data = new DataView(buffer), count = buffer.byteLength / 16;
  const positions = new Float32Array(count * 3), colors = new Float32Array(count * 3);
  for (let i = 0; i < count; i++) {
    const at = i * 16;
    const xyz = [data.getFloat32(at, true), data.getFloat32(at + 4, true), data.getFloat32(at + 8, true)];
    if (!xyz.every(Number.isFinite)) throw new Error("Non-finite geometry");
    positions.set(mapToRender(xyz, floor), i * 3);
    for (let c = 0; c < 3; c++) colors[i * 3 + c] = linear[data.getUint8(at + 12 + c)];
  }
  return { positions, colors };
}

export function pointCloud(buffer, floor, size = 3) {
  const { positions, colors } = decodePoints(buffer, floor);
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
  geometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));
  geometry.computeBoundingSphere();
  const material = new THREE.PointsMaterial({ size, sizeAttenuation: false, vertexColors: true });
  material.onBeforeCompile = (shader) => {
    shader.fragmentShader = shader.fragmentShader.replace("#include <clipping_planes_fragment>",
      "#include <clipping_planes_fragment>\nif (distance(gl_PointCoord, vec2(0.5)) > 0.5) discard;");
  };
  return new THREE.Points(geometry, material);
}

/** One allocation per trace, bounded at 20,000 poses. Updates reuse GPU buffers. */
export function traceLine(color) {
  const geometry = new THREE.BufferGeometry();
  geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array(20000 * 3), 3)
    .setUsage(THREE.DynamicDrawUsage));
  geometry.setDrawRange(0, 0);
  const line = new THREE.Line(geometry, new THREE.LineBasicMaterial({ color, depthWrite: false }));
  line.frustumCulled = false;
  line.renderOrder = 3;
  line.update = (trace = [], lift = 0.03) => {
    const count = Math.min(trace.length, 20000), attribute = geometry.getAttribute("position");
    for (let i = 0; i < count; i++) {
      const p = trace[trace.length - count + i];
      attribute.setXYZ(i, p[0] ?? p.x, lift, -(p[1] ?? p.y));
    }
    attribute.needsUpdate = true;
    geometry.setDrawRange(0, count);
  };
  return line;
}

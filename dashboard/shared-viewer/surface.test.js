import { describe, it, expect } from "vitest";
import * as THREE from "three";
import { importedSurface, keepValidTriangles, toMapPoint } from "./surface.js";
import { mapToRender, renderToMap } from "./coordinates.js";
import { pick } from "./picking.js";

// A Y-up centimetre export, as the server records it: file (x, y, z) -> map (x, -z, y) / 100.
const Y_UP_CM = [[0.01, 0, 0, 0], [0, 0, -0.01, 0], [0, 0.01, 0, 0], [0, 0, 0, 1]];
const FLOOR = -1.35;
const encode = text => new TextEncoder().encode(text).buffer;

// Four vertices of a floor quad 1.35 m below the phone, and one vertex that is not a number.
const PLY = `ply
format ascii 1.0
element vertex 5
property float x
property float y
property float z
element face 4
property list uchar int vertex_indices
end_header
0 -135 0
100 -135 0
100 -135 -200
0 -135 -200
nan 0 0
3 0 1 2
3 0 2 3
3 0 1 9
3 0 1 4
`;

function positions(root) {
  const out = [];
  root.traverse(child => {
    if (!child.isMesh) return;
    const p = child.geometry.getAttribute("position"), index = child.geometry.index;
    const at = i => [p.getX(i), p.getY(i), p.getZ(i)];
    if (index) for (let i = 0; i < index.count; i++) out.push(at(index.getX(i)));
    else for (let i = 0; i < p.count; i++) out.push(at(i));
  });
  return out;
}

describe("imported surfaces", () => {
  it("preserves imported OBJ colors when invalid faces are removed", () => {
    const obj = "v 0 0 0 1 0 0\nv 1 0 0 0 0.5 0\nv 0 1 0 0 0 1\nv nan 0 0 1 1 1\nf 1 2 3\nf 1 2 4\n";
    const root = importedSurface(encode(obj), "obj", 0);
    const mesh = root.children[0];
    expect(root.userData).toEqual({ triangles: 1, dropped: 1 });
    expect(mesh.material.vertexColors).toBe(true);
    expect(mesh.material.color.getHex()).toBe(0xffffff);
    const colors = mesh.geometry.getAttribute("color");
    expect(colors.count).toBe(3);
    expect(colors.getX(0)).toBe(1);
    // OBJLoader already converts sRGB to linear; never convert it twice.
    expect(colors.getY(1)).toBeCloseTo(0.21404, 4);
    expect(colors.getZ(2)).toBe(1);
  });

  it("uses PLY vertex colors and a neutral material for uncolored surfaces", () => {
    const colored = `ply\nformat ascii 1.0\nelement vertex 3\nproperty float x\nproperty float y\nproperty float z\nproperty uchar red\nproperty uchar green\nproperty uchar blue\nelement face 1\nproperty list uchar int vertex_indices\nend_header\n0 0 0 255 0 0\n1 0 0 0 128 0\n0 1 0 0 0 255\n3 0 1 2\n`;
    const mesh = importedSurface(encode(colored), "ply", 0).children[0];
    expect(mesh.material.vertexColors).toBe(true);
    expect(mesh.geometry.getAttribute("color").getY(1)).toBeCloseTo(0.21586, 2);
    const plain = importedSurface(encode("v 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n"), "obj", 0).children[0];
    expect(plain.material.vertexColors).toBe(false);
  });

  it("does not invent color alignment for a malformed OBJ color stream", () => {
    const obj = "v 0 0 0 1 0 0\nv 1 0 0 0 1 0\nv 0 1 0 0 0 1\nf 1 2 9\nf 1 2 3\n";
    const mesh = importedSurface(encode(obj), "obj", 0).children[0];
    expect(mesh.material.vertexColors).toBe(false);
    expect(mesh.geometry.getAttribute("position").count).toBe(3);
  });
  it("maps a Y-up centimetre export into the map frame and keeps only valid faces", () => {
    const root = importedSurface(encode(PLY), "ply", FLOOR, Y_UP_CM);
    expect(root.userData).toEqual({ triangles: 2, dropped: 2 });
    // File (100, -135, -200) cm is map (1, 2, -1.35) m: on the floor, 1 m east and 2 m north.
    expect(toMapPoint(Y_UP_CM, 100, -135, -200)).toEqual([1, 2, -1.35]);
    const drawn = positions(root);
    expect(drawn).toHaveLength(6);
    const corner = mapToRender([1, 2, -1.35], FLOOR);
    expect(drawn.some(p => p.every((v, i) => Math.abs(v - corner[i]) < 1e-6))).toBe(true);
    // Every drawn vertex lies on the floor, which renders at height 0.
    for (const p of drawn) expect(Math.abs(p[1])).toBeLessThan(1e-6);
  });

  it("drops an OBJ face that names a missing vertex and keeps the others", () => {
    const obj = "v 0 0 0\nv 1 0 0\nv 1 1 0\nv 0 1 0\nf 1 2 3\nf 1 3 4\nf 1 2 9\n";
    const root = importedSurface(encode(obj), "obj", 0);
    expect(root.userData).toEqual({ triangles: 2, dropped: 1 });
    for (const p of positions(root)) expect(p.every(Number.isFinite)).toBe(true);
  });

  it("an identity transform draws a legacy import exactly as before", () => {
    const geometry = new THREE.BufferGeometry();
    geometry.setAttribute("position", new THREE.BufferAttribute(new Float32Array([0, 0, 0, 1, 0, 0, 0, 1, 0]), 3));
    geometry.setIndex([0, 1, 2]);
    expect(keepValidTriangles(geometry)).toMatchObject({ triangles: 1, dropped: 0 });
    expect(toMapPoint([[1, 0, 0, 0], [0, 1, 0, 0], [0, 0, 1, 0], [0, 0, 0, 1]], 3, 4, 5)).toEqual([3, 4, 5]);
  });
});

describe("picking round trip (A1)", () => {
  it("a click on the floor returns the map point under it, floor offset kept", () => {
    const target = [1.25, 2.5, FLOOR];
    const [x, , z] = mapToRender(target, FLOOR);
    const camera = new THREE.PerspectiveCamera(50, 1, 0.1, 100);
    camera.position.set(x, 5, z); camera.up.set(0, 0, -1); camera.lookAt(x, 0, z);
    camera.updateMatrixWorld(); camera.updateProjectionMatrix();
    const canvas = { getBoundingClientRect: () => ({ left: 0, top: 0, width: 200, height: 200 }) };
    const hit = pick({ clientX: 100, clientY: 100 }, canvas, camera, new THREE.Group(), FLOOR);
    expect(hit.kind).toBe("point");
    hit.position.forEach((v, i) => expect(Math.abs(v - target[i])).toBeLessThan(1e-3));
    expect(renderToMap(mapToRender(target, FLOOR), FLOOR)).toEqual(target);
  });
});

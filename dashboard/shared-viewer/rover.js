import * as THREE from "three";
import { mergeGeometries } from "three/addons/utils/BufferGeometryUtils.js";

/** Physical wheelbase/radius/height follow the spec. Cosmetic hardware is illustrative. */
export function buildRover(spec = {}) {
  const group = new THREE.Group();
  const radius = spec.wheel_radius_m ?? 0.0325, base = spec.wheelbase_m ?? 0.17;
  const height = spec.height_m ?? 0.12, length = Math.min(0.22, (spec.footprint_radius_m ?? 0.15) * 1.45);
  const plate = new THREE.MeshStandardMaterial({ color: 0x49525b, metalness: 0.65, roughness: 0.38 });
  const tire = new THREE.MeshStandardMaterial({ color: 0x20262a, roughness: 0.94 });
  const metal = new THREE.MeshStandardMaterial({ color: 0xb6bfc5, metalness: 0.8, roughness: 0.28 });
  const board = new THREE.MeshStandardMaterial({ color: 0x176850, roughness: 0.6 });
  const orange = new THREE.MeshStandardMaterial({ color: 0xff5b2e, roughness: 0.55 });
  const brass = new THREE.MeshStandardMaterial({ color: 0xb79b55, metalness: 0.75, roughness: 0.38 });
  function box(x, y, z, sx, sy, sz, material) {
    const mesh = new THREE.Mesh(new THREE.BoxGeometry(sx, sy, sz), material);
    mesh.position.set(x, y, z); mesh.castShadow = true; mesh.receiveShadow = true; group.add(mesh);
    return mesh;
  }
  function cylinder(x, y, z, r, width, material) {
    const mesh = new THREE.Mesh(new THREE.CylinderGeometry(r, r, width, 24), material);
    mesh.rotation.x = Math.PI / 2; mesh.position.set(x, y, z); mesh.castShadow = true; group.add(mesh);
    return mesh;
  }
  // Heading zero is +X; wheels straddle the map Y axis (render Z).
  box(0, radius + 0.006, 0, length, 0.008, base * 0.77, plate);
  box(-0.01, height * 0.68, 0, length * 0.76, 0.006, base * 0.7, plate);
  for (const side of [-1, 1]) {
    cylinder(0, radius, side * base / 2, radius, 0.022, tire);
    cylinder(0, radius, side * (base / 2 + 0.012), radius * 0.48, 0.003, metal);
    cylinder(0, radius, side * (base / 2 + 0.014), radius * 0.18, 0.004, plate);
    // Discrete tread blocks and recessed hub fasteners remain cosmetic.
    for (let i = 0; i < 24; i++) {
      const angle = i * Math.PI / 12;
      const tread = box(Math.sin(angle) * radius * 0.97,
        radius + Math.cos(angle) * radius * 0.97, side * base / 2,
        radius * 0.14, radius * 0.06, 0.023, tire);
      tread.rotation.z = -angle;
    }
    for (let i = 0; i < 5; i++) {
      const angle = i * Math.PI * 2 / 5;
      cylinder(Math.sin(angle) * radius * 0.33, radius + Math.cos(angle) * radius * 0.33,
        side * (base / 2 + 0.015), 0.0018, 0.002, plate);
    }
    cylinder(0, radius, side * base * 0.3, radius * 0.46, 0.035, metal);
    for (const x of [-length * 0.27, length * 0.27])
      box(x, height * 0.49, side * base * 0.28, 0.004, height * 0.35, 0.004, metal);
  }
  const caster = new THREE.Mesh(new THREE.SphereGeometry(radius * 0.35, 16, 12), metal);
  caster.position.set(-length * 0.37, radius * 0.35, 0); group.add(caster);
  box(-length * 0.15, radius + 0.023, 0, 0.07, 0.028, 0.047, orange);
  box(-0.02, height * 0.75, 0, 0.057, 0.004, 0.043, board);
  box(-0.02, height * 0.8, 0, 0.022, 0.009, 0.022, tire);
  // Controller headers, radio shield, USB connector and mounting screws.
  for (const z of [-0.017, 0.017]) {
    box(-0.02, height * 0.79, z, 0.044, 0.006, 0.004, tire);
    for (let i = 0; i < 8; i++)
      box(-0.039 + i * 0.0055, height * 0.82, z, 0.001, 0.003, 0.001, brass);
  }
  box(-0.036, height * 0.8, 0, 0.012, 0.007, 0.014, metal);
  box(0.01, height * 0.77, 0, 0.007, 0.005, 0.008, metal);
  for (const x of [-length * 0.27, length * 0.27]) {
    for (const z of [-base * 0.28, base * 0.28]) {
      const screw = cylinder(x, height * 0.68 + 0.004, z, 0.0028, 0.002, metal);
      screw.rotation.x = 0;
    }
  }
  // Battery retaining strap and two short protected power leads.
  box(-length * 0.15, radius + 0.038, 0, 0.011, 0.002, 0.049, tire);
  for (const [z, material] of [[-0.013, orange], [0.013, tire]]) {
    const curve = new THREE.CatmullRomCurve3([
      new THREE.Vector3(-length * 0.15 + 0.035, radius + 0.026, z),
      new THREE.Vector3(length * 0.2, height * 0.53, z),
      new THREE.Vector3(0.007, height * 0.73, z),
    ]);
    group.add(new THREE.Mesh(new THREE.TubeGeometry(curve, 12, 0.0012, 5, false), material));
  }
  for (const z of [-0.02, 0.02]) {
    box(length * 0.35, height * 0.82, z, 0.013, 0.016, 0.017, metal);
    box(length * 0.39, height * 0.82, z, 0.005, 0.009, 0.011, tire);
  }
  // Static hardware shares six materials: bake it into one draw per material.
  // This keeps inspection detail inexpensive on the embedded phone viewer.
  const batches = new Map();
  for (const mesh of [...group.children]) {
    mesh.updateMatrix();
    mesh.geometry.applyMatrix4(mesh.matrix);
    if (!batches.has(mesh.material)) batches.set(mesh.material, []);
    batches.get(mesh.material).push(mesh.geometry);
    group.remove(mesh);
  }
  for (const [material, geometries] of batches) {
    const mesh = new THREE.Mesh(mergeGeometries(geometries), material);
    mesh.castShadow = true; mesh.receiveShadow = true;
    group.add(mesh);
    geometries.forEach(geometry => geometry.dispose());
  }
  return group;
}

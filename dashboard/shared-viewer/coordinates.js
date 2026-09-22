// All inputs use right-handed metres, Z-up. Scene height is relative to the floor.
export const mapToRender = ([x, y, z = 0], floor = 0) => [x, z - floor, -y];
export const renderToMap = ([x, y, z], floor = 0) => [x, -z, y + floor];

export function sceneBounds(objects = [], manifest, navmesh) {
  if (manifest?.bounds) return manifest.bounds;
  if (navmesh) return [
    [...navmesh.origin, navmesh.floor_z ?? 0],
    [navmesh.origin[0] + navmesh.width * navmesh.cell,
      navmesh.origin[1] + navmesh.height * navmesh.cell, (navmesh.floor_z ?? 0) + 2],
  ];
  if (!objects.length) return [[-2, -2, 0], [2, 2, 2]];
  const lo = [Infinity, Infinity, Infinity], hi = [-Infinity, -Infinity, -Infinity];
  for (const { bbox3d: b } of objects) for (let i = 0; i < 3; i++) {
    lo[i] = Math.min(lo[i], b[i]); hi[i] = Math.max(hi[i], b[i + 3]);
  }
  return [lo, hi];
}

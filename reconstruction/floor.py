"""Floor height of a gravity-aligned point cloud.

ARCore depth and Scaniverse exports are both gravity-aligned with Z up, so the floor is a
horizontal plane: in a histogram of point heights it is a dense spike at the bottom of the
room. The minimum z is not that — one flying pixel below the floor drags a min-z floor down
with it, and everything standing on the real floor then lands in the obstacle band.

Shared by segmentation (which slab is floor) and the navmesh (which heights the robot
sweeps), so the two can never disagree about where the floor is.
"""
import numpy as np

BIN = 0.02               # m: the reconstruction voxel, so one flat surface fills one or two bins
REFINE = 0.10            # m above the first real surface in which its peak is looked for
SURFACE_FRACTION = 0.10  # a bin is a real surface if it holds this share of the densest bin
MAX_BINS = 100_000       # beyond this the height range is garbage, not a room


def estimate_floor_z(points, bin_size: float = BIN) -> float:
    """Height of the floor: the peak of the lowest real surface in the height histogram.

    "Lowest real surface" is the lowest bin holding at least SURFACE_FRACTION of the densest
    bin's points. Outliers scatter across many bins and never get there; a floor does even
    when furniture hides most of it, down to a tenth of the densest surface in the room.
    The peak is then taken within REFINE above that bin, because sensor noise spreads a
    floor across a few bins and the lowest of them is its tail, not its middle.

    `points` is an (N, 3) array or a 1-D array of heights. Raises ValueError when there is
    no finite height to work with.
    """
    arr = np.asarray(points, dtype=np.float64)
    z = arr[:, 2] if arr.ndim == 2 else arr
    z = z[np.isfinite(z)]
    if z.size == 0:
        raise ValueError("cannot estimate a floor from an empty cloud")

    lo, hi = float(z.min()), float(z.max())
    if (hi - lo) / bin_size > MAX_BINS:
        # A handful of points kilometres away would otherwise allocate a histogram the size
        # of the error. Trimming the extreme 0.5 % each side keeps every plausible room.
        lo, hi = (float(v) for v in np.percentile(z, [0.5, 99.5]))
        z = z[(z >= lo) & (z <= hi)]
    if hi - lo < bin_size:
        return float(np.median(z))

    counts, edges = np.histogram(z, bins=int(np.ceil((hi - lo) / bin_size)), range=(lo, hi))
    first = int(np.flatnonzero(counts >= SURFACE_FRACTION * counts.max())[0])
    span = max(1, int(round(REFINE / bin_size)))
    peak = first + int(np.argmax(counts[first:first + span + 1]))

    # The median of the peak bin and its neighbours, not the bin centre: a 2 cm bin would
    # otherwise put a quantisation error of up to 1 cm into every height above it.
    lower, upper = edges[max(peak - 1, 0)], edges[min(peak + 2, len(edges) - 1)]
    return float(np.median(z[(z >= lower) & (z <= upper)]))

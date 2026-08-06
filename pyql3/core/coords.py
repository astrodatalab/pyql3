"""Display <-> array coordinate mapping for the viewer's flip and 90° rotations.

`ImageViewer.apply_spatial_transforms()` flips and `np.rot90`s the **data**, so a coordinate
on screen is not a coordinate in the array. Mapping between the two was implemented three
times, in three places, at three levels of correctness (`BUGS.md` B13/B14) — this module is
the single implementation those call sites now share, and the only place the arithmetic is
allowed to live.

Everything here is a pure function of numbers, deliberately: the mapping can then be tested
against the real `apply_spatial_transforms` over every pixel of a non-square array in all
eight flip × rotation combinations, without a viewer or a `QApplication`.
`ImageViewer.orig_to_display()` / `.display_to_orig()` are thin adapters that supply the
viewer's own `flip`, `rot_angle` and dimensions.

Two coordinate spaces, following B14's names:

- **orig** — indices into `transposed_data`, i.e. before the display flip and rotation. This
  is the coordinate along whichever FITS axis is currently mapped to X or Y by the AXIS 1/2/3
  combo boxes, which is what a WCS lookup needs. Note it is *not* `raw_data` order: choosing
  which FITS axis is X is a separate step, and this module knows nothing about it.
- **display** — indices into `display_data`, what is on screen.

View rotation (`apply_view_rotation`) is deliberately absent. That one is a `QTransform` on
the ImageItem rather than a change to the data, so items parented to the image inherit it and
no coordinate arithmetic is needed.

### The three pixel conventions

The helpers at the bottom exist because three conventions meet in this application and mixing
them shifts things by half or a whole pixel:

| Convention | Origin | Centre of the first pixel |
|------------|--------|---------------------------|
| FITS / ds9 | 1-based | `1.0` |
| numpy index | 0-based | `0.0` |
| pyqtgraph ImageItem | 0-based, corner | `0.5` |
"""

import math


def _steps(rot_angle):
    """`rot_angle` in degrees as a number of 90° `np.rot90` steps, 0-3."""
    return (int(rot_angle) // 90) % 4


def display_dims(nx, ny, rot_angle=0):
    """On-screen `(nx, ny)` for an array whose orig dimensions are `(nx, ny)`.

    Each 90° step swaps the extents. Forgetting that is precisely B13.
    """
    return (ny, nx) if _steps(rot_angle) % 2 else (nx, ny)


def orig_dims(display_nx, display_ny, rot_angle=0):
    """The inverse of `display_dims`: orig dimensions given the on-screen ones."""
    return display_dims(display_nx, display_ny, rot_angle)


def orig_to_display(x, y, nx, ny, flip=False, rot_angle=0):
    """Map an orig coordinate to where it appears on screen.

    `(nx, ny)` are the **orig** dimensions. Fractional coordinates are fine — a region's
    centre is rarely on an integer — and the result is always float.

    The operations are applied in the same order as `apply_spatial_transforms`: flip first,
    then the rotations. Reversing that order is a different transform whenever both are
    active (see `BUGS.md` B20).
    """
    cx, cy = float(x), float(y)
    mx, my = float(nx), float(ny)

    if flip:
        cx = mx - 1.0 - cx

    for _ in range(_steps(rot_angle)):
        # np.rot90 sends (x, y) -> (my - 1 - y, x) and swaps the extents with it.
        cx, cy = my - 1.0 - cy, cx
        mx, my = my, mx

    return cx, cy


def display_to_orig(x, y, nx, ny, flip=False, rot_angle=0):
    """Map a screen coordinate back to an orig coordinate.

    `(nx, ny)` are the **orig** dimensions, as for `orig_to_display`, so the two functions
    take the same arguments and are exact inverses. Undoing the rotations first and the flip
    second mirrors the forward order.
    """
    cx, cy = float(x), float(y)
    mx, my = display_dims(float(nx), float(ny), rot_angle)

    for _ in range(_steps(rot_angle)):
        # Inverse of one np.rot90 step, where mx is the current x extent.
        cx, cy = cy, mx - 1.0 - cx
        mx, my = my, mx

    if flip:
        cx = mx - 1.0 - cx

    return cx, cy


def orig_angle_to_display(angle_deg, flip=False, rot_angle=0):
    """Map a direction (degrees CCW from +X in orig space) to its on-screen direction.

    A flip about X mirrors an angle to `180 - angle`; each 90° rotation adds 90°. Applied in
    that order, matching the data transform. This is what a box's position angle and an
    arrow's heading have to follow.
    """
    angle = float(angle_deg)
    if flip:
        angle = 180.0 - angle
    return (angle + 90.0 * _steps(rot_angle)) % 360.0


def display_angle_to_orig(angle_deg, flip=False, rot_angle=0):
    """Inverse of `orig_angle_to_display`."""
    angle = float(angle_deg) - 90.0 * _steps(rot_angle)
    if flip:
        angle = 180.0 - angle
    return angle % 360.0


# --------------------------------------------------------------- pixel conventions

def fits_to_index(v):
    """FITS/ds9 1-based pixel coordinate -> numpy index. Pixel 1 becomes index 0."""
    return float(v) - 1.0


def index_to_fits(v):
    """numpy index -> FITS/ds9 1-based pixel coordinate."""
    return float(v) + 1.0


def index_to_item(v):
    """numpy index -> pyqtgraph ImageItem coordinate of that pixel's centre.

    An ImageItem draws pixel `i` over `[i, i+1)`, so its centre is at `i + 0.5`. Code that
    places a marker or an ROI at a pixel needs this offset; several call sites add the 0.5 by
    hand today.
    """
    return float(v) + 0.5


def item_to_index(v):
    """pyqtgraph ImageItem coordinate -> fractional numpy index.

    Use `math.floor` on the result for the index of the pixel a point falls inside.
    """
    return float(v) - 0.5


def item_to_pixel(v):
    """pyqtgraph ImageItem coordinate -> index of the pixel containing it."""
    return int(math.floor(float(v)))

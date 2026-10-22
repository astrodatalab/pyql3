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

import numpy as np


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
    return _orig_to_display(float(x), float(y), float(nx), float(ny), flip, rot_angle)


def orig_to_display_arrays(x, y, nx, ny, flip=False, rot_angle=0):
    """`orig_to_display` over whole arrays of coordinates at once, returning two float arrays.

    For overlays with one position per catalog row, where a call per row costs more than the
    rest of the redraw (`BUGS.md` R31). A NaN stays NaN. The arithmetic is the same function
    the scalar version uses.
    """
    return _orig_to_display(np.asarray(x, dtype=float), np.asarray(y, dtype=float),
                            float(nx), float(ny), flip, rot_angle)


def _orig_to_display(cx, cy, mx, my, flip, rot_angle):
    """The mapping behind both `orig_to_display` variants; `cx`, `cy` are floats or arrays."""
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


def fits_to_index_array(v):
    """`fits_to_index` for an array of FITS/ds9 pixel coordinates."""
    return np.asarray(v, dtype=float) - 1.0


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


def box_index_slices(x0, y0, width, height, nx, ny):
    """Whole pixels covered by a box in ImageItem coordinates, as `(x slice, y slice)`.

    The box is `[x0, x0 + width) x [y0, y0 + height)`, and a pixel belongs to it when its
    centre (`i + 0.5` in ImageItem coordinates) lies inside. Both ends of each slice are
    computed from the unclamped box and only then clipped to `[0, nx) x [0, ny)`, so a box
    hanging off the low edge keeps just the part that is on the image. Returns `None` when
    no pixel remains, which a caller reports rather than substituting an edge pixel.
    """
    x_lo, x_hi = math.ceil(x0 - 0.5), math.ceil(x0 + width - 0.5)
    y_lo, y_hi = math.ceil(y0 - 0.5), math.ceil(y0 + height - 0.5)
    x_lo, x_hi = max(0, x_lo), min(nx, x_hi)
    y_lo, y_hi = max(0, y_lo), min(ny, y_hi)
    if x_hi <= x_lo or y_hi <= y_lo:
        return None
    return slice(x_lo, x_hi), slice(y_lo, y_hi)


def circle_index_mask(x0, y0, width, height, x_slice, y_slice):
    """Which pixels of `x_slice` x `y_slice` have their centres inside the circle a box draws.

    The box is `[x0, x0 + width) x [y0, y0 + height)` in ImageItem coordinates, and its circle
    is centred on the box with radius half the shorter side. The slices are the pixels to
    test, normally the box's own from `box_index_slices`, already clipped to the image: the
    circle is still the drawn one, so a box hanging off an edge keeps the drawn circle's
    on-image pixels rather than a smaller circle fitted to the clipped box (`BUGS.md` R80).
    Returns a boolean array of shape `(len(x_slice), len(y_slice))`.
    """
    cx, cy = x0 + width / 2.0, y0 + height / 2.0
    r = min(width, height) / 2.0
    dx = np.arange(x_slice.start, x_slice.stop) + 0.5 - cx
    dy = np.arange(y_slice.start, y_slice.stop) + 0.5 - cy
    return dx[:, None] ** 2 + dy[None, :] ** 2 <= r ** 2

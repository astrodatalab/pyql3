"""Per-channel aperture photometry on a cube: a circular aperture, a sky annulus.

This is the Depth Plot's circular extraction, done once per wavelength channel the way
aperture photometry is done on an image. It imports no Qt, so the arithmetic can be tested
without a display -- and cross-checked against `gui/tools/photometry.py`, which must agree
with it plane for plane.

What "background subtraction" means here
----------------------------------------
One background level per channel, from the annulus, subtracted from every pixel in the
aperture before the pixels are combined::

    b(lam)   = estimator over the whole pixels of the annulus
    Total    = sum_i w_i * (d_i(lam) - b(lam))  =  sum_i w_i d_i(lam) - b(lam) * A
    Average  = sum_i w_i * (d_i(lam) - b(lam)) / sum_i w_i
    Median   = median_i (d_i(lam) - b(lam))

The `Total` line is textbook aperture photometry, which is why it is the Depth Plot's
default: `aperture_sum - median(annulus) * aperture_area`. The other two are the same
quantity per pixel, so switching between them rescales rather than redefines.

Note what is *not* offered: a background *total*. Summing the annulus and subtracting that
from each pixel has no meaning -- the number would grow with the annulus you happened to
draw. `ESTIMATORS` therefore holds only `Median` and `Average`.

Fractional pixels
-----------------
`Total` and `Average` weight each pixel by the fraction of it the aperture covers
(photutils' ``method='exact'``), which matters for the small apertures typical of an IFU
field -- a 3 px aperture quantised to whole pixels is wrong by several percent. A median
cannot be weighted meaningfully, so `Median` uses whole pixels whose centre falls inside
(``method='center'``), as does the annulus estimator.

Axis order (the trap)
---------------------
The cube here is `(z, x, y)` -- the Depth Plot's display order. photutils works in image
order, `(row, col) == (y, x)`. Rather than transpose every plane of a 465-channel cube,
this module builds the 2-D masks in photutils' frame and transposes *those*, once. Get it
backwards and the aperture lands at `(y, x)`: on a square field with a centred source the
answer still looks plausible, which is why `tests/test_spectral_photometry.py` puts an
asymmetric source off-centre in a non-square cube.
"""

import warnings
from dataclasses import dataclass

import numpy as np
from photutils.aperture import CircularAnnulus, CircularAperture

#: How the pixels of the aperture are combined into one number per channel.
COMBINE_METHODS = ("Total", "Average", "Median")

#: How the pixels of the annulus are reduced to one background level per channel.
#: A total is deliberately absent -- see the module docstring.
ESTIMATORS = ("Median", "Average")


class ApertureError(ValueError):
    """The requested geometry cannot be measured on this cube."""


@dataclass
class SpectrumResult:
    """The three curves the Depth Plot draws, plus what they were measured over."""

    source: np.ndarray
    background: np.ndarray | None
    subtracted: np.ndarray | None
    aperture_area: float
    annulus_pixels: int

    def as_tuple(self):
        return self.source, self.background, self.subtracted


def _cutout(cube, mask):
    """`cube` restricted to `mask`'s bounding box, with the matching weight cutout.

    Returns `(sub, weights)` where `sub` is `(z, nx_sub, ny_sub)` and `weights` has the same
    two spatial dimensions, or `(None, None)` when the mask falls entirely outside the cube.

    The index gymnastics are the whole point of this helper. `mask` was built against a
    `(ny, nx)` image, so its overlap slices come back as `(slice_y, slice_x)`; the cube is
    `(z, x, y)`, so the two have to be applied in the other order and the weights transposed.
    """
    ny, nx = cube.shape[2], cube.shape[1]
    overlap = mask.get_overlap_slices((ny, nx))
    slc_large, slc_small = overlap
    if slc_large is None or slc_small is None:
        return None, None

    sub = cube[:, slc_large[1], slc_large[0]]
    weights = mask.data[slc_small].T
    if sub.shape[1:] != weights.shape or sub.size == 0:
        return None, None
    return sub, weights


def _require(condition, message):
    if not condition:
        raise ApertureError(message)


def _validate(cube, r_ap):
    _require(isinstance(cube, np.ndarray) and cube.ndim == 3,
             "a spectrum needs a 3-D cube in (z, x, y) order")
    _require(r_ap > 0, f"aperture radius must be positive, got {r_ap}")


def aperture_spectrum(cube, center, r_ap, combine="Total"):
    """One value per channel from a circular aperture. No background subtraction.

    `cube` is `(z, x, y)`; `center` is `(x, y)` in the same pixel coordinates.
    """
    _validate(cube, r_ap)
    _require(combine in COMBINE_METHODS,
             f"combine must be one of {COMBINE_METHODS}, got {combine!r}")

    cx, cy = center
    # 'center' for a median (a weighted median is not a defined quantity), fractional
    # coverage otherwise.
    method = "center" if combine == "Median" else "exact"
    mask = CircularAperture((cx, cy), r=r_ap).to_mask(method=method)
    sub, weights = _cutout(cube, mask)
    if sub is None:
        raise ApertureError(
            f"the aperture at ({cx:.2f}, {cy:.2f}) with radius {r_ap:g} does not overlap "
            "the cube")

    if combine == "Median":
        inside = weights > 0
        _require(inside.any(), "the aperture covers no whole pixel; increase its radius")
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", RuntimeWarning)  # all-NaN channel -> NaN
            values = np.nanmedian(sub[:, inside], axis=1)
        return values, float(inside.sum())

    finite = np.isfinite(sub)
    # Weight only where there is data, so a NaN pixel neither contributes flux nor inflates
    # the area a background level is multiplied by.
    per_channel_weight = np.einsum("zxy,xy->z", finite.astype(float), weights)
    total = np.einsum("zxy,xy->z", np.where(finite, sub, 0.0), weights)

    if combine == "Total":
        return total, per_channel_weight
    with np.errstate(invalid="ignore", divide="ignore"):
        return np.where(per_channel_weight > 0, total / per_channel_weight, np.nan), \
            per_channel_weight


def annulus_background(cube, center, r_in, r_out, estimator="Median"):
    """One background level per channel from a sky annulus, and its pixel count."""
    _require(isinstance(cube, np.ndarray) and cube.ndim == 3,
             "a background needs a 3-D cube in (z, x, y) order")
    _require(estimator in ESTIMATORS,
             f"estimator must be one of {ESTIMATORS}, got {estimator!r}")
    _require(0 < r_in < r_out,
             f"the annulus needs 0 < inner < outer, got inner={r_in:g} outer={r_out:g}")

    cx, cy = center
    mask = CircularAnnulus((cx, cy), r_in=r_in, r_out=r_out).to_mask(method="center")
    sub, weights = _cutout(cube, mask)
    if sub is None:
        raise ApertureError(
            f"the annulus at ({cx:.2f}, {cy:.2f}) does not overlap the cube")

    inside = weights > 0
    count = int(inside.sum())
    _require(count > 0,
             "the annulus contains no whole pixel; widen it or move it onto the cube")

    values = sub[:, inside]
    with warnings.catch_warnings():
        # A channel that is entirely NaN inside the annulus -- a dead plane -- yields NaN
        # rather than raising, so one bad channel does not lose the whole spectrum.
        warnings.simplefilter("ignore", RuntimeWarning)
        level = np.nanmedian(values, axis=1) if estimator == "Median" \
            else np.nanmean(values, axis=1)
    return level, count


def subtract_background(source, level, combine, aperture_area):
    """Apply a per-channel background level to an already-combined aperture spectrum.

    Where the level came from -- a concentric annulus or a region drawn somewhere else --
    makes no difference to how it is applied, so both callers use this and cannot drift
    apart. `aperture_area` is the summed pixel weight and matters only for `Total`, which
    is a sum over pixels and so needs the level multiplied by how many there were.
    """
    if combine == "Total":
        return source - level * aperture_area
    return source - level


def annulus_spectrum(cube, center, r_ap, r_in=None, r_out=None,
                     combine="Total", estimator="Median"):
    """The Depth Plot's three curves for a circular aperture on a cube.

    With `r_in`/`r_out` given, `background` and `subtracted` are filled in; without them
    both are `None` and only the aperture is measured. `center` is `(x, y)` and the cube is
    `(z, x, y)`, both in display pixel coordinates -- see the module docstring.
    """
    source, area = aperture_spectrum(cube, center, r_ap, combine=combine)

    if r_in is None or r_out is None:
        return SpectrumResult(source=source, background=None, subtracted=None,
                              aperture_area=float(np.nanmax(area)) if np.ndim(area) else float(area),
                              annulus_pixels=0)

    level, count = annulus_background(cube, center, r_in, r_out, estimator=estimator)

    # sum_i w_i (d_i - b) == sum_i w_i d_i - b * sum_i w_i, with the per-channel weight so
    # that a NaN pixel is excluded from both terms consistently.
    subtracted = subtract_background(source, level, combine, area)

    scalar_area = float(np.nanmax(area)) if np.ndim(area) else float(area)
    return SpectrumResult(source=source, background=level, subtracted=subtracted,
                          aperture_area=scalar_area, annulus_pixels=count)

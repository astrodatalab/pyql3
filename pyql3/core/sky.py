"""Mapping between orig pixel coordinates and the sky, for a 2-D or 3-D WCS.

Two things make this less trivial than `wcs.pixel_to_world`:

**`wcs.celestial` is silently wrong here.** For an OSIRIS cube, whose axes are
(WAVE, DEC, RA), it returns a WCS with `ctype == ['DEC--TAN', 'RA---TAN']` — latitude first —
so the natural `world_to_pixel_values(ra, dec)` call returns `(nan, nan)` with no exception and
no warning. `wcs.sub(['longitude','latitude'])` instead reorders the axes so pixel axis 0 is
always longitude and axis 1 always latitude, which is both well defined and what the `regions`
package expects to be handed.

**The displayed axes are not necessarily the celestial ones, or in that order.** The AXIS 1/2/3
combo boxes choose which FITS axis is X and which is Y, so orig X can be the latitude axis, or
an axis that is not celestial at all (wavelength against declination is a perfectly reasonable
thing to display, and has no sky position). `CelestialMap` resolves that correspondence once,
using `wcs.wcs.lng` / `wcs.wcs.lat` — astropy's own record of which axes are longitude and
latitude — rather than sniffing `CTYPE` strings.

Nothing here knows about ds9 or about regions; `ds9_regions` builds on it.
"""

import math

import numpy as np
from astropy.wcs.utils import proj_plane_pixel_scales


class CelestialMap:
    """The correspondence between orig `(x, y)` coordinates and a celestial WCS.

    `x_axis` and `y_axis` are 0-based FITS axis indices — the axes the viewer currently shows
    as X and Y, which is what `ImageViewer.display_axis_indices()` reports.

    Check `usable` before anything else: a map is unusable when there is no WCS, when it has no
    celestial axes, or when a displayed axis is not one of them. `reason` then says which, in
    words fit for a dialog.
    """

    def __init__(self, wcs, x_axis=0, y_axis=1):
        self.wcs = wcs
        self.x_axis = int(x_axis)
        self.y_axis = int(y_axis)
        self.wcs2d = None
        self.swapped = False
        self.reason = ""

        self._build()

    def _build(self):
        if self.wcs is None:
            self.reason = "the file has no WCS"
            return

        lng, lat = getattr(self.wcs.wcs, 'lng', -1), getattr(self.wcs.wcs, 'lat', -1)
        if lng < 0 or lat < 0 or lng == lat:
            self.reason = "the WCS has no celestial (RA/Dec) axes"
            return

        if {self.x_axis, self.y_axis} != {lng, lat}:
            shown = f"FITS axes {self.x_axis + 1} and {self.y_axis + 1}"
            celestial = f"axes {lng + 1} and {lat + 1}"
            self.reason = (f"the display shows {shown}, but the celestial axes are {celestial}"
                           " — this plane has no sky coordinates")
            return

        try:
            # Reorders so pixel axis 0 is longitude and axis 1 latitude, whatever the file's
            # own axis order was. This is the object `regions` needs for to_sky/to_pixel.
            self.wcs2d = self.wcs.sub(['longitude', 'latitude'])
        except Exception as exc:            # a malformed WCS can raise from wcslib
            self.reason = f"the celestial WCS could not be extracted ({exc})"
            self.wcs2d = None
            return

        # orig X is the latitude axis when the AXIS combos have them the other way round.
        self.swapped = (self.x_axis == lat)

    @property
    def usable(self):
        return self.wcs2d is not None

    # ------------------------------------------------------- orig <-> sub-WCS pixels

    def to_wcs_pixels(self, x, y):
        """Orig `(x, y)` as pixel coordinates in `wcs2d`'s axis order (longitude first)."""
        return (float(y), float(x)) if self.swapped else (float(x), float(y))

    def from_wcs_pixels(self, px, py):
        """The inverse: `wcs2d` pixel coordinates back to orig `(x, y)`."""
        return (float(py), float(px)) if self.swapped else (float(px), float(py))

    # ---------------------------------------------------------------- orig <-> sky

    def to_sky(self, x, y):
        """Orig `(x, y)` to `(ra_deg, dec_deg)`, or None if this map is unusable."""
        if not self.usable:
            return None
        px, py = self.to_wcs_pixels(x, y)
        ra, dec = self.wcs2d.pixel_to_world_values(px, py)
        ra, dec = float(ra), float(dec)
        if not (math.isfinite(ra) and math.isfinite(dec)):
            return None
        return (ra % 360.0, dec)

    def from_sky(self, ra_deg, dec_deg):
        """`(ra_deg, dec_deg)` to orig `(x, y)`, or None if unusable or off the projection."""
        if not self.usable:
            return None
        px, py = self.wcs2d.world_to_pixel_values(float(ra_deg), float(dec_deg))
        px, py = float(px), float(py)
        if not (math.isfinite(px) and math.isfinite(py)):
            # A position outside the projection's valid range comes back as NaN rather than
            # raising, which is the trap this module exists to contain.
            return None
        return self.from_wcs_pixels(px, py)

    def pixel_scale_arcsec(self):
        """Arcseconds per pixel, or None. The mean of the two axes if they differ slightly."""
        if not self.usable:
            return None
        try:
            scales = proj_plane_pixel_scales(self.wcs2d)
        except Exception:
            return None
        scales = np.asarray(scales, dtype=float) * 3600.0
        if not np.all(np.isfinite(scales)) or np.any(scales <= 0):
            return None
        return float(scales.mean())

    def arcsec_to_pixels(self, arcsec):
        scale = self.pixel_scale_arcsec()
        if scale is None or arcsec is None:
            return None
        return float(arcsec) / scale

    def pixels_to_arcsec(self, pixels):
        scale = self.pixel_scale_arcsec()
        if scale is None or pixels is None:
            return None
        return float(pixels) * scale

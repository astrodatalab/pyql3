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

**The WCS's world coordinates are in the WCS's own celestial frame**, which is not always ICRS:
an OSIRIS cube declares `RADESYS = 'FK5'`, older data FK4, and some images are galactic.
`CelestialMap.frame` is that frame, and `skycoord` builds a coordinate in it, so a position can be
converted to another image's frame rather than having its numbers read there as if they were the
same thing (`BUGS.md` R10).

Nothing here knows about regions; `ds9_regions` builds on it. The one piece of ds9 here is the
table of its sky frame names, because a region file records the frame by that name.
"""

import math

import astropy.units as u
import numpy as np
from astropy.coordinates import SkyCoord
from astropy.wcs.utils import proj_plane_pixel_scales, skycoord_to_pixel, wcs_to_celestial_frame

#: ds9's sky frame names, mapped to the astropy frame each one means. This mirrors the `regions`
#: package's own `regions.io.ds9.core.ds9_frame_map` and must keep mirroring it: a hand-parsed
#: arrow and a library-parsed circle written on the same coordinates have to land on the same
#: pixel, which `tests/test_ds9_regions.py::test_our_frame_table_matches_the_librarys` checks.
DS9_SKY_FRAMES = {
    "icrs": "icrs",
    "fk5": "fk5",
    "j2000": "fk5",
    "fk4": "fk4",
    "b1950": "fk4",
    "galactic": "galactic",
    "ecliptic": "barycentricmeanecliptic",
}

#: The inverse: one ds9 name for each astropy frame, the first listed above (`icrs`, `fk5`, `fk4`,
#: `galactic`, `ecliptic`).
DS9_FRAME_NAMES = {}
for _name, _frame in DS9_SKY_FRAMES.items():
    DS9_FRAME_NAMES.setdefault(_frame, _name)
del _name, _frame

#: How far, in pixels, the direction and length methods step along a direction. Small enough that
#: the projection is linear over it; large enough to be resolved in degrees: at 36 mas per pixel
#: the step is 1e-7 degrees, against a rounding error near 1e-13 degrees in a coordinate of 266.
DIRECTION_STEP = 0.01


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
        #: The WCS's celestial frame as an astropy frame, or None when unusable or unrecognised.
        self.frame = None

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

        try:
            self.frame = wcs_to_celestial_frame(self.wcs2d)
        except Exception:                   # a celestial axis type astropy has no frame for
            self.frame = None

    @property
    def usable(self):
        return self.wcs2d is not None

    @property
    def ds9_frame(self):
        """The ds9 name of the WCS's celestial frame (`icrs`, `fk5`, `fk4`, `galactic`,
        `ecliptic`), or None when the frame is unknown or has no ds9 name.

        Chosen by the kind of frame, as the `regions` package chooses the name it writes: an FK5
        WCS with an equinox other than J2000 is still called `fk5`.
        """
        if self.frame is None:
            return None
        return DS9_FRAME_NAMES.get(self.frame.name)

    def skycoord(self, lon_deg, lat_deg):
        """A `SkyCoord` for the WCS's own world coordinates, in the WCS's frame, or None."""
        if self.frame is None:
            return None
        return SkyCoord(float(lon_deg), float(lat_deg), unit="deg", frame=self.frame)

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
        """`(ra_deg, dec_deg)` to orig `(x, y)`, or None if unusable or off the projection.

        The numbers are the WCS's own world coordinates. Anything that might be in another
        frame — galactic, B1950 — belongs in `from_skycoord`, which converts first.
        """
        if not self.usable:
            return None
        px, py = self.wcs2d.world_to_pixel_values(float(ra_deg), float(dec_deg))
        return self._orig_pixel(px, py)

    def from_skycoord(self, coord):
        """A `SkyCoord` in *any* frame to orig `(x, y)`, or None.

        `skycoord_to_pixel` transforms the coordinate into the WCS's own celestial frame before
        projecting it, so a galactic or B1950 position lands where it belongs rather than where
        its numbers would fall if they were read as RA/Dec. This is the same path the `regions`
        package takes for a sky shape, which is what keeps a hand-parsed arrow and a
        library-parsed circle on the same pixel.
        """
        if not self.usable or coord is None:
            return None
        try:
            px, py = skycoord_to_pixel(coord, self.wcs2d)
        except Exception:       # an un-transformable frame, or a WCS wcslib will not invert
            return None
        return self._orig_pixel(px, py)

    def _orig_pixel(self, px, py):
        """A `wcs2d` pixel pair as orig `(x, y)`, or None if it is not a real position."""
        px, py = float(px), float(py)
        if not (math.isfinite(px) and math.isfinite(py)):
            # A position outside the projection's valid range comes back as NaN rather than
            # raising, which is the trap this module exists to contain.
            return None
        return self.from_wcs_pixels(px, py)

    # ------------------------------------------------------------ directions and lengths

    def to_wcs_angle(self, theta):
        """An angle measured from orig +X, measured instead from `wcs2d`'s pixel +X.

        Exchanging the two axes is a reflection about the diagonal, under which a direction θ
        becomes 90 - θ (`BUGS.md` R38). Only a shape handed to the `regions` package in `wcs2d`
        pixels needs this; the direction methods below work in orig coordinates throughout.
        """
        theta = float(theta)
        return (90.0 - theta) % 360.0 if self.swapped else theta

    def from_wcs_angle(self, theta):
        """The inverse of `to_wcs_angle`, which is the same reflection."""
        return self.to_wcs_angle(theta)

    def direction_to_sky(self, x, y, angle_deg):
        """The ds9 sky angle of a direction leaving orig `(x, y)` at `angle_deg` CCW from +X.

        That is the position angle (east of north, in the WCS's own frame) of a point a short step
        along the direction, plus 90 degrees, which is how ds9 measures an angle in a sky frame.
        The direction itself is mapped, not a shape: a rectangle probe returned the principal
        axis of the mapped rectangle, which is not the direction once the pixels are not square
        (`BUGS.md` R37). None if either point has no sky position.
        """
        pair = self._step_along(x, y, angle_deg)
        if pair is None:
            return None
        tail, ahead = pair
        return (float(tail.position_angle(ahead).deg) + 90.0) % 360.0

    def direction_from_sky(self, coord, sky_angle_deg):
        """The inverse of `direction_to_sky`: an orig angle CCW from +X, or None.

        `coord` is where the direction starts, as a `SkyCoord` in any frame; the angle is measured
        in that frame, as ds9 measures it in the frame a file declares.
        """
        moved = self._step_from(coord, sky_angle_deg)
        if moved is None:
            return None
        (x0, y0), (x1, y1) = moved[0], moved[1]
        return math.degrees(math.atan2(y1 - y0, x1 - x0)) % 360.0

    def length_to_sky(self, x, y, angle_deg, pixels):
        """`pixels` along the direction `angle_deg` from orig `(x, y)`, as an `Angle` on the sky.

        Scaled from the separation over a short step in that direction, so with unequal pixel
        scales a length depends on which way it points. None if there is no sky position.
        """
        pair = self._step_along(x, y, angle_deg)
        if pair is None:
            return None
        tail, ahead = pair
        return tail.separation(ahead) * (float(pixels) / DIRECTION_STEP)

    def length_from_sky(self, coord, sky_angle_deg, angle):
        """The inverse of `length_to_sky`: an `Angle` along a sky direction, in orig pixels."""
        moved = self._step_from(coord, sky_angle_deg)
        if moved is None:
            return None
        (x0, y0), (x1, y1) = moved[0], moved[1]
        step = moved[2]
        return float((angle / step).decompose()) * math.hypot(x1 - x0, y1 - y0)

    def _step_along(self, x, y, angle_deg):
        """`(tail, ahead)` as `SkyCoord`s in the WCS's frame, `DIRECTION_STEP` px apart, or None."""
        if self.frame is None:
            return None
        radians = math.radians(float(angle_deg))
        tail = self.to_sky(x, y)
        ahead = self.to_sky(float(x) + DIRECTION_STEP * math.cos(radians),
                            float(y) + DIRECTION_STEP * math.sin(radians))
        if tail is None or ahead is None:
            return None
        return self.skycoord(*tail), self.skycoord(*ahead)

    def _step_from(self, coord, sky_angle_deg):
        """`(start, ahead, step)`: orig positions of `coord` and of a point a short step from it
        along the ds9 sky angle, and that step as an `Angle`; or None."""
        scale = self.pixel_scale_arcsec()
        if coord is None or scale is None:
            return None
        step = DIRECTION_STEP * scale * u.arcsec
        start = self.from_skycoord(coord)
        ahead = self.from_skycoord(
            coord.directional_offset_by((float(sky_angle_deg) - 90.0) * u.deg, step))
        if start is None or ahead is None:
            return None
        return start, ahead, step

    # ---------------------------------------------------------------------- scales

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

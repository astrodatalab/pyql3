"""Reading and writing region files, in whichever of the two formats is in front of us.

`regions_model` owns the native YAML format and `ds9_regions` owns `.reg`; this is the thin layer
that picks between them and fills in the sky positions. It exists so the GUI has one call for
"open this file" and one for "save to that file", and so the choice is made by looking at the
*content* rather than trusting a suffix — a `.reg` file with the wrong extension is a normal thing
to be handed.

This is also where the sky anchor decided in `TODO_regions.md` gets written: geometry stays in
pixels, and the sky position is recorded alongside it whenever a WCS allows, so a saved file still
means something on another frame of the same field. The live regions are left alone — the anchors
are computed into copies for writing.

**The anchor is read back, not merely written.** A region saved as `frame: sky` is placed from its
RA/Dec when the file is loaded, exactly as a ds9 file in `fk5` is: draw on one dither, load on the
next, and the regions land on the same stars instead of the same detector pixels. `frame: image`
keeps the stored pixels, which is what a detector artifact wants. Whichever happens, and whichever
cannot, is said in the returned `Report` — a region that moved 20 px because the pointing changed
is not something to discover by eye.
"""

import math
from dataclasses import dataclass, replace
from pathlib import Path

from astropy.coordinates import SkyCoord

from pyql3.core.ds9_regions import Report, from_ds9, pixel_angle_to_sky, sky_angle_to_pixel, to_ds9
from pyql3.core.regions_model import (
    Arrow,
    Box,
    Circle,
    RegionList,
    SkyAnchor,
    geometry_of,
    looks_like_ds9,
)
from pyql3.core.sky import DS9_SKY_FRAMES, CelestialMap

#: Suffix that selects the ds9 format on save. Everything else is written as native YAML.
DS9_SUFFIXES = (".reg",)

#: How close two versions of a region have to be, in pixels, to count as the same one. Below this
#: a "move" is only the WCS round trip's floating point, and reporting it would cry wolf on every
#: file loaded back onto the image it was drawn on.
SAME_PLACE = 0.01

#: For a file dialog, in the order they should be offered.
FILE_FILTERS = (
    "Region files (*.yml *.yaml *.reg)",
    "QuickLook 3 regions (*.yml *.yaml)",
    "ds9 regions (*.reg)",
    "All files (*)",
)


@dataclass
class FrameChoice:
    """What a file offers when it holds the same regions in both frames, for the user to pick from.

    Handed to the `choose_frame` hook so the question can say what is actually at stake — "20
    pixels apart" is a decision; "which frame?" on its own is a riddle.
    """

    #: How many regions would be placed differently by the two frames.
    regions: int
    #: The largest of those differences, in pixels.
    furthest: float
    #: The frame the file was saved with, which is the sensible default. Only `frame: sky`
    #: regions are ever offered, so this is `"sky"`.
    saved: str = "sky"

    def summary(self):
        return (f"{self.regions} region(s) sit up to {self.furthest:.1f} pixel(s) apart in the "
                "two frames")


def load_regions(path, wcs=None, axis_indices=(0, 1), frame="auto", choose_frame=None):
    """Read a region file. Returns `(RegionList, Report | None)`.

    The format is chosen by sniffing the text, not the suffix. A ds9 file always returns a report,
    even an empty one, because the conversion can always lose something; a native file returns one
    only when there is something to say — which includes regions that moved to follow their sky
    positions onto this image. Raises `RegionFormatError` for an unreadable file, or `OSError` if
    it cannot be read at all.

    A native file written with a WCS holds each region **in both frames** — pixels and RA/Dec —
    and on a different pointing the two disagree. `frame` settles it: `"image"` or `"sky"` chooses
    outright, and `"auto"` places each region by its own `frame:`, asking `choose_frame(FrameChoice)`
    about the `frame: sky` ones and placing them from the sky when there is no one to ask. A
    `frame: image` region keeps its pixels and is not part of the question. The question is only
    put when the two frames would actually place a `frame: sky` region differently.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    if looks_like_ds9(text):
        return from_ds9(text, wcs=wcs, axis_indices=axis_indices, where=path.name)

    region_list = RegionList.from_yaml(text, where=path.name)
    regions, report = placed_on_image(region_list.regions, wcs=wcs, axis_indices=axis_indices,
                                      frame=frame, choose_frame=choose_frame)
    return replace(region_list, regions=regions), (report or None)


def placed_on_image(regions, wcs=None, axis_indices=(0, 1), frame="auto", choose_frame=None):
    """Regions put on this image in the frame that was chosen. Returns `(regions, Report)`.

    A region carries pixels always and a sky anchor when one could be recorded, so most files hold
    both frames at once and `frame` decides between them — see `load_regions`. Anything that cannot
    honour a sky choice — no WCS on this file, a plane with no sky coordinates, a position off the
    projection — keeps its stored pixels and is reported rather than quietly left where it does not
    belong.
    """
    report = Report()
    anchored = [region for region in regions if region.sky is not None]
    if not anchored or frame == "image":
        return list(regions), report

    mapping = CelestialMap(wcs, *axis_indices)
    if not mapping.usable:
        if frame == "sky" or any(region.frame == "sky" for region in anchored):
            report.notes.append(
                f"{len(anchored)} region(s) carry sky positions, but {mapping.reason}; they were "
                "placed at the pixel coordinates stored alongside")
        return list(regions), report

    # Both placements first, so the choice can be described before it is put — and skipped when
    # the two agree, which is every file loaded back onto the image it was drawn on.
    candidates = {}
    stranded = 0
    for index, region in enumerate(regions):
        if region.sky is None:
            continue
        try:
            on_image, shift = _from_sky_anchor(region, mapping)
        except Exception:       # a conversion astropy or wcslib refuses; the pixels still stand
            on_image, shift = None, 0.0
        if on_image is None:
            stranded += 1
        elif not _same_geometry(on_image, region):
            candidates[index] = (on_image, shift)

    if stranded:
        report.notes.append(
            f"{stranded} region(s) have a sky position that does not project onto this image; "
            "they were placed at the pixel coordinates stored alongside")

    if not candidates:
        # The frames agree. Keeping the file's own numbers rather than numbers that went out
        # through the WCS and back means a save/load cycle is exact instead of drifting by a WCS
        # round trip's worth of floating point each time.
        return list(regions), report

    moving = _regions_to_move(frame, choose_frame, regions, candidates)
    kept = [shift for index, (_, shift) in candidates.items() if index not in moving]
    if kept:
        report.notes.append(
            f"{len(kept)} region(s) were kept at their saved pixel coordinates, which this "
            f"image puts up to {max(kept):.1f} pixel(s) from their saved sky positions")
    if not moving:
        return list(regions), report

    placed = [moving[index][0] if index in moving else region
              for index, region in enumerate(regions)]
    report.notes.append(
        f"{len(moving)} region(s) were placed from their sky positions rather than their "
        f"stored pixels, moving up to {max(shift for _, shift in moving.values()):.1f} "
        "pixel(s): this file was drawn on an image pointed differently from this one")
    return placed, report


def _regions_to_move(frame, choose_frame, regions, candidates):
    """The candidates to place from their sky positions, as a subset of `candidates`.

    `frame="sky"` moves them all. Otherwise each region goes by its own `frame:` (`BUGS.md` R40):
    a `frame: image` region, or one whose frame was never chosen, keeps its pixels, and the user is
    asked only about the `frame: sky` ones. With no one to ask — a command-line load, a test,
    anything headless — the file's own `frame:` stands, so a script never blocks.
    """
    if frame == "sky":
        return dict(candidates)

    following = {index: candidate for index, candidate in candidates.items()
                 if regions[index].frame == "sky"}
    if not following or choose_frame is None:
        return following

    offer = FrameChoice(regions=len(following),
                        furthest=max(shift for _, shift in following.values()))
    return {} if choose_frame(offer) == "image" else following


def _from_sky_anchor(region, mapping):
    """`(region moved onto this image, how far it moved)`, or `(None, 0)` if it cannot be placed.

    The exact inverse of `sky_anchor_for` below, and it has to stay that way: whatever that one
    records is what this one can restore, and a size or angle it does not record keeps the value
    stored in pixels.
    """
    anchor = region.sky
    if _in_this_frame(anchor, mapping):
        # The numbers are this WCS's own world coordinates, so they are placed as they are: no
        # conversion, and no `SkyCoord` to build unless an angle needs one, which for a large
        # set of circles is most of the cost of loading.
        coord = None
        placed = mapping.from_sky(anchor.ra_deg, anchor.dec_deg)
    else:
        coord = _anchor_coord(anchor, mapping)
        placed = mapping.from_skycoord(coord)
    if placed is None:
        return None, 0.0

    x, y = placed
    values = {"x": x, "y": y}
    if isinstance(region, (Box, Arrow)) and coord is None:
        coord = _anchor_coord(anchor, mapping)

    if isinstance(region, Circle):
        _set_size(values, "radius", anchor.size_arcsec, mapping)
    elif isinstance(region, Box):
        _set_angle(values, coord, anchor, mapping)
        angle = values.get("angle", region.angle)
        _set_length(values, "width", angle, anchor.size_arcsec, mapping)
        _set_length(values, "height", angle + 90.0, anchor.size2_arcsec, mapping)
    elif isinstance(region, Arrow):
        _set_angle(values, coord, anchor, mapping)
        _set_length(values, "length", values.get("angle", region.angle), anchor.size_arcsec,
                    mapping)

    moved = replace(region, **values)
    # Placed from the anchor, so this is the geometry the anchor goes with (R41).
    moved.anchor_geometry = geometry_of(moved)
    return moved, math.hypot(x - region.x, y - region.y)


def _in_this_frame(anchor, mapping):
    """True when the anchor's numbers are in this WCS's own frame: it names that frame, or none."""
    return anchor.frame is None or anchor.frame == mapping.ds9_frame


def _anchor_coord(anchor, mapping):
    """The anchor's position as a `SkyCoord` in the frame its numbers are in, or None.

    An anchor in this image's own frame, or one that names no frame, is built in the WCS's frame
    exactly, so loading a file back onto its own image converts nothing (`BUGS.md` R10). Any other
    frame is the ds9 frame of that name, which `from_skycoord` converts into this WCS's frame.
    """
    if _in_this_frame(anchor, mapping):
        return mapping.skycoord(anchor.ra_deg, anchor.dec_deg)
    return SkyCoord(float(anchor.ra_deg), float(anchor.dec_deg), unit="deg",
                    frame=DS9_SKY_FRAMES[anchor.frame])


def _same_geometry(one, other):
    """True when two versions of a region sit within `SAME_PLACE` of each other.

    Compares the geometry fields (`GEOMETRY`), so it is a circle's radius and a box's angle as
    well as the position, and a new shape needs nothing added here.
    """
    return _same_values(type(one).GEOMETRY, geometry_of(one), geometry_of(other))


def _same_values(names, values, others):
    """True when two geometry tuples agree within `SAME_PLACE`, angles as directions.

    An angle is compared modulo 360: a WCS round trip that ends in `% 360` turns -6e-15 into
    359.99999999999994, which is the same direction as 0 and not 360 degrees from it
    (`BUGS.md` R39).
    """
    for name, value, other in zip(names, values, others, strict=True):
        if name == "angle":
            difference = abs((value - other + 180.0) % 360.0 - 180.0)
        else:
            difference = abs(value - other)
        if not difference <= SAME_PLACE:
            return False
    return True


def _anchor_is_stale(region):
    """True when a region has been edited since its sky anchor was recorded or placed (R41).

    A region whose pairing is not known (`anchor_geometry` None) is taken to be unedited.
    """
    if region.anchor_geometry is None:
        return False
    return not _same_values(region.GEOMETRY, region.anchor_geometry, geometry_of(region))


def _set_size(values, name, arcsec, mapping):
    """A size recorded in arcseconds, as pixels on this image.

    A size the anchor never recorded, or one this WCS cannot scale, leaves the stored pixel value
    standing — a region in the right place at its old size beats no region at all.
    """
    pixels = mapping.arcsec_to_pixels(arcsec)
    if pixels is not None and pixels > 0:
        values[name] = float(pixels)


def _set_length(values, name, angle, arcsec, mapping):
    """A length recorded in arcseconds along the orig direction `angle`, as pixels on this image.

    Measured along the direction at the region's new position, because with unequal pixel scales
    a length in pixels depends on which way it points (`BUGS.md` R37). As in `_set_size`, a length
    that cannot be converted leaves the stored pixel value standing.
    """
    if arcsec is None:
        return
    per_pixel = mapping.length_to_sky(values["x"], values["y"], angle, 1.0)
    if per_pixel is None or per_pixel.arcsec <= 0:
        # No direction to measure along (a frame astropy does not recognise): the mean scale.
        _set_size(values, name, arcsec, mapping)
        return
    values[name] = float(arcsec) / float(per_pixel.arcsec)


def _set_angle(values, coord, anchor, mapping):
    """A direction recorded against the sky at `coord`, as an image angle here — zero included.

    A field rotated differently from the one the region was drawn on turns the region with it,
    which is the whole point of recording the angle against the sky rather than the axes.
    """
    if anchor.angle_deg is None:
        return
    angle = sky_angle_to_pixel(mapping, coord, anchor.angle_deg)
    if angle is not None:
        values["angle"] = angle % 360.0


def save_regions(path, regions, wcs=None, axis_indices=(0, 1), written_by="", source="",
                 frame="auto", style=None, record=False):
    """Write `regions` to `path`, choosing the format from its suffix. Returns `Report | None`.

    A `.reg` suffix writes ds9 format — `frame` then selects image or sky coordinates, and the
    returned report says what could not be carried across. Anything else writes native YAML, with
    sky anchors filled in where possible and nothing lost.

    `frame` means the same thing to both formats: `"sky"` writes positions that follow the field,
    `"image"` writes pixels of this file, and `"auto"` — the default — writes sky wherever it can,
    which for the native format is wherever the plane on display has sky coordinates at all.

    `style` is the native file's `style:` block — the one the regions were loaded with, so a
    re-saved file keeps it (`BUGS.md` R77). None writes the built-in defaults.

    `record=True` copies what a native save wrote — each region's sky anchor, frame and
    `anchor_geometry` — back onto the regions passed in, once the file is written (`record_saved`).
    The window asks for it, so a later save compares each region with the anchor its last save
    recorded rather than the one it was loaded with. Without it the regions are left alone.
    """
    path = Path(path)
    regions = list(regions)

    if path.suffix.lower() in DS9_SUFFIXES:
        text, report = to_ds9(RegionList(regions=regions), wcs=wcs, axis_indices=axis_indices,
                              frame=frame, written_by=written_by)
        path.write_text(text, encoding="utf-8")
        return report

    written = with_sky_anchors(regions, wcs=wcs, axis_indices=axis_indices, frame=frame)
    RegionList(regions=written, written_by=written_by, source=source, style=style).save(path)
    if record:
        record_saved(regions, written)
    return None


def record_saved(regions, written):
    """Give each region the sky anchor, frame and `anchor_geometry` its saved copy was written with.

    `written` is what `with_sky_anchors(regions, ...)` returned, in the same order. Without this, a
    region dragged and then saved with a WCS kept the anchor it was loaded with, so a second save
    on a plane with no sky axes counted it as moved and dropped the anchor the first save had
    recorded correctly.
    """
    for region, copy in zip(regions, written, strict=True):
        if copy is region:
            continue
        region.sky = copy.sky
        region.frame = copy.frame
        region.anchor_geometry = copy.anchor_geometry


def with_sky_anchors(regions, wcs=None, axis_indices=(0, 1), frame="auto"):
    """Copies of `regions` carrying their sky position, where the WCS allows one.

    The anchor is what makes a file meaningful on another frame of the same field. A region whose
    frame was never chosen is marked `frame: sky` when it gets one, so that loading it somewhere
    else puts it back on the sky rather than on the old pixels; an explicit `frame: image` is kept
    (`BUGS.md` R40), and `frame="image"` or `"sky"` marks them all. A plane with no sky coordinates at
    all (wavelength against declination, say) records no new anchors.

    **The anchor is recomputed, not preserved.** What the user sees and drags is the pixel
    geometry, so an anchor carried in from a file is stale the moment a region is moved; writing
    it back would put the region somewhere it has not been since. A stored one is kept only when
    this image cannot produce a new one *and* the region has not been edited since the anchor was
    recorded (`anchor_geometry`); an edited region loses it, and its frame becomes unchosen
    (written `frame: image`), so its new pixels are what the next load uses and the next save
    with a WCS anchors it there (R41).
    """
    mapping = CelestialMap(wcs, *axis_indices)

    anchored = []
    for region in regions:
        anchor = sky_anchor_for(region, mapping) if mapping.usable else None
        if anchor is not None:
            if frame in ("image", "sky"):
                chosen = frame
            else:
                chosen = "image" if region.frame == "image" else "sky"
            anchored.append(replace(region, sky=anchor, frame=chosen,
                                    anchor_geometry=geometry_of(region)))
        elif region.sky is not None and _anchor_is_stale(region):
            # Not "image": that would pin the region to the detector for good, which nobody
            # asked for. With no sky block it is written `frame: image` and read back as unchosen.
            anchored.append(replace(region, sky=None, frame=None, anchor_geometry=None))
        else:
            anchored.append(region)
    return anchored


def sky_anchor_for(region, mapping):
    """The `SkyAnchor` for one region, or None if it has no sky position."""
    sky = mapping.to_sky(region.x, region.y)
    if sky is None:
        return None

    size = size2 = angle = None
    if isinstance(region, Circle):
        size = mapping.pixels_to_arcsec(region.radius)
    elif isinstance(region, Box):
        # The width lies along the box's angle and the height across it, and each is measured on
        # the sky in its own direction (`BUGS.md` R37).
        size = _arcsec_along(mapping, region, region.angle, region.width)
        size2 = _arcsec_along(mapping, region, region.angle + 90.0, region.height)
        angle = pixel_angle_to_sky(mapping, region.x, region.y, region.angle)
    elif isinstance(region, Arrow):
        size = _arcsec_along(mapping, region, region.angle, region.length)
        angle = pixel_angle_to_sky(mapping, region.x, region.y, region.angle)

    return SkyAnchor(ra_deg=sky[0], dec_deg=sky[1], size_arcsec=size, size2_arcsec=size2,
                     angle_deg=angle, frame=mapping.ds9_frame)


def _arcsec_along(mapping, region, angle, pixels):
    """`pixels` along the orig direction `angle` from the region's centre, in arcseconds, or None.

    Falls back to the mean pixel scale when there is no direction to measure along, which is the
    case for a celestial frame astropy does not recognise.
    """
    length = mapping.length_to_sky(region.x, region.y, angle, pixels)
    if length is None:
        return mapping.pixels_to_arcsec(pixels)
    return float(length.arcsec)


def suggested_filename(source_path, suffix=".yml"):
    """A region filename beside the FITS file it was drawn on, for a Save dialog's default."""
    if not source_path:
        return f"regions{suffix}"
    return str(Path(source_path).with_suffix("").name) + f"_regions{suffix}"

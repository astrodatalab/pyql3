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
from dataclasses import dataclass, fields, replace
from pathlib import Path

from pyql3.core.ds9_regions import Report, from_ds9, pixel_angle_to_sky, sky_angle_to_pixel, to_ds9
from pyql3.core.regions_model import (
    COMMON_ORDER,
    Arrow,
    Box,
    Circle,
    RegionList,
    SkyAnchor,
    looks_like_ds9,
)
from pyql3.core.sky import CelestialMap

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
    #: The frame the file was saved with, which is the sensible default.
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
    outright, and `"auto"` asks `choose_frame(FrameChoice)` for the answer, falling back to what
    each region was saved as when there is no one to ask. The question is only put when the two
    frames would actually place something differently.
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
        on_image, shift = _from_sky_anchor(region, mapping)
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

    chosen = _chosen_frame(frame, choose_frame, regions, candidates, anchored)
    if chosen == "image":
        report.notes.append(
            f"{len(candidates)} region(s) were kept at their saved pixel coordinates, which this "
            f"image puts up to {max(shift for _, shift in candidates.values()):.1f} pixel(s) from "
            "their saved sky positions")
        return list(regions), report

    placed = [candidates[index][0] if index in candidates else region
              for index, region in enumerate(regions)]
    report.notes.append(
        f"{len(candidates)} region(s) were placed from their sky positions rather than their "
        f"stored pixels, moving up to {max(shift for _, shift in candidates.values()):.1f} "
        "pixel(s): this file was drawn on an image pointed differently from this one")
    return placed, report


def _chosen_frame(frame, choose_frame, regions, candidates, anchored):
    """Which frame to place the regions in: what was asked for, what the user says, or the file's.

    With no one to ask — a command-line load, a test, anything headless — the file's own `frame:`
    stands, so a script never blocks and never surprises.
    """
    if frame in ("image", "sky"):
        return frame

    saved = "sky" if any(region.frame == "sky" for region in anchored) else "image"
    if choose_frame is None:
        return saved

    offer = FrameChoice(regions=len(candidates),
                        furthest=max(shift for _, shift in candidates.values()),
                        saved=saved)
    answer = choose_frame(offer)
    return answer if answer in ("image", "sky") else saved


def _from_sky_anchor(region, mapping):
    """`(region moved onto this image, how far it moved)`, or `(None, 0)` if it cannot be placed.

    The exact inverse of `sky_anchor_for` below, and it has to stay that way: whatever that one
    records is what this one can restore, and a size or angle it does not record keeps the value
    stored in pixels.
    """
    anchor = region.sky
    placed = mapping.from_sky(anchor.ra_deg, anchor.dec_deg)
    if placed is None:
        return None, 0.0

    x, y = placed
    values = {"x": x, "y": y}

    if isinstance(region, Circle):
        _set_size(values, "radius", anchor.size_arcsec, mapping)
    elif isinstance(region, Box):
        _set_size(values, "width", anchor.size_arcsec, mapping)
        _set_size(values, "height", anchor.size2_arcsec, mapping)
        _set_angle(values, anchor, mapping)
    elif isinstance(region, Arrow):
        _set_size(values, "length", anchor.size_arcsec, mapping)
        _set_angle(values, anchor, mapping)

    return replace(region, **values), math.hypot(x - region.x, y - region.y)


def _same_geometry(one, other):
    """True when two versions of a region sit within `SAME_PLACE` of each other.

    Compares the geometry fields — whatever a region has beyond the shared styling — so it is a
    circle's radius and a box's angle as well as the position, and a new shape needs nothing added
    here.
    """
    names = [field.name for field in fields(one) if field.name not in COMMON_ORDER]
    return all(math.isclose(float(getattr(one, name)), float(getattr(other, name)),
                            abs_tol=SAME_PLACE)
               for name in names)


def _set_size(values, name, arcsec, mapping):
    """A size recorded in arcseconds, as pixels on this image.

    A size the anchor never recorded, or one this WCS cannot scale, leaves the stored pixel value
    standing — a region in the right place at its old size beats no region at all.
    """
    pixels = mapping.arcsec_to_pixels(arcsec)
    if pixels is not None and pixels > 0:
        values[name] = float(pixels)


def _set_angle(values, anchor, mapping):
    """A direction recorded against the sky, as an image angle here — zero included.

    A field rotated differently from the one the region was drawn on turns the region with it,
    which is the whole point of recording the angle against the sky rather than the axes.
    """
    if anchor.angle_deg is not None:
        values["angle"] = sky_angle_to_pixel(mapping, anchor.ra_deg, anchor.dec_deg,
                                             anchor.angle_deg) % 360.0


def save_regions(path, regions, wcs=None, axis_indices=(0, 1), written_by="", source="",
                 frame="auto"):
    """Write `regions` to `path`, choosing the format from its suffix. Returns `Report | None`.

    A `.reg` suffix writes ds9 format — `frame` then selects image or sky coordinates, and the
    returned report says what could not be carried across. Anything else writes native YAML, with
    sky anchors filled in where possible and nothing lost.

    `frame` means the same thing to both formats: `"sky"` writes positions that follow the field,
    `"image"` writes pixels of this file, and `"auto"` — the default — writes sky wherever it can,
    which for the native format is wherever the plane on display has sky coordinates at all.
    """
    path = Path(path)
    regions = list(regions)

    if path.suffix.lower() in DS9_SUFFIXES:
        text, report = to_ds9(RegionList(regions=regions), wcs=wcs, axis_indices=axis_indices,
                              frame=frame, written_by=written_by)
        path.write_text(text, encoding="utf-8")
        return report

    RegionList(regions=with_sky_anchors(regions, wcs=wcs, axis_indices=axis_indices, frame=frame),
               written_by=written_by, source=source).save(path)
    return None


def with_sky_anchors(regions, wcs=None, axis_indices=(0, 1), frame="auto"):
    """Copies of `regions` carrying their sky position, where the WCS allows one.

    The anchor is what makes a file meaningful on another frame of the same field, and unless
    `frame="image"` the copies are marked `frame: sky` so that loading them somewhere else puts
    them back on the sky rather than on the old pixels. A plane with no sky coordinates at all
    (wavelength against declination, say) simply yields the originals unchanged.

    **The anchor is recomputed, not preserved.** What the user sees and drags is the pixel
    geometry, so an anchor carried in from a file is stale the moment a region is moved; writing
    it back would put the region somewhere it has not been since. The stored one is kept only when
    this image cannot produce a new one.
    """
    mapping = CelestialMap(wcs, *axis_indices)
    if not mapping.usable:
        return list(regions)

    anchored = []
    for region in regions:
        anchor = sky_anchor_for(region, mapping)
        if anchor is None:
            anchored.append(region)
            continue
        anchored.append(replace(region, sky=anchor,
                                frame="image" if frame == "image" else "sky"))
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
        size = mapping.pixels_to_arcsec(region.width)
        size2 = mapping.pixels_to_arcsec(region.height)
        angle = pixel_angle_to_sky(mapping, region.x, region.y, region.angle)
    elif isinstance(region, Arrow):
        size = mapping.pixels_to_arcsec(region.length)
        angle = pixel_angle_to_sky(mapping, region.x, region.y, region.angle)

    return SkyAnchor(ra_deg=sky[0], dec_deg=sky[1], size_arcsec=size, size2_arcsec=size2,
                     angle_deg=angle)


def suggested_filename(source_path, suffix=".yml"):
    """A region filename beside the FITS file it was drawn on, for a Save dialog's default."""
    if not source_path:
        return f"regions{suffix}"
    return str(Path(source_path).with_suffix("").name) + f"_regions{suffix}"

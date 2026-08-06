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
"""

from dataclasses import replace
from pathlib import Path

from pyql3.core.ds9_regions import from_ds9, pixel_angle_to_sky, to_ds9
from pyql3.core.regions_model import (
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

#: For a file dialog, in the order they should be offered.
FILE_FILTERS = (
    "Region files (*.yml *.yaml *.reg)",
    "QuickLook 3 regions (*.yml *.yaml)",
    "ds9 regions (*.reg)",
    "All files (*)",
)


def load_regions(path, wcs=None, axis_indices=(0, 1)):
    """Read a region file. Returns `(RegionList, Report | None)`.

    The format is chosen by sniffing the text, not the suffix. `Report` is None for a native file,
    which by construction has nothing to report — it is only the ds9 conversion that can lose
    things. Raises `RegionFormatError` for an unreadable file, or `OSError` if it cannot be read
    at all.
    """
    path = Path(path)
    text = path.read_text(encoding="utf-8")

    if looks_like_ds9(text):
        return from_ds9(text, wcs=wcs, axis_indices=axis_indices, where=path.name)

    return RegionList.from_yaml(text, where=path.name), None


def save_regions(path, regions, wcs=None, axis_indices=(0, 1), written_by="", source="",
                 frame="auto"):
    """Write `regions` to `path`, choosing the format from its suffix. Returns `Report | None`.

    A `.reg` suffix writes ds9 format — `frame` then selects image or sky coordinates, and the
    returned report says what could not be carried across. Anything else writes native YAML, with
    sky anchors filled in where possible and nothing lost.
    """
    path = Path(path)
    regions = list(regions)

    if path.suffix.lower() in DS9_SUFFIXES:
        text, report = to_ds9(RegionList(regions=regions), wcs=wcs, axis_indices=axis_indices,
                              frame=frame, written_by=written_by)
        path.write_text(text, encoding="utf-8")
        return report

    RegionList(regions=with_sky_anchors(regions, wcs=wcs, axis_indices=axis_indices),
               written_by=written_by, source=source).save(path)
    return None


def with_sky_anchors(regions, wcs=None, axis_indices=(0, 1)):
    """Copies of `regions` carrying their sky position, where the WCS allows one.

    Pixel geometry stays authoritative; this is the record that makes a file meaningful on a
    different frame of the same field. Regions that already carry an anchor keep it, and a plane
    with no sky coordinates at all (wavelength against declination, say) simply yields the
    originals unchanged.
    """
    mapping = CelestialMap(wcs, *axis_indices)
    if not mapping.usable:
        return list(regions)

    anchored = []
    for region in regions:
        if region.sky is not None:
            anchored.append(region)
            continue
        anchor = sky_anchor_for(region, mapping)
        anchored.append(region if anchor is None else replace(region, sky=anchor))
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

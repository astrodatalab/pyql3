"""ds9 `.reg` import and export, built on the `regions` package.

`regions` owns the parts it does well — circle, box, text, line, every coordinate frame, the
1-based ds9 pixel convention, and the sky-frame angle conventions — and this module owns the
parts it cannot do. Both gaps were measured rather than assumed (see `TODO_regions.md`):

- **`vector(...)` is dropped silently on read** and has no class to write, so arrows are parsed
  out of the file text and appended to the output by hand. Every ds9 arrow is a `vector`.
- **`textangle` is read but never written**: it arrives as `visual['rotation']` and is missing
  from `serialize()` output, so a rotated label is re-attached by hand.

Two rules about the output were established against ds9 itself:

- A **bare `vector(...)` is a syntax error** that makes ds9 reject the whole file, so ours are
  always written `# vector(...) vector=1`.
- A **comment beginning with `-` is likewise fatal** (`-` is ds9's exclude prefix), so the
  provenance line is checked before it is written.

Everything this module cannot represent goes into a `Report` for the caller to show. Silent loss
is the specific failure this whole module is a reaction to.
"""

import math
import re
from dataclasses import dataclass, field

import astropy.units as u
from astropy.coordinates import SkyCoord
from regions import (
    CirclePixelRegion,
    LinePixelRegion,
    PixCoord,
    RectanglePixelRegion,
    RectangleSkyRegion,
    Regions,
    TextPixelRegion,
)

from pyql3.core.regions_model import Arrow, Box, Circle, RegionList, Text
from pyql3.core.sky import CelestialMap

#: ds9's own header line. `regions` writes its own variant, which ds9 also accepts.
DS9_HEADER = "# Region file format: DS9 version 4.1"

#: Frame names we accept for output. "auto" picks sky when image coordinates would not line up.
OUTPUT_FRAMES = ("auto", "image", "sky")

#: `vector(x, y, length, angle)`, with or without ds9's leading `#`, and any trailing
#: properties. Written by hand because `regions` has no vector at all.
_VECTOR_RE = re.compile(
    r"""^\s*(?:\#\s*)?vector\s*\(\s*
        ([-+0-9.eE]+)\s*,\s*([-+0-9.eE]+)\s*,\s*
        ([-+0-9.eE]+)\s*(?:"|'|d)?\s*,\s*([-+0-9.eE]+)\s*\)
        (?P<properties>.*)$""",
    re.VERBOSE | re.IGNORECASE)

#: A coordinate-system line, which applies to everything after it.
_FRAME_RE = re.compile(
    r"^\s*(image|physical|fk4|fk5|icrs|galactic|ecliptic|wcs[a-z]?)\s*$", re.IGNORECASE)

_SKY_FRAMES = ("fk4", "fk5", "icrs", "galactic", "ecliptic")

_TEXT_PROPERTY_RE = re.compile(r"text\s*=\s*\{([^}]*)\}")
_TAG_PROPERTY_RE = re.compile(r"tag\s*=\s*\{([^}]*)\}")
_COLOR_PROPERTY_RE = re.compile(r"color\s*=\s*(\S+)")
_WIDTH_PROPERTY_RE = re.compile(r"width\s*=\s*(\d+)")
_DASH_PROPERTY_RE = re.compile(r"dash\s*=\s*(\d+)")
_TEXTANGLE_RE = re.compile(r"textangle\s*=\s*([-+0-9.eE]+)")


@dataclass
class Report:
    """What could not be carried across, in words meant for a dialog.

    `skipped` is content that did not survive; `notes` is content that survived differently
    than asked. Empty means a clean conversion.
    """

    skipped: list = field(default_factory=list)
    notes: list = field(default_factory=list)

    def __bool__(self):
        """True when there is something the user should be told."""
        return bool(self.skipped or self.notes)

    def summary(self):
        lines = []
        if self.skipped:
            lines.append("Not converted:")
            lines += [f"  • {item}" for item in self.skipped]
        if self.notes:
            lines.append("Note:")
            lines += [f"  • {item}" for item in self.notes]
        return "\n".join(lines)


# ===================================================================== writing

def to_ds9(region_list, wcs=None, axis_indices=(0, 1), frame="auto", written_by=""):
    """Serialize regions as ds9 `.reg` text. Returns `(text, Report)`.

    `axis_indices` is the pair of 0-based FITS axis indices the viewer shows as X and Y
    (`ImageViewer.display_axis_indices()`). ds9's `image` frame always means FITS axes 1 and 2,
    so when the display shows anything else — an OSIRIS cube shows axes 3 and 2 — image
    coordinates would land somewhere else entirely in ds9. `frame="auto"` writes sky
    coordinates in that case, and says so in the report.
    """
    if frame not in OUTPUT_FRAMES:
        raise ValueError(f"frame must be one of {list(OUTPUT_FRAMES)}, not {frame!r}")

    report = Report()
    mapping = CelestialMap(wcs, *axis_indices)
    matches_ds9_axes = tuple(axis_indices) == (0, 1)

    use_sky = _choose_sky(frame, mapping, matches_ds9_axes, report)

    shapes, arrows, rotated_text = [], [], []
    for index, region in enumerate(region_list):
        if isinstance(region, Arrow):
            arrows.append(region)
            continue
        if isinstance(region, Text) and region.angle % 360.0:
            # Handled by hand below. Passing it to the library as well would write the label
            # twice, once without its rotation.
            rotated_text.append(region)
            continue
        shape = _to_regions_shape(region, index, report)
        if shape is not None:
            shapes.append(shape)

    if use_sky:
        shapes = _to_sky_shapes(shapes, mapping, report)

    if shapes:
        lines = Regions(shapes).serialize(format="ds9").splitlines()
    else:
        # Nothing for the library to write, but the frame still has to be declared for the
        # hand-written lines that follow.
        lines = [DS9_HEADER, "icrs" if use_sky else "image"]

    provenance = _provenance_line(written_by)
    if provenance:
        # After the header line, which is where ds9 puts its own preamble.
        lines.insert(1, provenance)

    lines += _rotated_text_lines(rotated_text, use_sky, mapping, report)
    lines += _arrow_lines(arrows, use_sky, mapping, report)

    return "\n".join(lines) + "\n", report


def _choose_sky(frame, mapping, matches_ds9_axes, report):
    """Decide between sky and image output, explaining anything surprising."""
    if frame == "sky":
        if mapping.usable:
            return True
        report.notes.append(
            f"asked for sky coordinates but {mapping.reason}; wrote image coordinates instead")
        return False

    if frame == "image":
        if not matches_ds9_axes:
            report.notes.append(
                "wrote image coordinates, but ds9's image frame means FITS axes 1 and 2 while "
                "this cube is displayed on other axes, so the regions will not line up in "
                "ds9 — export in sky coordinates for that")
        return False

    # auto
    if matches_ds9_axes:
        return False
    if mapping.usable:
        report.notes.append(
            "wrote sky coordinates: ds9's image frame means FITS axes 1 and 2, and this cube "
            "is displayed on different axes")
        return True

    report.notes.append(
        f"this cube is not displayed on FITS axes 1 and 2, and {mapping.reason}, so the image "
        "coordinates written here will not line up in ds9")
    return False


def _to_regions_shape(region, index, report):
    """One model region as a `regions` pixel object, or None if it has no equivalent."""
    visual, meta = _visual_and_meta(region)
    centre = PixCoord(region.x, region.y)

    if isinstance(region, Circle):
        return CirclePixelRegion(centre, region.radius, visual=visual, meta=meta)
    if isinstance(region, Box):
        return RectanglePixelRegion(centre, region.width, region.height,
                                    region.angle * u.deg, visual=visual, meta=meta)
    if isinstance(region, Text):
        # The label is the shape here, so it goes in `text` rather than in meta.
        meta.pop("text", None)
        return TextPixelRegion(centre, region.text, visual=visual, meta=meta)

    report.skipped.append(f"region {index}: {type(region).__name__} has no ds9 equivalent")
    return None


def _visual_and_meta(region):
    """Style and label as `regions` expects them.

    ds9's single `color=` becomes `facecolor`+`edgecolor` on a shape but `color` on text, which
    is how the library round-trips it; setting all three is harmless and covers both.
    """
    visual = {
        "color": region.color,
        "edgecolor": region.color,
        "facecolor": region.color,
        "linewidth": region.line_width,
    }
    if region.dash:
        visual["linestyle"] = "dashed"
    if isinstance(region, Text):
        visual["fontsize"] = region.font_size

    meta = {}
    if region.text:
        meta["text"] = region.text
    if region.tag:
        meta["tag"] = [region.tag]
    return visual, meta


def _to_sky_shapes(shapes, mapping, report):
    """Convert pixel shapes to sky, letting `regions` handle the angle conventions."""
    converted = []
    for shape in shapes:
        moved = _with_wcs_pixels(shape, mapping)
        try:
            converted.append(moved.to_sky(mapping.wcs2d))
        except Exception as exc:
            report.skipped.append(f"{type(shape).__name__} could not be put on the sky ({exc})")
    return converted


def _with_wcs_pixels(shape, mapping):
    """Return `shape` with its centre expressed in the celestial WCS's own axis order.

    Only differs from the original when the AXIS combos have X on the latitude axis.
    """
    if not mapping.swapped:
        return shape
    px, py = mapping.to_wcs_pixels(shape.center.x, shape.center.y)
    moved = shape.copy()
    moved.center = PixCoord(px, py)
    return moved


def _rotated_text_lines(rotated_text, use_sky, mapping, report):
    """`textangle` for rotated labels, which `regions` reads but does not write.

    Measured, not assumed: `TextPixelRegion(..., visual={'rotation': 30})` serializes with no
    `textangle` at all, so a rotated label loses its angle if the library writes it. Such labels
    are therefore kept out of the library's hands entirely (see `to_ds9`) and written here, in
    the `# text(...)` form ds9 itself uses.
    """
    lines = []
    for region in rotated_text:
        placed = _ds9_position(region.x, region.y, use_sky, mapping)
        if placed is None:
            report.skipped.append(f"text {region.text!r}: no sky position for its rotation")
            continue
        angle = region.angle % 360.0
        properties = [f"text={{{_escape_braces(region.text)}}}", f"textangle={angle:g}"]
        if region.color:
            properties.append(f"color={region.color}")
        properties.append(f'font="helvetica {int(region.font_size)} normal roman"')
        lines.append(f"# text({placed[0]},{placed[1]}) " + " ".join(properties))
    return lines


def _arrow_lines(arrows, use_sky, mapping, report):
    """Arrows, as the only encoding ds9 accepts: `# vector(x, y, length, angle) vector=1`.

    Written in whichever frame the rest of the file uses. A sky export converts the tail to
    RA/Dec, the length to degrees and the direction to ds9's sky convention: an arrow left in
    pixel coordinates would not follow the field, and transferring to another frame of the same
    field is the only reason to export sky coordinates at all.
    """
    if not arrows:
        return []

    lines = []
    for arrow in arrows:
        properties = ["vector=1"]
        if arrow.text:
            properties.append(f"text={{{_escape_braces(arrow.text)}}}")
        if arrow.color:
            properties.append(f"color={arrow.color}")
        if arrow.line_width != 2:
            properties.append(f"width={int(arrow.line_width)}")
        if arrow.dash:
            properties.append("dash=1")
        if arrow.tag:
            properties.append(f"tag={{{_escape_braces(arrow.tag)}}}")

        placed = _ds9_position(arrow.x, arrow.y, use_sky, mapping)
        if placed is None:
            label = f" {arrow.text!r}" if arrow.text else ""
            report.skipped.append(f"an arrow{label}: its tail has no sky position")
            continue

        if use_sky:
            length = mapping.pixels_to_arcsec(arrow.length)
            if length is None:
                report.skipped.append("an arrow whose length could not be put on the sky")
                continue
            length_text = f"{length / 3600.0:.8f}"
            angle = pixel_angle_to_sky(mapping, arrow.x, arrow.y, arrow.angle)
        else:
            length_text = f"{arrow.length:.8f}"
            angle = arrow.angle % 360.0

        lines.append(f"# vector({placed[0]},{placed[1]},{length_text},{angle:.8f}) "
                     + " ".join(properties))
    return lines


def pixel_angle_to_sky(mapping, x, y, angle_deg):
    """A direction measured from the image axes, expressed as ds9 wants it in a sky frame.

    In a sky frame ds9 measures an angle from the *sky* axes, so it differs from the image-frame
    angle by the field rotation — 45° becomes 75° on an image rotated 30°, and is unchanged on an
    unrotated one. Confirmed in ds9 on a rotated field: a vector given a sky angle lands along a
    box given the same one, so both use that convention.

    Rather than reimplement it, the conversion is borrowed from the library's own box handling,
    which is the tested implementation: a throwaway rectangle is rotated by `angle_deg`, put on
    the sky, and its angle read back.

    The probe's dimensions do not matter — the result is identical from 100 pixels down to 1e-6,
    because the library takes the rotation from the local WCS derivative rather than from the
    rectangle's corners. Any residual offset from a naive `angle ± rotation` is the real local
    field rotation of the projection away from the reference pixel.
    """
    probe = RectanglePixelRegion(PixCoord(*mapping.to_wcs_pixels(x, y)),
                                 2.0, 1.0, float(angle_deg) * u.deg)
    return float(probe.to_sky(mapping.wcs2d).angle.to_value(u.deg)) % 360.0


def sky_angle_to_pixel(mapping, ra_deg, dec_deg, angle_deg):
    """The inverse of `pixel_angle_to_sky`, by the same borrowed conversion."""
    probe = RectangleSkyRegion(SkyCoord(float(ra_deg) * u.deg, float(dec_deg) * u.deg),
                               2.0 * u.arcsec, 1.0 * u.arcsec, float(angle_deg) * u.deg)
    return float(probe.to_pixel(mapping.wcs2d).angle.to_value(u.deg)) % 360.0


def _ds9_position(x, y, use_sky, mapping):
    """A coordinate pair formatted for a hand-written ds9 line, or None."""
    if not use_sky:
        return (f"{x + 1:.8f}", f"{y + 1:.8f}")
    sky = mapping.to_sky(x, y)
    if sky is None:
        return None
    return (f"{sky[0]:.8f}", f"{sky[1]:.8f}")


def _escape_braces(text):
    """ds9 delimits property values with braces, so a brace inside one has to go."""
    return str(text).replace("{", "(").replace("}", ")")


def _provenance_line(written_by):
    """A `# written by ...` line, or nothing.

    Refuses a leading `-`: a comment starting with ds9's exclude prefix makes ds9 reject the
    entire file, and a version string is not worth that.
    """
    text = str(written_by or "").strip()
    if not text or text.startswith("-"):
        return ""
    return f"# written by {text}"


# ===================================================================== reading

def from_ds9(text, wcs=None, axis_indices=(0, 1), where="region file"):
    """Parse ds9 `.reg` text into a `RegionList`. Returns `(RegionList, Report)`.

    Sky-frame files need a WCS whose celestial axes are the two on display; without one their
    regions are reported rather than placed at a guess.
    """
    mapping = CelestialMap(wcs, *axis_indices)
    report = Report()

    arrows, remaining = _extract_vectors(text, mapping, report)

    try:
        parsed = Regions.parse(remaining, format="ds9") if remaining.strip() else []
    except Exception as exc:
        from pyql3.core.regions_model import RegionFormatError
        raise RegionFormatError(f"{where} could not be read as a ds9 region file: {exc}") from exc

    regions = []
    pixel_frame_count = 0
    for index, shape in enumerate(parsed):
        if not _was_sky(shape):
            pixel_frame_count += 1
        region = _from_regions_shape(shape, index, mapping, report)
        if region is not None:
            regions.append(region)

    if pixel_frame_count and tuple(axis_indices) != (0, 1):
        # Imported rather than refused — the user may well want them anyway — but this cannot
        # be left unsaid, because the numbers mean different axes in the two applications.
        report.notes.append(
            f"{pixel_frame_count} region(s) were given in ds9 image coordinates, which mean "
            f"FITS axes 1 and 2, but this cube is displayed on axes {axis_indices[0] + 1} and "
            f"{axis_indices[1] + 1}, so they may not line up. A file saved from ds9 in sky "
            "coordinates would.")

    regions += arrows
    return RegionList(regions=regions, source=where), report


def _extract_vectors(text, mapping, report):
    """Pull `vector(...)` lines out of `text`, returning `(arrows, text_without_them)`.

    Done on the raw text because `regions` discards vectors without a word, and because the
    un-prefixed form is a syntax error to ds9 that we nevertheless want to accept on the way in
    — people hand-edit these files.
    """
    arrows = []
    kept = []
    frame = "image"

    for number, line in enumerate(text.splitlines(), start=1):
        frame_match = _FRAME_RE.match(line)
        if frame_match:
            frame = frame_match.group(1).lower()
            kept.append(line)
            continue

        match = _VECTOR_RE.match(line)
        if not match:
            kept.append(line)
            continue

        arrow = _arrow_from_match(match, frame, mapping, number, report)
        if arrow is not None:
            arrows.append(arrow)

    return arrows, "\n".join(kept)


def _arrow_from_match(match, frame, mapping, line_number, report):
    x_raw, y_raw, length_raw, angle_raw = (float(match.group(i)) for i in range(1, 5))
    properties = match.group("properties") or ""

    if frame in _SKY_FRAMES:
        if not mapping.usable:
            report.skipped.append(
                f"line {line_number}: an arrow in {frame} coordinates, but {mapping.reason}")
            return None
        placed = mapping.from_sky(x_raw, y_raw)
        if placed is None:
            report.skipped.append(
                f"line {line_number}: an arrow at RA/Dec {x_raw:g},{y_raw:g} falls outside "
                "this image")
            return None
        x, y = placed
        length = mapping.arcsec_to_pixels(length_raw * 3600.0)
        if length is None or length <= 0:
            report.skipped.append(
                f"line {line_number}: an arrow whose length could not be converted to pixels")
            return None
        # A sky-frame angle is measured from the sky axes, so on a rotated field it is not the
        # image-frame angle. The conversion is the library's own (see `sky_angle_to_pixel`).
        angle = sky_angle_to_pixel(mapping, x_raw, y_raw, angle_raw)
    else:
        x, y = x_raw - 1.0, y_raw - 1.0     # ds9 counts pixels from 1
        length = length_raw
        angle = angle_raw

    if length <= 0:
        report.skipped.append(f"line {line_number}: an arrow of zero length")
        return None

    return Arrow(x=x, y=y, length=length, angle=angle % 360.0,
                 **_attributes_from_properties(properties))


def _attributes_from_properties(properties):
    """Style and label from the trailing property text of a hand-parsed line."""
    attributes = {}

    text_match = _TEXT_PROPERTY_RE.search(properties)
    if text_match:
        attributes["text"] = text_match.group(1)

    tag_match = _TAG_PROPERTY_RE.search(properties)
    if tag_match:
        attributes["tag"] = tag_match.group(1)

    color_match = _COLOR_PROPERTY_RE.search(properties)
    if color_match:
        attributes["color"] = color_match.group(1)

    width_match = _WIDTH_PROPERTY_RE.search(properties)
    if width_match:
        attributes["line_width"] = max(1, int(width_match.group(1)))

    dash_match = _DASH_PROPERTY_RE.search(properties)
    if dash_match:
        attributes["dash"] = dash_match.group(1) != "0"

    return attributes


def _from_regions_shape(shape, index, mapping, report):
    """One `regions` object as a model region, or None with a note in the report."""
    pixel = _as_pixel_shape(shape, index, mapping, report)
    if pixel is None:
        return None

    attributes = _attributes_from_regions(shape, pixel)
    to_orig = _orig_mapper(shape, mapping)

    if isinstance(pixel, CirclePixelRegion):
        centre = to_orig(pixel.center)
        return Circle(x=centre[0], y=centre[1], radius=float(pixel.radius), **attributes)

    if isinstance(pixel, RectanglePixelRegion):
        centre = to_orig(pixel.center)
        return Box(x=centre[0], y=centre[1], width=float(pixel.width),
                   height=float(pixel.height),
                   angle=float(pixel.angle.to_value(u.deg)) % 360.0, **attributes)

    if isinstance(pixel, TextPixelRegion):
        centre = to_orig(pixel.center)
        label = getattr(shape, 'text', '') or attributes.pop("text", "")
        attributes.pop("text", None)
        if not label:
            report.skipped.append(f"region {index}: a text region with no text")
            return None
        rotation = float(shape.visual.get("rotation", 0.0) or 0.0)
        return Text(x=centre[0], y=centre[1], text=label, angle=rotation % 360.0, **attributes)

    if isinstance(pixel, LinePixelRegion):
        # ds9 lines carry optional arrowheads that `regions` drops, so a line becomes an arrow
        # pointing at its second endpoint. Nothing is lost that the library kept.
        start, end = to_orig(pixel.start), to_orig(pixel.end)
        if math.hypot(end[0] - start[0], end[1] - start[1]) <= 0:
            report.skipped.append(f"region {index}: a line of zero length")
            return None
        report.notes.append(
            f"region {index}: a line was imported as an arrow (ds9 arrowhead flags are not "
            "carried by the regions library)")
        return Arrow.from_points(start[0], start[1], end[0], end[1], **attributes)

    report.skipped.append(
        f"region {index}: {type(shape).__name__.replace('PixelRegion', '').replace('SkyRegion', '')}"
        " is not one of the shapes QuickLook 3 draws")
    return None


def _was_sky(shape):
    """True when the parsed shape came from a sky frame and so went through the WCS."""
    return type(shape).__name__.endswith("SkyRegion")


def _orig_mapper(shape, mapping):
    """A function turning one of `shape`'s pixel points into orig `(x, y)`.

    A sky region's pixels come back in the celestial WCS's own axis order, which is only the
    display's order when the AXIS combos have X on the longitude axis.
    """
    if _was_sky(shape):
        return lambda point: mapping.from_wcs_pixels(point.x, point.y)
    return lambda point: (float(point.x), float(point.y))


def _as_pixel_shape(shape, index, mapping, report):
    """A pixel-space version of `shape`, converting through the WCS when it is a sky region."""
    if not _was_sky(shape):
        return shape

    if not mapping.usable:
        report.skipped.append(
            f"region {index}: given in sky coordinates, but {mapping.reason}")
        return None

    try:
        pixel = shape.to_pixel(mapping.wcs2d)
    except Exception as exc:
        report.skipped.append(f"region {index}: could not be placed on this image ({exc})")
        return None

    # A position outside the projection comes back as NaN rather than raising, for the centre
    # of a shape or for either end of a line.
    points = [getattr(pixel, name, None) for name in ("center", "start", "end")]
    for point in points:
        if point is not None and not (math.isfinite(point.x) and math.isfinite(point.y)):
            report.skipped.append(f"region {index}: its sky position falls outside this image")
            return None
    return pixel


def _attributes_from_regions(shape, pixel):
    """Model attributes from a parsed shape's `meta` and `visual` dicts.

    ds9's `color=` lands under different keys depending on the shape — `facecolor`/`edgecolor`
    for a circle, `color` for text — so all three are consulted.
    """
    visual = dict(getattr(shape, 'visual', {}) or {})
    meta = dict(getattr(shape, 'meta', {}) or {})
    attributes = {}

    color = visual.get("color") or visual.get("edgecolor") or visual.get("facecolor")
    if color:
        attributes["color"] = str(color)

    if visual.get("linewidth"):
        attributes["line_width"] = max(1, int(visual["linewidth"]))
    if visual.get("linestyle") in ("dashed", "dashdot", "dotted"):
        attributes["dash"] = True
    if visual.get("fontsize"):
        attributes["font_size"] = max(1, int(visual["fontsize"]))

    if meta.get("text"):
        attributes["text"] = str(meta["text"])
    tag = meta.get("tag")
    if tag:
        attributes["tag"] = str(tag[0] if isinstance(tag, (list, tuple)) else tag)

    return attributes

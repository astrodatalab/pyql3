"""ds9 `.reg` import and export, built on the `regions` package.

`regions` owns the parts it does well — circle, box, text, line, every coordinate frame, the
1-based ds9 pixel convention, and the sky-frame angle conventions — and this module owns the
parts it cannot do. Both gaps were measured rather than assumed (see `TODO_regions.md`):

- **`vector(...)` is dropped silently on read** and has no class to write, so arrows are parsed
  out of the file text and appended to the output by hand. Every ds9 arrow is a `vector`.
- **`textangle` is read but never written**: it arrives as `visual['rotation']` and is missing
  from `serialize()` output, so a rotated label is re-attached by hand.
- **`physical` is refused as a frame**, and refusing a frame means dropping every shape that
  follows it — a whole file, in practice — so it is rewritten to `image` before parsing (see
  `_normalize_pixel_frames`).

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
import warnings
from dataclasses import dataclass, field

import astropy.units as u
from astropy.coordinates import SkyCoord
from regions import (
    CirclePixelRegion,
    LinePixelRegion,
    PixCoord,
    RectanglePixelRegion,
    Regions,
    TextPixelRegion,
)

from pyql3.core.regions_model import (
    Arrow,
    Box,
    Circle,
    DS9_FRAME_WORDS,
    RegionFormatError,
    RegionList,
    Text,
    default_style,
)
from pyql3.core.sky import DS9_SKY_FRAMES, CelestialMap

#: ds9's own header line. `regions` writes its own variant, which ds9 also accepts.
DS9_HEADER = "# Region file format: DS9 version 4.1"

#: Frame names we accept for output. "auto" picks sky when image coordinates would not line up.
OUTPUT_FRAMES = ("auto", "image", "sky")

#: `vector(x, y, length, angle)`, with or without ds9's leading `#`, and any trailing
#: properties. Written by hand because `regions` has no vector at all. The fields are taken as
#: whole tokens rather than numbers: in a sky frame ds9 writes sexagesimal positions and a length
#: carrying its unit (`15"`), and reading those as bare degrees put arrows kiloparsecs away.
_VECTOR_RE = re.compile(
    r"""^\s*(?:\#\s*)?vector\s*\(\s*
        ([^,()]+?)\s*,\s*([^,()]+?)\s*,\s*
        ([^,()]+?)\s*,\s*([^,()]+?)\s*\)
        (?P<properties>.*)$""",
    re.VERBOSE | re.IGNORECASE)

#: Enough of a vector to know one was meant. A line matching this but not `_VECTOR_RE` is
#: reported: `regions` treats `# vector(...)` as a comment, so it would otherwise just vanish.
_VECTOR_LOOSE_RE = re.compile(r"^\s*(?:\#\s*)?vector\s*\(", re.IGNORECASE)

#: A coordinate-system statement, which applies to everything after it: the frame word alone or
#: followed by a comment (`fk5 # from ds9`), on a line of its own or as one of several statements
#: separated by `;`. The words are `regions_model.DS9_FRAME_WORDS`, so recognising a file and
#: reading it agree; `detector`, `amplifier` and `linear` are among them so that an arrow given in
#: one is refused rather than read as pixels of this image (`BUGS.md` R47).
_FRAME_RE = re.compile(rf"^\s*({DS9_FRAME_WORDS})\s*(#.*)?$", re.IGNORECASE)

#: ds9's `# text(...)` label statement.
_TEXT_STATEMENT_RE = re.compile(r"^\s*\#\s*text\s*\(", re.IGNORECASE)

#: ds9's `global` line, whose properties apply to every region after it that does not set its own.
_GLOBAL_RE = re.compile(r"^\s*global\b(?P<properties>.*)$", re.IGNORECASE)

#: The properties of a `global` line that a hand-parsed arrow takes as its defaults, as the
#: library's shapes do (`BUGS.md` R64).
_GLOBAL_ARROW_ATTRIBUTES = ("color", "line_width", "dash")

#: ds9's sky frames, mapped to the astropy frame each one means. Kept in `sky`, which also names
#: a WCS's own frame from it; see `DS9_SKY_FRAMES` there for why it has to match the library.
_SKY_FRAMES = DS9_SKY_FRAMES

#: Frames whose numbers are pixels of the image on display.
_PIXEL_FRAMES = ("image", "physical")

#: Astropy frames whose longitude ds9 writes in hours when it writes sexagesimal. Galactic and
#: ecliptic sexagesimal is degrees, and reading `17:45:40` as hours there is a 15× error.
_HOUR_ANGLE_FRAMES = ("icrs", "fk5", "fk4")

#: ds9's unit suffixes on a length, as multiples of an arcsecond. `p` and `i` mean pixels, which
#: is not an angle at all, so they are handled separately.
_ARCSEC_PER = {'"': 1.0, "'": 60.0, "d": 3600.0}

#: How many items of a report to spell out before counting the rest. A file that goes wrong
#: uniformly goes wrong once per region, and a dialog of five hundred identical lines is a dialog
#: nobody reads. The lists themselves stay complete for anything that wants them.
_MAX_SUMMARY_ITEMS = 10

#: Trailing wording the library adds to every warning; it is the report's job to say this.
_WARNING_TAIL_RE = re.compile(r",?\s*skipping\.?\s*$", re.IGNORECASE)

#: ds9 annotations written behind a `#`, which makes them comments to `regions` — it drops them
#: without even a warning, so they are counted here to keep them out of the silent-loss category.
#: `vector` is absent deliberately: that one we do read.
_ANNOTATION_RE = re.compile(
    r"^\s*\#\s*(compass|ruler|projection|segment|panda|epanda|bpanda)\s*\(", re.IGNORECASE)

_TEXT_PROPERTY_RE = re.compile(r"text\s*=\s*\{([^}]*)\}")
_TAG_PROPERTY_RE = re.compile(r"tag\s*=\s*\{([^}]*)\}")
_COLOR_PROPERTY_RE = re.compile(r"color\s*=\s*(\S+)")
_WIDTH_PROPERTY_RE = re.compile(r"width\s*=\s*(\d+)")
_DASH_PROPERTY_RE = re.compile(r"dash\s*=\s*(\d+)")
#: The size in ds9's `font="helvetica 12 normal roman"`.
_FONT_SIZE_PROPERTY_RE = re.compile(r'font\s*=\s*"[^"\d]*(\d+)')
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
        """The report as text for a dialog, long lists shortened to a count."""
        lines = []
        if self.skipped:
            lines.append("Not converted:")
            lines += _bulleted(self.skipped)
        if self.notes:
            lines.append("Note:")
            lines += _bulleted(self.notes)
        return "\n".join(lines)


def _bulleted(items):
    """`items` as bullet lines, with everything past `_MAX_SUMMARY_ITEMS` counted instead."""
    lines = [f"  • {item}" for item in items[:_MAX_SUMMARY_ITEMS]]
    if len(items) > _MAX_SUMMARY_ITEMS:
        lines.append(f"  • ...and {len(items) - _MAX_SUMMARY_ITEMS} more")
    return lines


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
        if _has_line_break(region.text) or _has_line_break(region.tag):
            report.notes.append(
                f"region {index}: a newline in its label or tag was written as a space, because "
                "ds9 reads one region per line")
        if isinstance(region, Arrow):
            arrows.append(region)
            continue
        if isinstance(region, Text) and (region.angle % 360.0 or use_sky):
            # Handled by hand below. Passing it to the library as well would write the label
            # twice, once without its rotation. In a sky frame every label is: its angle is
            # measured from the sky axes, so even a level label needs one on a rotated field.
            rotated_text.append(region)
            continue
        shape = _to_regions_shape(region, index, report)
        if shape is not None:
            shapes.append(shape)

    _report_unwritten(list(region_list), report)

    if use_sky:
        shapes = _to_sky_shapes(shapes, mapping, report)

    if shapes:
        lines = Regions(shapes).serialize(format="ds9").splitlines()
    else:
        # Nothing for the library to write, but the frame still has to be declared for the
        # hand-written lines that follow, and their numbers are in the WCS's own frame — the
        # name the library would have written for a shape on it (`BUGS.md` R17).
        lines = [DS9_HEADER, (mapping.ds9_frame or "icrs") if use_sky else "image"]

    provenance = _provenance_line(written_by)
    if provenance:
        # After the header line, which is where ds9 puts its own preamble.
        lines.insert(1, provenance)

    lines += _rotated_text_lines(rotated_text, use_sky, mapping, report)
    lines += _arrow_lines(arrows, use_sky, mapping, report)

    return "\n".join(lines) + "\n", report


def _report_unwritten(regions, report):
    """One `skipped` entry for each kind of attribute a ds9 file has no place for (`BUGS.md` R48).

    The regions are still written; what they lose is named, with the regions it applies to.
    """
    default_font = default_style()["font_size"]
    kinds = (
        ("a channel range, which ds9 regions do not have; written for every channel",
         lambda region: region.z_range is not None),
        ("hidden, which ds9 regions cannot be; written visible",
         lambda region: not region.visible),
        ("a label font size, which ds9 is given for text regions and arrows only; it reads "
         f"back as {default_font}",
         lambda region: (not isinstance(region, (Text, Arrow)) and bool(region.text)
                         and region.font_size != default_font)),
    )
    for description, applies in kinds:
        indices = [index for index, region in enumerate(regions) if applies(region)]
        if indices:
            named = ", ".join(f"region {index}" for index in indices[:_MAX_SUMMARY_ITEMS])
            if len(indices) > _MAX_SUMMARY_ITEMS:
                named += f" and {len(indices) - _MAX_SUMMARY_ITEMS} more"
            report.skipped.append(f"{named}: {description}")


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
        return TextPixelRegion(centre, _one_line(region.text), visual=visual, meta=meta)

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
        # `regions` writes `font=` only when a font name is set, so without these the size was
        # dropped from every unrotated label (review of Phase 10, I3).
        visual["fontname"] = "helvetica"
        visual["fontweight"] = "normal"
        visual["fontstyle"] = "normal"

    meta = {}
    if region.text:
        meta["text"] = _one_line(region.text)
    if region.tag:
        meta["tag"] = [_one_line(region.tag)]
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
    """Return `shape` with its centre and angle expressed in the celestial WCS's own axis order.

    Only differs from the original when the AXIS combos have X on the latitude axis. Exchanging
    the axes is a reflection, so a box's angle changes too; its width and height do not
    (`BUGS.md` R38).
    """
    if not mapping.swapped:
        return shape
    px, py = mapping.to_wcs_pixels(shape.center.x, shape.center.y)
    moved = shape.copy()
    moved.center = PixCoord(px, py)
    if isinstance(moved, RectanglePixelRegion):
        moved.angle = mapping.to_wcs_angle(shape.angle.to_value(u.deg)) * u.deg
    return moved


def _rotated_text_lines(rotated_text, use_sky, mapping, report):
    """`textangle` for rotated labels, which `regions` reads but does not write.

    Measured, not assumed: `TextPixelRegion(..., visual={'rotation': 30})` serializes with no
    `textangle` at all, so a rotated label loses its angle if the library writes it. Such labels
    are therefore kept out of the library's hands entirely (see `to_ds9`) and written here, in
    the `# text(...)` form ds9 itself uses.

    In a sky frame ds9 measures `textangle` from the sky axes, as it does a box's angle: seen in
    ds9 on a 30°-rotated field, `textangle=30` is drawn level. The angle is converted like an
    arrow's (`pixel_angle_to_sky`), so every label in a sky export is written here.
    """
    lines = []
    for region in rotated_text:
        placed = _ds9_position(region.x, region.y, use_sky, mapping)
        if placed is None:
            report.skipped.append(f"text {region.text!r}: {_unplaced_reason(mapping, use_sky)}")
            continue
        angle = region.angle
        if use_sky:
            angle = pixel_angle_to_sky(mapping, region.x, region.y, angle)
            if angle is None:
                report.skipped.append(f"text {region.text!r}: no sky position for its angle")
                continue
        angle = round(angle % 360.0, 6) % 360.0
        properties = [f"text={{{_escape_braces(region.text)}}}", f"textangle={angle:g}"]
        if region.color:
            properties.append(f"color={region.color}")
        properties.append(f'font="helvetica {int(region.font_size)} normal roman"')
        if region.tag:
            # Written by hand here, so the tag is too, as the library writes it for a shape.
            properties.append(f"tag={{{_escape_braces(region.tag)}}}")
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
        # Always stated, even when empty: with a single labelled shape the library writes its
        # label on the `global` line, and ds9 would give it to this arrow too. A label's size is
        # stated for the same reason, since that line can also carry `font=`.
        properties.append(f"text={{{_escape_braces(arrow.text)}}}")
        if arrow.text:
            properties.append(f'font="helvetica {int(arrow.font_size)} normal roman"')
        if arrow.color:
            properties.append(f"color={arrow.color}")
        # Always stated: ds9 applies the library's `global` line to a vector too, and that line
        # carries the shapes' width and dash, not the arrow's (`BUGS.md` R64).
        properties.append(f"width={int(arrow.line_width)}")
        properties.append(f"dash={1 if arrow.dash else 0}")
        if arrow.tag:
            properties.append(f"tag={{{_escape_braces(arrow.tag)}}}")

        placed = _ds9_position(arrow.x, arrow.y, use_sky, mapping)
        if placed is None:
            label = f" {arrow.text!r}" if arrow.text else ""
            report.skipped.append(f"an arrow{label}: {_unplaced_reason(mapping, use_sky)}")
            continue

        if use_sky:
            length = mapping.length_to_sky(arrow.x, arrow.y, arrow.angle, arrow.length)
            angle = pixel_angle_to_sky(mapping, arrow.x, arrow.y, arrow.angle)
            if length is None or angle is None:
                report.skipped.append("an arrow whose length could not be put on the sky")
                continue
            length_text = f"{length.deg:.8f}"
        else:
            length_text = f"{arrow.length:.8f}"
            angle = arrow.angle % 360.0

        lines.append(f"# vector({placed[0]},{placed[1]},{length_text},{angle:.8f}) "
                     + " ".join(properties))
    if use_sky and lines and not _pixels_are_square(mapping):
        report.notes.append(
            "the image's pixels are not square, and ds9 draws a sky-frame arrow as if they were: "
            "its direction and length in ds9 will differ from this image (BUGS.md R94)")
    return lines


def _pixels_are_square(mapping):
    """True when the celestial pixels have the same scale on both axes, to 1 part in 1000.

    Seen in ds9 on 0.36" x 0.72" pixels: ds9 turns a vector's sky angle into a pixel angle by the
    field rotation alone, and its length into pixels with one scale (there, 0.72"). The writer
    gives the true direction and length on the sky, so on such pixels ds9 draws the arrow off
    target (`BUGS.md` R94). On square pixels the two agree.
    """
    from astropy.wcs.utils import proj_plane_pixel_scales

    scales = proj_plane_pixel_scales(mapping.wcs2d)
    return math.isclose(scales[0], scales[1], rel_tol=1e-3)


def pixel_angle_to_sky(mapping, x, y, angle_deg):
    """A direction measured from the orig axes at `(x, y)`, as ds9 wants it in a sky frame.

    In a sky frame ds9 measures an angle from the *sky* axes, so it differs from the image-frame
    angle by the field rotation — 45° becomes 75° on an image rotated 30°, and is unchanged on an
    unrotated one. Confirmed in ds9 on a rotated field: a vector given a sky angle lands along a
    box given the same one, so both use that convention.

    The direction itself is mapped (`CelestialMap.direction_to_sky`). This used to rotate a 2 x 1
    rectangle and read back the library's angle for it, which is the rectangle's principal axis
    on the sky: the direction only while the pixels are square (`BUGS.md` R37), and an angle in
    the WCS's pixel axes rather than orig ones when the display swaps them (R38). None if there
    is no sky position.
    """
    return mapping.direction_to_sky(x, y, angle_deg)


def sky_angle_to_pixel(mapping, coord, angle_deg):
    """The inverse of `pixel_angle_to_sky`: an orig angle, or None.

    `coord` is the `SkyCoord` the angle is measured at, in whatever frame it was given: bare
    numbers would be read as ICRS, which put a galactic position off the projection and a B1950
    one 0.7 degrees away, where north points differently (`BUGS.md` R10).
    """
    return mapping.direction_from_sky(coord, angle_deg)


def _ds9_position(x, y, use_sky, mapping):
    """A coordinate pair formatted for a hand-written ds9 line, or None."""
    if not use_sky:
        return (f"{x + 1:.8f}", f"{y + 1:.8f}")
    sky = mapping.to_sky(x, y)
    if sky is None or mapping.ds9_frame is None:
        # Numbers in a frame ds9 has no name for could not be declared in the file.
        return None
    return (f"{sky[0]:.8f}", f"{sky[1]:.8f}")


def _unplaced_reason(mapping, use_sky):
    """Why `_ds9_position` gave nothing for a hand-written line, in words for the Report."""
    if use_sky and mapping.usable and mapping.ds9_frame is None:
        name = mapping.frame.name if mapping.frame is not None else "not recognised"
        return f"the WCS's celestial frame ({name}) has no ds9 name to declare it by"
    return "its position has no sky coordinates"


def _escape_braces(text):
    """ds9 delimits property values with braces, so a brace inside one has to go.

    So does a newline, which would end the region's line in the middle of its label.
    """
    return _one_line(text).replace("{", "(").replace("}", ")")


def _one_line(text):
    """`text` with every newline and carriage return replaced by a space (`BUGS.md` R79)."""
    return re.sub(r"\r\n|\r|\n", " ", str(text))


def _has_line_break(text):
    return "\n" in str(text) or "\r" in str(text)


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

    text = _normalize_pixel_frames(text, report)
    _report_annotations(text, report)
    arrows, remaining, pixel_frame_arrows = _extract_vectors(text, mapping, report)
    parsed = _parse_shapes(remaining, report, where)

    regions = []
    # Image-frame arrows mean FITS axes 1 and 2 as much as image-frame shapes do (R47).
    pixel_frame_count = pixel_frame_arrows
    for index, shape in enumerate(parsed):
        if not _was_sky(shape):
            pixel_frame_count += 1
        if (getattr(shape, "meta", None) or {}).get("include", 1) == 0:
            # ds9's leading `-`, which `regions` records as `include = 0` (`BUGS.md` R48).
            report.skipped.append(
                f"region {index}: excluded in ds9 (a leading '-'), which QuickLook 3 does not "
                "have; imported as an ordinary region")
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


def _normalize_pixel_frames(text, report):
    """Rewrite ds9's `physical` frame lines to `image`, noting it in the report.

    ds9 has two pixel frames. `image` counts pixels of the file as stored; `physical` counts them
    in the coordinates of whatever the file was cut from, which IRAF records in the `LTV`/`LTM`
    keywords. Without those keywords — the case for every reduced cube and mosaic we see — the two
    frames are the same numbers.

    `regions` 0.12 refuses `physical` outright, and refusing a frame is not a local failure there:
    it clears the current frame, so *every shape after that line* is dropped with a warning. A
    whole file of perfectly ordinary boxes therefore loads as nothing at all, which is what
    `example/mag06maylgs1_wide_kp_GCOWS_2012a.reg` did. Rewriting the one word is the whole fix.

    The `LTV`/`LTM` shift is not applied: the reader is handed a WCS, not a header, and no file we
    have carries the keywords. The note says so rather than leaving the assumption implicit.
    """
    lines = text.splitlines()
    rewritten = 0

    for index, line in enumerate(lines):
        # ds9 accepts several statements on a line, separated by semicolons, and the frame is one
        # of them. Only a segment that is nothing but the word is touched, so rejoining is exact.
        segments = line.split(";")
        for position, segment in enumerate(segments):
            match = _FRAME_RE.match(segment)
            if match and match.group(1).lower() == "physical":
                segments[position] = re.sub("physical", "image", segment, count=1,
                                            flags=re.IGNORECASE)
                rewritten += 1
        lines[index] = ";".join(segments)

    if not rewritten:
        return text

    report.notes.append(
        "this file uses ds9's 'physical' frame, which was read as image coordinates; the two "
        "differ only for a file cut from a larger one with IRAF LTV/LTM keywords, which are not "
        "applied here")
    return "\n".join(lines)


def _parse_shapes(text, report, where):
    """`Regions.parse`, with everything it complains about moved into the report.

    The library announces a shape it cannot read through `warnings.warn`, which lands in the
    terminal where a GUI user never sees it — so a file it dropped entirely looked like a file
    with nothing in it. Silent loss is the failure this module exists to prevent, so the warnings
    are caught and reported instead of printed.

    Catching them also stops the default warning filter from hiding the second copy: it shows a
    given message once per process, so re-opening the same bad file said nothing at all.
    """
    if not text.strip():
        return []

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        try:
            parsed = Regions.parse(text, format="ds9")
        except Exception as exc:
            raise RegionFormatError(
                f"{where} could not be read as a ds9 region file: {exc}") from exc

    _record_parse_warnings(caught, report)
    return parsed


def _record_parse_warnings(caught, report):
    """Fold the library's warnings into `report`, re-raising anything that is not one of ours."""
    messages = []
    for warning in caught:
        if not issubclass(warning.category, UserWarning):
            # Not a complaint about this file — a deprecation, say. Do not swallow it.
            warnings.warn_explicit(warning.message, warning.category, warning.filename,
                                   warning.lineno)
            continue
        message = _WARNING_TAIL_RE.sub("", " ".join(str(warning.message).split()))
        if message and message not in messages:
            messages.append(message)

    report.skipped += messages


def _report_annotations(text, report):
    """Note the `#`-prefixed ds9 annotations that nothing downstream will mention.

    A `# compass(...)` or `# ruler(...)` reads as a plain comment to `regions`, so unlike an
    unsupported *shape* it does not even produce a warning — it simply is not in the result. There
    is no model region to put one in, but the user should still be told it was there.
    """
    for number, line in enumerate(text.splitlines(), start=1):
        match = _ANNOTATION_RE.match(line)
        if match:
            report.skipped.append(
                f"line {number}: a ds9 {match.group(1).lower()} annotation, which QuickLook 3 "
                "cannot draw")


def _extract_vectors(text, mapping, report):
    """Pull `vector(...)` statements out of `text`.

    Returns `(arrows, text_without_them, how many arrows were in an image frame)`.

    Done on the raw text because `regions` discards vectors without a word, and because the
    un-prefixed form is a syntax error to ds9 that we nevertheless want to accept on the way in
    — people hand-edit these files.

    The frame and the `global` line are followed the way ds9 and `regions` follow them for
    shapes (`BUGS.md` R47, R64): each line is split into its `;`-separated statements, a frame
    word followed by a comment still sets the frame, and `fk5; # vector(...)` is a frame and then
    a vector. The latest `global` line gives an arrow its colour, width and dash where its own
    line does not.
    """
    arrows = []
    kept = []
    frame = "image"
    base = {}
    pixel_frame_arrows = 0

    for number, line in enumerate(text.splitlines(), start=1):
        statements = []
        for segment in _statements(line):
            global_match = _GLOBAL_RE.match(segment)
            if global_match:
                found = _attributes_from_properties(global_match.group("properties"))
                # Merged, as `regions` merges successive `global` lines for its shapes.
                base.update({name: found[name] for name in _GLOBAL_ARROW_ATTRIBUTES
                             if name in found})
                statements.append(segment)
                continue

            frame_match = _FRAME_RE.match(segment)
            if frame_match:
                frame = frame_match.group(1).lower()
                comment = frame_match.group(2) or ""
                if _TEXT_STATEMENT_RE.match(comment):
                    # `fk5 # text(...)`: a label, which the library reads as a statement of its
                    # own but not behind a frame word.
                    statements += [frame_match.group(1), comment]
                    continue
                if not _VECTOR_LOOSE_RE.match(comment):
                    # A plain comment, which says nothing about the frame.
                    statements.append(segment)
                    continue
                # `fk5 # vector(...)`: the frame, then a statement behind ds9's `#`.
                statements.append(frame_match.group(1))
                segment = comment

            match = _VECTOR_RE.match(segment)
            if match is None:
                if _VECTOR_LOOSE_RE.match(segment):
                    report.skipped.append(
                        f"line {number}: a vector whose fields could not be read "
                        f"({segment.strip()})")
                else:
                    statements.append(segment)
                continue

            arrow = _arrow_from_match(match, frame, mapping, number, report, base)
            if arrow is not None:
                arrows.append(arrow)
                if frame in _PIXEL_FRAMES:
                    pixel_frame_arrows += 1

        if statements:
            kept.append(";".join(statements))

    return arrows, "\n".join(kept), pixel_frame_arrows


def _statements(line):
    """`line` split at the `;` between ds9 statements, not at one inside a `{...}` value."""
    parts = []
    depth = start = 0
    for position, character in enumerate(line):
        if character == "{":
            depth += 1
        elif character == "}":
            depth = max(0, depth - 1)
        elif character == ";" and depth == 0:
            parts.append(line[start:position])
            start = position + 1
    parts.append(line[start:])
    return parts


def _arrow_from_match(match, frame, mapping, line_number, report, base=None):
    x_text, y_text, length_text, angle_text = (match.group(i).strip() for i in range(1, 5))
    properties = match.group("properties") or ""

    angle_raw = _as_float(angle_text)
    if angle_raw is None:
        report.skipped.append(f"line {line_number}: an arrow with an unreadable angle "
                              f"({angle_text})")
        return None

    if frame in _SKY_FRAMES:
        if not mapping.usable:
            report.skipped.append(
                f"line {line_number}: an arrow in {frame} coordinates, but {mapping.reason}")
            return None

        tail = _sky_tail(x_text, y_text, frame)
        if tail is None:
            report.skipped.append(
                f"line {line_number}: an arrow whose {frame} position could not be read "
                f"({x_text},{y_text})")
            return None

        placed = mapping.from_skycoord(tail)
        if placed is None:
            report.skipped.append(
                f"line {line_number}: an arrow at {frame} {x_text},{y_text} falls outside "
                "this image")
            return None
        x, y = placed

        # A sky-frame angle is measured from the sky axes, so on a rotated field it is not the
        # image-frame angle (see `sky_angle_to_pixel`). It is measured in the frame the file
        # wrote the tail in, and so is the length, which with unequal pixel scales depends on
        # the direction it points.
        angle = sky_angle_to_pixel(mapping, tail, angle_raw)
        length = _length_in_pixels(length_text, frame, mapping, tail, angle_raw)
        if angle is None or length is None or length <= 0:
            report.skipped.append(
                f"line {line_number}: an arrow whose length could not be converted to pixels "
                f"({length_text})")
            return None
    elif frame not in _PIXEL_FRAMES:
        # `wcsa`, `detector`, ... — the library refuses these for shapes, and guessing that the
        # numbers are pixels would put the arrow somewhere arbitrary.
        report.skipped.append(
            f"line {line_number}: an arrow in {frame} coordinates, which cannot be placed on "
            "this image")
        return None
    else:
        x_raw, y_raw = _as_float(x_text), _as_float(y_text)
        length_raw = _length_in_pixels(length_text, frame, mapping)
        if x_raw is None or y_raw is None or length_raw is None:
            report.skipped.append(
                f"line {line_number}: an arrow whose numbers could not be read "
                f"({x_text},{y_text},{length_text})")
            return None
        x, y = x_raw - 1.0, y_raw - 1.0     # ds9 counts pixels from 1
        length = length_raw
        angle = angle_raw

    if length <= 0:
        report.skipped.append(f"line {line_number}: an arrow of zero length")
        return None

    attributes = dict(base or {})
    attributes.update(_attributes_from_properties(properties))
    return Arrow(x=x, y=y, length=length, angle=angle % 360.0, **attributes)


def _as_float(text):
    """`text` as a number, or None. ds9 allows a trailing `d` on a plain degree value."""
    try:
        return float(str(text).strip().rstrip("dD"))
    except ValueError:
        return None


def _sky_tail(x_text, y_text, frame):
    """An arrow's tail as a `SkyCoord` in the frame the file declared, or None.

    ds9 writes an equatorial position either as degrees or as sexagesimal, and in sexagesimal the
    longitude is in *hours* — `17:45:40` is 266.4°, not 17.8°. Galactic and ecliptic sexagesimal
    is degrees throughout. Getting that wrong is a 15× error, which lands off the image rather
    than merely off the target, so it is decided here from the frame rather than guessed.
    """
    astropy_frame = _SKY_FRAMES[frame]
    sexagesimal = any(mark in x_text.lower() for mark in (":", "h"))
    longitude_unit = (u.hourangle if sexagesimal and astropy_frame in _HOUR_ANGLE_FRAMES
                      else u.deg)
    try:
        return SkyCoord(x_text, y_text, frame=astropy_frame, unit=(longitude_unit, u.deg))
    except Exception:       # unparseable text, or a unit the coordinate string contradicts
        return None


def _length_in_pixels(text, frame, mapping, tail=None, sky_angle=None):
    """A ds9 `length` field as pixels, or None if it cannot be converted.

    ds9 puts the unit in the value: `15"` is fifteen arcseconds, `15'` fifteen arcminutes, `15d`
    fifteen degrees, `15p`/`15i` fifteen pixels. A bare number means degrees in a sky frame and
    pixels in a pixel frame — which is why reading a sky arrow's `15"` as a bare degree value
    made it 3600 times too long.

    An angular length is measured along the arrow, from `tail` at `sky_angle`, when those are
    given (`BUGS.md` R37); otherwise it is scaled by the mean pixel scale.
    """
    text = str(text).strip()
    if not text:
        return None

    suffix = text[-1].lower() if text[-1] in ('"', "'", "d", "D", "p", "P", "i", "I") else ""
    value = _as_float(text[:-1] if suffix else text)
    if value is None:
        return None

    if suffix in ("p", "i") or (not suffix and frame in _PIXEL_FRAMES):
        return value
    arcsec = value * _ARCSEC_PER.get(suffix, 3600.0)
    if tail is not None:
        return mapping.length_from_sky(tail, sky_angle, arcsec * u.arcsec)
    return mapping.arcsec_to_pixels(arcsec)


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

    font_match = _FONT_SIZE_PROPERTY_RE.search(properties)
    if font_match:
        attributes["font_size"] = max(1, int(font_match.group(1)))

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
        angle = float(pixel.angle.to_value(u.deg))
        if _was_sky(shape):
            # Measured from the WCS's pixel axes, which are orig's the other way round when the
            # display swaps them (`BUGS.md` R38).
            angle = mapping.from_wcs_angle(angle)
        return Box(x=centre[0], y=centre[1], width=float(pixel.width),
                   height=float(pixel.height), angle=angle % 360.0, **attributes)

    if isinstance(pixel, TextPixelRegion):
        centre = to_orig(pixel.center)
        label = getattr(shape, 'text', '') or attributes.pop("text", "")
        attributes.pop("text", None)
        if not label:
            report.skipped.append(f"region {index}: a text region with no text")
            return None
        rotation = float(shape.visual.get("rotation", 0.0) or 0.0)
        if _was_sky(shape):
            # Measured from the sky axes, as the writer converts it (`_rotated_text_lines`).
            rotation = sky_angle_to_pixel(mapping, shape.center, rotation)
            if rotation is None:
                report.skipped.append(f"region {index}: a label whose angle could not be "
                                      "converted to this image")
                return None
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

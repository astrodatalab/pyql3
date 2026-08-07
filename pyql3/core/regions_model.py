"""Drawable regions — the data model and the native YAML file format.

This is deliberately free of Qt, astropy and any knowledge of ds9: a region is a few numbers
and some styling, and keeping it that way means the file format can be tested exhaustively
without a viewer. The interactive layer (`TODO_regions.md` Phase 3) turns these into pyqtgraph
items; `ds9_regions` (Phase 2) converts to and from `.reg`.

### Coordinates

Geometry is stored in **orig coordinates**: 0-based indices into `transposed_data`, the
coordinate along whichever FITS axis the AXIS 1/2/3 combo boxes currently map to X or Y. Never
display coordinates — a flip or a 90° rotation would silently move every region on the next
load. `pyql3.core.coords` converts to and from what is on screen, and is the only thing allowed
to (`AGENTS.md`, `BUGS.md` B13/B14).

Angles are degrees CCW from +X in that same orig frame, so `coords.orig_angle_to_display()`
maps them for drawing.

### Sky positions

Each region can carry a `SkyAnchor` alongside its pixel geometry, which is what makes a saved
file meaningful on a different frame of the same field. Populating it needs a WCS and so
belongs with the Phase 2 work, where the `wcs.sub(['longitude','latitude'])` trap is already
documented; this module only defines and round-trips the record. Pixel geometry stays
authoritative while `frame == "image"`.

### The file format

```yaml
format: pyql3-regions/1
regions:
  - type: circle
    x: 31.5
    y: 9.5
    radius: 4.0
    text: src A
```

Fields left at their default are omitted on write, so a hand-edited file stays readable.
Reading is `yaml.safe_load` only — a region file is data, and `yaml.load` on data from
elsewhere constructs arbitrary Python objects.
"""

from dataclasses import MISSING, dataclass, fields
from pathlib import Path

import yaml

#: Format marker written into every file. The number is a *major* version: a file whose major
#: version we do not know is refused rather than half-read.
FORMAT_NAME = "pyql3-regions"
FORMAT_VERSION = 1
FORMAT = f"{FORMAT_NAME}/{FORMAT_VERSION}"

#: Order the shared attributes are written in, after `type` and the geometry.
COMMON_ORDER = ("text", "color", "line_width", "dash", "font_size", "tag", "visible",
                "frame", "z_range", "sky")

VALID_FRAMES = ("image", "sky")

#: Written to the file even when left at its default, because reading it back is a decision the
#: user may be asked to make and "absent" is a bad way to say "pixels".
ALWAYS_WRITTEN = ("frame",)

#: The formatting a file records for itself, in the `style:` block — ds9's `global` line by
#: another name. A region writes only what differs from it, so the appearance of every region is
#: fixed by the file rather than by whatever this program's built-in defaults happen to be on the
#: day it is read back. Editing the block restyles everything that did not override it, exactly as
#: editing ds9's `global` line does.
STYLE_FIELDS = ("color", "line_width", "dash", "font_size")

#: ds9's own RGB for the colour names it uses, so a region looks the same in both applications.
#:
#: Only `green` actually differs, and it differs a lot: Qt reads the SVG palette, where `green` is
#: the dark `#008000`, while ds9 draws the neon `#00ff00`. A file saying `color=green` — which is
#: ds9's default, so most files — was being drawn in a colour ds9 never uses. The rest are listed
#: so the set is explicit rather than a single mysterious exception.
DS9_COLORS = {
    "green": "#00ff00",
    "red": "#ff0000",
    "blue": "#0000ff",
    "cyan": "#00ffff",
    "magenta": "#ff00ff",
    "yellow": "#ffff00",
    "white": "#ffffff",
    "black": "#000000",
}

#: What a new region is drawn in, matching ds9's default.
DEFAULT_COLOR = "green"


def resolve_color(name):
    """A colour string Qt will draw the way ds9 does.

    ds9 colour *names* are kept in the model and in saved files, so what is written stays
    idiomatic and round-trips; they are resolved to RGB only for painting.
    """
    return DS9_COLORS.get(str(name).strip().lower(), name)


class _InlineList(list):
    """A list YAML writes on one line, so a channel range reads as `z_range: [120, 180]`.

    Still a plain `list` to every consumer; only the dumper below treats it differently.
    """


class _Dumper(yaml.SafeDumper):
    """`SafeDumper` — so only plain data is ever emitted — plus inline short lists."""


_Dumper.add_representer(
    _InlineList,
    lambda dumper, data: dumper.represent_sequence('tag:yaml.org,2002:seq', data,
                                                   flow_style=True))


class RegionFormatError(ValueError):
    """A region file could not be read, with every problem found listed in the message.

    One exception type covers a YAML syntax error, a wrong format marker and a bad field, so
    a caller showing a dialog has exactly one thing to catch.
    """


def _is_number(value):
    """True for a real, finite number. `bool` is excluded deliberately: `True` is an `int`."""
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return False
    return value == value and value not in (float('inf'), float('-inf'))


@dataclass(kw_only=True)
class SkyAnchor:
    """Where a region sits on the sky, recorded alongside its pixel geometry.

    `size_arcsec` / `size2_arcsec` hold whatever sizes the shape has — a circle's radius, a
    box's width and height, an arrow's length — and `angle_deg` its orientation east of north.
    A shape without sizes leaves them unset.
    """

    ra_deg: float
    dec_deg: float
    size_arcsec: float | None = None
    size2_arcsec: float | None = None
    angle_deg: float | None = None

    def to_dict(self):
        data = {"ra_deg": float(self.ra_deg), "dec_deg": float(self.dec_deg)}
        for name in ("size_arcsec", "size2_arcsec", "angle_deg"):
            value = getattr(self, name)
            if value is not None:
                data[name] = float(value)
        return data

    @classmethod
    def from_dict(cls, data, problems, where):
        if not isinstance(data, dict):
            problems.append(f"{where}: sky must be a mapping, found {type(data).__name__}")
            return None

        unknown = set(data) - {"ra_deg", "dec_deg", "size_arcsec", "size2_arcsec", "angle_deg"}
        if unknown:
            problems.append(f"{where}: unknown sky field(s) {sorted(unknown)}")
            return None

        for required in ("ra_deg", "dec_deg"):
            if not _is_number(data.get(required)):
                problems.append(f"{where}: sky needs a numeric {required}")
                return None

        for optional in ("size_arcsec", "size2_arcsec", "angle_deg"):
            if optional in data and data[optional] is not None and not _is_number(data[optional]):
                problems.append(f"{where}: sky {optional} must be a number")
                return None

        return cls(**{k: (float(v) if v is not None else None) for k, v in data.items()})


@dataclass(kw_only=True)
class Region:
    """Shared attributes. Subclasses add their geometry and declare `TYPE` and `GEOMETRY`.

    Keyword-only because a dataclass cannot otherwise put a subclass's mandatory geometry
    after the base class's defaulted styling — and `Circle(x=31.5, y=9.5, radius=4)` reads
    better than three bare numbers anyway.

    `text` is a label on any shape, following ds9, where `text={...}` is a property rather
    than a shape of its own. On a `Text` region it *is* the content.
    """

    #: Shape name in the file. Set by each subclass.
    TYPE = None
    #: Geometry field names, in the order they are written.
    GEOMETRY = ()

    x: float
    y: float
    text: str = ""
    color: str = DEFAULT_COLOR
    line_width: int = 2
    dash: bool = False
    font_size: int = 12
    tag: str = ""
    visible: bool = True
    frame: str = "image"
    z_range: tuple | None = None
    sky: SkyAnchor | None = None

    def to_dict(self, style=None):
        """A plain dict for YAML, omitting anything left at its default.

        `style` is the file's `style:` block, which overrides the built-in defaults for the
        formatting fields — so a region writes `color:` only when it differs from what the file
        says at the top, and never relies on this program's idea of green.

        `frame` is written even when it is the default: the geometry above it is pixels and the
        `sky` block below it is degrees, so a file can hold a region in both frames at once, and
        which of the two is meant should never have to be inferred from a missing key.
        """
        data = {"type": self.TYPE}
        for name in self.GEOMETRY:
            data[name] = float(getattr(self, name))

        defaults = dict(_defaults(type(self)))
        defaults.update(style or {})
        for name in COMMON_ORDER:
            value = getattr(self, name)
            if value == defaults.get(name) and name not in ALWAYS_WRITTEN:
                continue
            if name == "sky":
                data[name] = value.to_dict()
            elif name == "z_range":
                data[name] = _InlineList([int(value[0]), int(value[1])])
            else:
                data[name] = value
        return data


@dataclass(kw_only=True)
class Circle(Region):
    """A circle of `radius` pixels centred on `(x, y)`."""

    TYPE = "circle"
    GEOMETRY = ("x", "y", "radius")

    radius: float


@dataclass(kw_only=True)
class Box(Region):
    """A rectangle centred on `(x, y)`, rotated `angle` degrees CCW about its centre."""

    TYPE = "box"
    GEOMETRY = ("x", "y", "width", "height", "angle")

    width: float
    height: float
    angle: float = 0.0


@dataclass(kw_only=True)
class Arrow(Region):
    """An arrow of `length` pixels from `(x, y)` pointing `angle` degrees CCW from +X.

    Stored as tail-plus-heading rather than as two endpoints to match ds9's
    `vector(x, y, length, angle)`, which is the only encoding ds9 accepts for an arrow
    (`TODO_regions.md`). `end` converts for anything that would rather have two points.
    """

    TYPE = "arrow"
    GEOMETRY = ("x", "y", "length", "angle")

    length: float
    angle: float = 0.0

    @property
    def end(self):
        """The arrow head, as `(x, y)` in orig coordinates."""
        import math
        radians = math.radians(self.angle)
        return (self.x + self.length * math.cos(radians),
                self.y + self.length * math.sin(radians))

    @classmethod
    def from_points(cls, x0, y0, x1, y1, **attributes):
        """Build from tail and head, for a two-handled ROI."""
        import math
        dx, dy = x1 - x0, y1 - y0
        return cls(x=x0, y=y0, length=math.hypot(dx, dy),
                   angle=math.degrees(math.atan2(dy, dx)) % 360.0, **attributes)


@dataclass(kw_only=True)
class Text(Region):
    """A label at `(x, y)`, rotated `angle` degrees. The content is `text`, and is required."""

    TYPE = "text"
    GEOMETRY = ("x", "y", "angle")

    angle: float = 0.0


#: Every shape, by the name used in the file.
REGION_TYPES = {cls.TYPE: cls for cls in (Circle, Box, Arrow, Text)}


def sizes_of(region):
    """`(size, size2)` for a region: whatever its shape calls its dimensions, or None.

    A circle has a radius, a box a width and a height, an arrow a length, a label neither.
    """
    if isinstance(region, Circle):
        return region.radius, None
    if isinstance(region, Box):
        return region.width, region.height
    if isinstance(region, Arrow):
        return region.length, None
    return None, None


def _defaults(cls):
    """`{field: default}` for a region class, for omitting unchanged values on write."""
    return {f.name: f.default for f in fields(cls) if f.default is not MISSING}


def default_style():
    """The formatting an omitted key means, taken from the model rather than restated here."""
    defaults = _defaults(Region)
    return {name: defaults[name] for name in STYLE_FIELDS}


def _required(cls):
    """Field names with no default — the ones a file must supply."""
    return tuple(f.name for f in fields(cls)
                 if f.default is MISSING and f.default_factory is MISSING)


@dataclass
class RegionList:
    """A file's worth of regions, plus the provenance and formatting written into it."""

    regions: list = None
    written_by: str = ""
    source: str = ""
    #: The file's `style:` block — what an omitted formatting key means. `None` writes the
    #: built-in defaults, which is what a file saved from the viewer carries.
    style: dict = None

    def __post_init__(self):
        if self.regions is None:
            self.regions = []

    def __len__(self):
        return len(self.regions)

    def __iter__(self):
        return iter(self.regions)

    def to_dict(self):
        data = {"format": FORMAT}
        if self.written_by:
            data["written_by"] = self.written_by
        if self.source:
            data["source"] = self.source

        style = dict(default_style())
        style.update(self.style or {})
        data["style"] = style
        data["regions"] = [region.to_dict(style) for region in self.regions]
        return data

    def to_yaml(self):
        """Serialize to YAML text.

        `sort_keys=False` keeps the declared order — `type` and geometry first — because the
        format is meant to be readable and hand-editable. `default_flow_style=False` keeps one
        field per line for the same reason.
        """
        return yaml.dump(self.to_dict(), Dumper=_Dumper, sort_keys=False,
                         default_flow_style=False, allow_unicode=True)

    def save(self, path):
        Path(path).write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def from_yaml(cls, text, where="region file"):
        """Parse YAML text, reporting *every* problem found rather than only the first.

        A region file is something a user may well have edited by hand, so being told about
        one bad field at a time, five saves in a row, is the wrong experience.
        """
        if looks_like_ds9(text):
            # By far the likeliest wrong file to be handed here, and as YAML it parses to a
            # bare string, which would otherwise produce a baffling "found str".
            raise RegionFormatError(
                f"{where} is a ds9 .reg file, not a QuickLook 3 region file. Load it with "
                "Region -> Load ds9 Regions instead.")

        try:
            raw = yaml.safe_load(text)
        except yaml.YAMLError as exc:
            raise RegionFormatError(f"{where} is not valid YAML: {exc}") from exc

        if raw is None:
            raise RegionFormatError(f"{where} is empty")
        if not isinstance(raw, dict):
            raise RegionFormatError(
                f"{where} should be a mapping with 'format' and 'regions' keys, "
                f"found {type(raw).__name__}")

        _check_format_marker(raw.get("format"), where)

        unknown = set(raw) - {"format", "written_by", "source", "style", "regions"}
        if unknown:
            raise RegionFormatError(
                f"{where} has unknown top-level key(s) {sorted(unknown)}; "
                "expected 'format', 'written_by', 'source', 'style', 'regions'")

        style = _style_from_dict(raw.get("style"), where)

        entries = raw.get("regions")
        if entries is None:
            entries = []
        if not isinstance(entries, list):
            raise RegionFormatError(
                f"{where}: 'regions' should be a list, found {type(entries).__name__}")

        problems = []
        regions = []
        for index, entry in enumerate(entries):
            region = _region_from_dict(entry, index, problems, style)
            if region is not None:
                regions.append(region)

        if problems:
            raise RegionFormatError(
                f"{where} could not be read:\n  " + "\n  ".join(problems))

        return cls(regions=regions,
                   written_by=str(raw.get("written_by") or ""),
                   source=str(raw.get("source") or ""),
                   style=style)

    @classmethod
    def load(cls, path):
        """Read a region file. Raises `RegionFormatError`, or `OSError` if unreadable."""
        path = Path(path)
        return cls.from_yaml(path.read_text(encoding="utf-8"), where=path.name)


#: Shape keywords that only appear in a ds9 region file, used to recognise one.
_DS9_SHAPES = ("circle(", "box(", "ellipse(", "polygon(", "point(", "line(", "vector(",
               "text(", "annulus(")


def looks_like_ds9(text):
    """True for something that is plainly a ds9 `.reg` file.

    Either the header ds9 writes, or a coordinate-system line followed by a shape call. Both
    are cheap to spot and neither can occur in our own format, where every shape is a YAML
    mapping.
    """
    lowered = text.lower()
    if "region file format: ds9" in lowered:
        return True

    has_frame_line = any(line.strip() in ("image", "physical", "fk5", "icrs", "galactic")
                         for line in lowered.splitlines())
    has_shape = any(shape in lowered.replace(" ", "") for shape in _DS9_SHAPES)
    return has_frame_line and has_shape


def _check_format_marker(marker, where):
    if marker is None:
        raise RegionFormatError(
            f"{where} has no 'format' key, so it is not a QuickLook 3 region file "
            f"(expected 'format: {FORMAT}'). ds9 .reg files are opened as ds9 regions "
            "instead.")

    name, _, version = str(marker).partition("/")
    if name != FORMAT_NAME:
        raise RegionFormatError(
            f"{where} declares format '{marker}', which is not a QuickLook 3 region file "
            f"(expected '{FORMAT}')")

    try:
        major = int(version)
    except ValueError:
        raise RegionFormatError(
            f"{where} declares format '{marker}' with an unreadable version number; "
            f"this build understands '{FORMAT}'") from None

    if major > FORMAT_VERSION:
        raise RegionFormatError(
            f"{where} was written by a newer version of QuickLook 3 (format '{marker}'); "
            f"this build understands '{FORMAT}'. Upgrade, or export from the other build "
            "as a ds9 .reg file.")


def _style_from_dict(data, where):
    """The file's `style:` block, validated, filled in from the built-in defaults.

    Raises rather than collecting problems: everything below it is read against this block, so a
    bad one makes every region's formatting meaningless and there is nothing useful to go on with.
    """
    style = dict(default_style())
    if data is None:
        return style

    if not isinstance(data, dict):
        raise RegionFormatError(
            f"{where}: 'style' should be a mapping of {list(STYLE_FIELDS)}, "
            f"found {type(data).__name__}")

    unknown = set(data) - set(STYLE_FIELDS)
    if unknown:
        raise RegionFormatError(
            f"{where}: 'style' has unknown key(s) {sorted(unknown)}; "
            f"allowed: {list(STYLE_FIELDS)}")

    problems = []
    checked = _common_from_dict(data, Region, f"{where}: 'style'", problems)
    if problems:
        raise RegionFormatError(f"{where} could not be read:\n  " + "\n  ".join(problems))

    if "color" in data and not str(data["color"]).strip():
        raise RegionFormatError(f"{where}: 'style' color must not be blank")

    style.update({name: value for name, value in checked.items() if name in STYLE_FIELDS})
    return style


def _region_from_dict(entry, index, problems, style=None):
    """Build one region, appending to `problems` instead of raising."""
    where = f"region {index}"
    if not isinstance(entry, dict):
        problems.append(f"{where}: should be a mapping, found {type(entry).__name__}")
        return None

    type_name = entry.get("type")
    if type_name is None:
        problems.append(f"{where}: no 'type'; expected one of {sorted(REGION_TYPES)}")
        return None
    cls = REGION_TYPES.get(str(type_name))
    if cls is None:
        problems.append(
            f"{where}: unknown type '{type_name}'; expected one of {sorted(REGION_TYPES)}")
        return None

    where = f"region {index} ({cls.TYPE})"
    allowed = set(cls.GEOMETRY) | set(COMMON_ORDER)
    unknown = set(entry) - allowed - {"type"}
    if unknown:
        # Rejected rather than ignored: a silently dropped `colour:` in a hand-edited file
        # looks like the setting simply does not work.
        problems.append(
            f"{where}: unknown field(s) {sorted(unknown)}; allowed here: {sorted(allowed)}")
        return None

    # Every bad geometry field is reported, not just the first: a region missing both its
    # centre and its radius should say so once.
    values = {}
    bad = False
    for name in _required(cls):
        if not _is_number(entry.get(name)):
            problems.append(f"{where}: needs a numeric '{name}'")
            bad = True
        else:
            values[name] = float(entry[name])

    if "angle" in cls.GEOMETRY and "angle" in entry:
        if not _is_number(entry["angle"]):
            problems.append(f"{where}: 'angle' must be a number")
            bad = True
        else:
            values["angle"] = float(entry["angle"])

    for name in ("radius", "width", "height", "length"):
        if name in values and values[name] <= 0:
            problems.append(f"{where}: '{name}' must be greater than zero, "
                            f"found {values[name]:g}")
            bad = True

    if bad:
        return None

    common = _common_from_dict(entry, cls, where, problems)
    if common is None:
        return None
    # The file's own formatting stands in for whatever this region did not override, so a
    # region's appearance is fixed by the file and not by this build's built-in defaults.
    for name, value in (style or {}).items():
        common.setdefault(name, value)
    values.update(common)

    if cls is Text and not values.get("text"):
        problems.append(f"{where}: a text region needs a non-empty 'text'")
        return None

    return cls(**values)


def _common_from_dict(entry, cls, where, problems):
    """Validate the shared attributes, returning them as kwargs or None on failure."""
    values = {}

    for name in ("text", "color", "tag"):
        if name in entry:
            value = entry[name]
            if not isinstance(value, str):
                problems.append(f"{where}: '{name}' must be text, "
                                f"found {type(value).__name__}")
                return None
            values[name] = value

    if "color" in values and not values["color"].strip():
        problems.append(f"{where}: 'color' must not be blank")
        return None

    for name in ("dash", "visible"):
        if name in entry:
            if not isinstance(entry[name], bool):
                problems.append(f"{where}: '{name}' must be true or false, "
                                f"found {entry[name]!r}")
                return None
            values[name] = entry[name]

    for name in ("line_width", "font_size"):
        if name in entry:
            value = entry[name]
            if isinstance(value, bool) or not isinstance(value, int) or value < 1:
                problems.append(f"{where}: '{name}' must be a whole number of at least 1, "
                                f"found {value!r}")
                return None
            values[name] = value

    if "frame" in entry:
        if entry["frame"] not in VALID_FRAMES:
            problems.append(f"{where}: 'frame' must be one of {list(VALID_FRAMES)}, "
                            f"found {entry['frame']!r}")
            return None
        values["frame"] = entry["frame"]

    if entry.get("z_range") is not None:
        z_range = _z_range_from_value(entry["z_range"], where, problems)
        if z_range is None:
            return None
        values["z_range"] = z_range

    if entry.get("sky") is not None:
        sky = SkyAnchor.from_dict(entry["sky"], problems, where)
        if sky is None:
            return None
        values["sky"] = sky

    # Only what the file actually specified is returned. Filling in the class defaults here
    # would clobber geometry the caller has already parsed — `angle` has a default, so it
    # came back as 0 from every file that set it until a round-trip test caught it. The
    # dataclass supplies the defaults for whatever is left out.
    return values


def _z_range_from_value(value, where, problems):
    """A `[zmin, zmax]` channel range: two whole numbers, in order, not negative.

    Reversed ranges are refused rather than quietly swapped — a reversed range elsewhere in
    this application silently blanked the image (`BUGS.md` B12), and a hand-edited file
    deserves to be told.
    """
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        problems.append(f"{where}: 'z_range' must be a list of two channel numbers, "
                        f"found {value!r}")
        return None

    limits = []
    for limit in value:
        if isinstance(limit, bool) or not isinstance(limit, int):
            problems.append(f"{where}: 'z_range' entries must be whole numbers, "
                            f"found {limit!r}")
            return None
        limits.append(limit)

    if limits[0] < 0:
        problems.append(f"{where}: 'z_range' cannot start below channel 0, found {limits[0]}")
        return None
    if limits[0] > limits[1]:
        problems.append(f"{where}: 'z_range' is reversed ({limits[0]} > {limits[1]})")
        return None

    return (limits[0], limits[1])

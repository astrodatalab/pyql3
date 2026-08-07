"""The region model and its native YAML format (`TODO_regions.md` Phase 1).

No Qt and no WCS here — that is the point of keeping the model plain. What matters is that a
file written today reads back identically, and that a file someone has hand-edited fails with
a message naming the field, rather than loading half a region or crashing somewhere later.
"""
import math

import pytest
import yaml

from pyql3.core.regions_model import (
    FORMAT,
    Arrow,
    Box,
    Circle,
    Region,
    RegionFormatError,
    RegionList,
    SkyAnchor,
    Text,
)


def one_of_each():
    """One fully-populated region of every type, for round-trip tests."""
    return [
        Circle(x=31.5, y=9.5, radius=4.0, text="src A", color="red", line_width=3, tag="grp1"),
        Box(x=10.0, y=12.0, width=8.0, height=4.0, angle=20.0, dash=True, z_range=(100, 200)),
        Arrow(x=2.0, y=3.0, length=12.0, angle=135.0, color="#00ffcc"),
        Text(x=5.0, y=6.0, text="a label with (parens)", angle=30.0, font_size=18,
             visible=False),
    ]


def reload(regions, **metadata):
    """Write and read back, which is the operation every one of these tests cares about."""
    return RegionList.from_yaml(RegionList(regions=list(regions), **metadata).to_yaml())


# ------------------------------------------------------------------- round trips

def test_every_shape_survives_a_round_trip():
    original = one_of_each()
    restored = reload(original)

    assert len(restored) == len(original)
    for before, after in zip(original, restored, strict=True):
        assert after == before, f"{type(before).__name__} changed across a round trip"


def test_metadata_survives_a_round_trip():
    restored = reload(one_of_each(), written_by="QuickLook 3 v9.9", source="/data/cube.fits")
    assert restored.written_by == "QuickLook 3 v9.9"
    assert restored.source == "/data/cube.fits"


def test_a_sky_anchor_survives_a_round_trip():
    """Pixels stay authoritative, but the sky record has to come back intact."""
    sky = SkyAnchor(ra_deg=266.41681, dec_deg=-29.00782, size_arcsec=0.14, angle_deg=31.0)
    restored, = reload([Circle(x=1.0, y=2.0, radius=3.0, sky=sky, frame="sky")])

    assert restored.sky == sky
    assert restored.frame == "sky"


def test_an_empty_list_round_trips():
    restored = reload([])
    assert len(restored) == 0
    assert list(restored) == []


def test_fractional_coordinates_are_preserved_exactly():
    """Region centres land on half-pixels constantly; YAML must not round them."""
    original = Circle(x=31.499999, y=-0.5, radius=0.125)
    restored, = reload([original])
    assert (restored.x, restored.y, restored.radius) == (31.499999, -0.5, 0.125)


# --------------------------------------------------------------- what gets written

def test_defaults_are_left_out_of_the_file():
    """A hand-editable format should not be 90% restating the defaults."""
    text = RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0)]).to_yaml()
    entry, = yaml.safe_load(text)["regions"]

    assert entry == {"type": "circle", "x": 1.0, "y": 2.0, "radius": 3.0, "frame": "image"}


def test_the_frame_is_written_even_when_it_is_the_default():
    """A file can hold a region in both frames at once, so which one is meant is never implied.

    The geometry above it is pixels and a `sky` block below it is degrees; a reader — human or
    otherwise — should not have to know that a missing key means pixels.
    """
    both = Circle(x=1.0, y=2.0, radius=3.0, sky=SkyAnchor(ra_deg=266.4, dec_deg=-29.0))
    entries = yaml.safe_load(RegionList(regions=[both]).to_yaml())["regions"]

    assert entries[0]["frame"] == "image"
    assert entries[0]["sky"]["ra_deg"] == 266.4


# ------------------------------------------------------------------ the style block

def test_every_file_records_the_formatting_it_was_written_with():
    """ds9 writes a `global` line; this is the same idea, and for the same reason.

    Without it a region that took the defaults has no colour, width or font size anywhere in the
    file, and its appearance depends on what this build happens to default to when it is read.
    """
    written = yaml.safe_load(RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0)]).to_yaml())

    assert written["style"] == {"color": "green", "line_width": 2, "dash": False,
                                "font_size": 12}


def test_the_style_block_supplies_what_a_region_leaves_out():
    """Editing one block restyles everything that did not override it, as ds9's global does."""
    text = """format: pyql3-regions/1
style:
  color: cyan
  line_width: 4
  dash: true
  font_size: 20
regions:
- {type: circle, x: 1.0, y: 2.0, radius: 3.0}
- {type: circle, x: 4.0, y: 5.0, radius: 6.0, color: red}
"""
    plain, overridden = RegionList.from_yaml(text).regions

    assert (plain.color, plain.line_width, plain.dash, plain.font_size) == ("cyan", 4, True, 20)
    assert overridden.color == "red", "a region's own value wins over the block"
    assert overridden.line_width == 4, "...and the rest still comes from the block"


def test_a_styled_file_round_trips_what_it_shows():
    """What is written back is what was on screen, whatever block it was read under."""
    text = """format: pyql3-regions/1
style: {color: cyan}
regions:
- {type: circle, x: 1.0, y: 2.0, radius: 3.0}
"""
    once = RegionList.from_yaml(text)
    twice = RegionList.from_yaml(RegionList(regions=once.regions).to_yaml())

    assert twice.regions[0].color == "cyan", "the colour must survive a save under new defaults"
    assert twice.regions == once.regions


@pytest.mark.parametrize("block,complaint", [
    ("style: {colour: cyan}", "unknown key"),
    ("style: {line_width: 0}", "at least 1"),
    ("style: {dash: yes please}", "true or false"),
    ("style: {color: '  '}", "must not be blank"),
    ("style: [cyan]", "should be a mapping"),
])
def test_a_bad_style_block_is_refused(block, complaint):
    """Everything below it is read against this block, so there is no going on without it."""
    text = f"format: pyql3-regions/1\n{block}\nregions: []\n"

    with pytest.raises(RegionFormatError, match=complaint):
        RegionList.from_yaml(text)


def test_non_default_values_are_written():
    text = RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0, color="cyan", visible=False)]).to_yaml()
    entry, = yaml.safe_load(text)["regions"]

    assert entry["color"] == "cyan"
    assert entry["visible"] is False


def test_the_file_declares_its_format_and_leads_with_type_and_geometry():
    text = RegionList(regions=[Box(x=1.0, y=2.0, width=3.0, height=4.0, angle=5.0,
                                   color="red")]).to_yaml()

    assert yaml.safe_load(text)["format"] == FORMAT
    # Readability: the file's own formatting up top, as ds9 puts its `global` line, then shape
    # and geometry before styling, in declaration order.
    keys = [line.split(":")[0].strip("- ") for line in text.splitlines() if ":" in line]
    assert keys[:2] == ["format", "style"]
    start = keys.index("regions")
    assert keys[start:start + 5] == ["regions", "type", "x", "y", "width"]


def test_a_channel_range_is_written_on_one_line():
    """`z_range: [120, 180]` rather than three lines of block list, for hand editing."""
    text = RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0, z_range=(120, 180))]).to_yaml()

    assert "z_range: [120, 180]" in text, text
    restored, = RegionList.from_yaml(text)
    assert restored.z_range == (120, 180)


def test_empty_metadata_is_not_written():
    data = yaml.safe_load(RegionList(regions=[]).to_yaml())
    assert "written_by" not in data
    assert "source" not in data


def test_saved_file_is_utf8_and_reloads(tmp_path):
    path = tmp_path / "regions.yml"
    original = [Text(x=1.0, y=2.0, text="µm ✱ naïve")]
    RegionList(regions=original, written_by="QuickLook 3").save(path)

    restored = RegionList.load(path)
    assert restored.regions == original
    assert "µm" in path.read_text(encoding="utf-8")


# ----------------------------------------------------------------------- geometry

def test_an_arrow_converts_between_heading_and_endpoints():
    """Stored as ds9 does (tail, length, angle); edited as two handles."""
    arrow = Arrow(x=10.0, y=10.0, length=5.0, angle=90.0)
    assert arrow.end == pytest.approx((10.0, 15.0))

    rebuilt = Arrow.from_points(3.0, 4.0, 3.0, 9.0, color="red")
    assert (rebuilt.x, rebuilt.y) == (3.0, 4.0)
    assert rebuilt.length == pytest.approx(5.0)
    assert rebuilt.angle == pytest.approx(90.0)
    assert rebuilt.color == "red"


@pytest.mark.parametrize("angle", [0, 30, 90, 180, 270, 359])
def test_an_arrow_round_trips_through_its_endpoints(angle):
    original = Arrow(x=4.0, y=7.0, length=6.0, angle=float(angle))
    rebuilt = Arrow.from_points(original.x, original.y, *original.end)

    assert rebuilt.length == pytest.approx(original.length)
    assert math.cos(math.radians(rebuilt.angle - original.angle)) == pytest.approx(1.0)


# ------------------------------------------------------------------- bad input

def test_a_file_without_a_format_marker_is_refused():
    with pytest.raises(RegionFormatError, match="no 'format' key"):
        RegionList.from_yaml("regions: []")


def test_a_foreign_format_marker_is_refused():
    with pytest.raises(RegionFormatError, match="not a QuickLook 3 region file"):
        RegionList.from_yaml("format: something-else/1\nregions: []")


def test_a_newer_format_version_is_refused_with_a_way_forward():
    """Refuse rather than half-read, and say what to do about it."""
    with pytest.raises(RegionFormatError) as caught:
        RegionList.from_yaml(f"format: pyql3-regions/{99}\nregions: []")

    message = str(caught.value)
    assert "newer version" in message
    assert ".reg" in message, "should suggest the interchange format"


def test_a_ds9_reg_file_is_refused_with_a_pointer_to_the_right_reader():
    """The likeliest wrong file to hand this loader is a ds9 region file."""
    with pytest.raises(RegionFormatError, match="ds9"):
        RegionList.from_yaml("# Region file format: DS9 version 4.1\nimage\ncircle(1,2,3)\n")


@pytest.mark.parametrize("label", [
    "circle(1,2,3)",       # a label that quotes ds9 syntax
    "image",               # a label that is a ds9 coordinate-system keyword
    "vector(0,0,1,0) and box(1,1,2,2)",
])
def test_our_own_files_are_never_mistaken_for_ds9(label):
    """The ds9 sniff must not reject a valid file whose *labels* look like ds9 syntax."""
    restored, = reload([Text(x=1.0, y=2.0, text=label)])
    assert restored.text == label


def test_a_ds9_file_without_the_ds9_header_is_still_recognised():
    """ds9 will happily load a file with no header line, so we have to recognise one too."""
    with pytest.raises(RegionFormatError, match="ds9"):
        RegionList.from_yaml("image\ncircle(100,100,20)\nbox(1,2,3,4,5)\n")


def test_malformed_yaml_is_reported_as_a_region_error():
    """Callers catch one exception type, whatever the underlying failure."""
    with pytest.raises(RegionFormatError, match="not valid YAML"):
        RegionList.from_yaml("format: pyql3-regions/1\nregions: [ unclosed")


def test_an_empty_file_is_refused():
    with pytest.raises(RegionFormatError, match="empty"):
        RegionList.from_yaml("")


def test_a_top_level_list_is_refused():
    with pytest.raises(RegionFormatError, match="should be a mapping"):
        RegionList.from_yaml("- circle\n- box\n")


def test_regions_must_be_a_list():
    with pytest.raises(RegionFormatError, match="'regions' should be a list"):
        RegionList.from_yaml(f"format: {FORMAT}\nregions: {{}}\n")


def test_a_missing_regions_key_is_an_empty_file_not_an_error():
    assert len(RegionList.from_yaml(f"format: {FORMAT}\n")) == 0


def test_an_unknown_top_level_key_is_refused():
    with pytest.raises(RegionFormatError, match="unknown top-level key"):
        RegionList.from_yaml(f"format: {FORMAT}\nregionz: []\n")


@pytest.mark.parametrize("entry,expected", [
    ("type: sphere\n    x: 1\n    y: 2", "unknown type 'sphere'"),
    ("x: 1\n    y: 2", "no 'type'"),
    ("type: circle\n    x: 1", "needs a numeric 'radius'"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: wide", "needs a numeric 'radius'"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 0", "greater than zero"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: -3", "greater than zero"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    colour: red", "unknown field"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    color: 7", "'color' must be text"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    color: ''", "must not be blank"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    visible: yes please",
     "must be true or false"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    line_width: 0", "at least 1"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    font_size: 1.5", "at least 1"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    frame: galactic", "'frame' must be"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    z_range: 100", "list of two"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    z_range: [200, 100]", "reversed"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    z_range: [-1, 5]",
     "cannot start below channel 0"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    z_range: [1.5, 5]",
     "whole numbers"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    sky: 5", "sky must be a mapping"),
    ("type: circle\n    x: 1\n    y: 2\n    radius: 3\n    sky: {ra_deg: 1}",
     "sky needs a numeric dec_deg"),
    ("type: box\n    x: 1\n    y: 2\n    width: 3", "needs a numeric 'height'"),
    ("type: arrow\n    x: 1\n    y: 2", "needs a numeric 'length'"),
    ("type: text\n    x: 1\n    y: 2", "needs a non-empty 'text'"),
])
def test_bad_fields_are_reported_by_name(entry, expected):
    text = f"format: {FORMAT}\nregions:\n  - {entry}\n"
    with pytest.raises(RegionFormatError, match=expected):
        RegionList.from_yaml(text)


def test_a_boolean_is_not_accepted_as_a_number():
    """`isinstance(True, int)` is True in Python, which would let `x: true` through as 1.0."""
    with pytest.raises(RegionFormatError, match="needs a numeric 'x'"):
        RegionList.from_yaml(
            f"format: {FORMAT}\nregions:\n  - type: circle\n    x: true\n    y: 2\n"
            "    radius: 3\n")


@pytest.mark.parametrize("value", [".nan", ".inf", "-.inf"])
def test_non_finite_coordinates_are_refused(value):
    """A NaN centre would place a region nowhere and break every downstream calculation."""
    with pytest.raises(RegionFormatError, match="needs a numeric 'x'"):
        RegionList.from_yaml(
            f"format: {FORMAT}\nregions:\n  - type: circle\n    x: {value}\n    y: 2\n"
            "    radius: 3\n")


def test_every_problem_is_reported_at_once():
    """Being told about one bad field per save, five saves running, is the wrong experience."""
    text = f"""format: {FORMAT}
regions:
  - type: circle
    x: 1
    y: 2
  - type: sphere
    x: 1
    y: 2
  - type: box
    x: 1
    y: 2
    width: 3
    height: 4
    z_range: [9, 4]
"""
    with pytest.raises(RegionFormatError) as caught:
        RegionList.from_yaml(text)

    message = str(caught.value)
    assert "region 0 (circle)" in message
    assert "region 1" in message and "sphere" in message
    assert "region 2 (box)" in message and "reversed" in message


def test_the_error_names_the_file():
    with pytest.raises(RegionFormatError, match="my_regions.yml"):
        RegionList.from_yaml("regions: []", where="my_regions.yml")


def test_load_reports_a_missing_file_as_an_os_error(tmp_path):
    """Distinct from a format problem: the caller may want to offer the file dialog again."""
    with pytest.raises(OSError):
        RegionList.load(tmp_path / "nope.yml")


def test_load_names_the_file_in_a_format_error(tmp_path):
    path = tmp_path / "broken.yml"
    path.write_text("regions: []")
    with pytest.raises(RegionFormatError, match="broken.yml"):
        RegionList.load(path)


# ------------------------------------------------------------------ safety

def test_loading_never_constructs_arbitrary_objects():
    """`yaml.load` would build a Python object out of a tag; a region file is only data."""
    hostile = f"""format: {FORMAT}
regions:
  - type: circle
    x: !!python/object/apply:os.system ['echo pwned']
    y: 2
    radius: 3
"""
    with pytest.raises(RegionFormatError):
        RegionList.from_yaml(hostile)


def test_a_region_list_is_iterable_and_sized():
    regions = one_of_each()
    region_list = RegionList(regions=regions)
    assert len(region_list) == 4
    assert [type(r) for r in region_list] == [Circle, Box, Arrow, Text]


def test_regions_default_to_an_empty_list():
    """A shared mutable default would leak regions between files."""
    first, second = RegionList(), RegionList()
    first.regions.append(Circle(x=1.0, y=2.0, radius=3.0))
    assert len(second) == 0


def test_shapes_are_keyword_only():
    """Positional construction would silently reorder geometry as fields are added."""
    with pytest.raises(TypeError):
        Circle(1.0, 2.0, 3.0)


def test_the_base_class_is_not_a_usable_shape():
    assert Region.TYPE is None


# --------------------------------------------------------------- ds9 colours

def test_ds9_green_is_neon_not_qt_green():
    """Qt reads the SVG palette, where `green` is the dark #008000; ds9 draws #00ff00.

    A ds9 file saying `color=green` — its default, so most files — was being drawn in a colour ds9
    never uses.
    """
    from pyql3.core.regions_model import resolve_color

    assert resolve_color("green") == "#00ff00"
    assert resolve_color("GREEN") == "#00ff00", "ds9 colour names are not case sensitive"


@pytest.mark.parametrize("name,expected", [
    ("red", "#ff0000"), ("blue", "#0000ff"), ("cyan", "#00ffff"),
    ("magenta", "#ff00ff"), ("yellow", "#ffff00"), ("white", "#ffffff"),
])
def test_the_other_ds9_names_resolve_to_the_same_colours_qt_uses(name, expected):
    from pyql3.core.regions_model import resolve_color

    assert resolve_color(name) == expected


def test_an_unknown_colour_is_passed_through(qapp=None):
    """Hex values and any other Qt-readable name are left alone."""
    from pyql3.core.regions_model import resolve_color

    assert resolve_color("#ff8800") == "#ff8800"
    assert resolve_color("orange") == "orange"


def test_a_new_region_is_ds9_green():
    assert Circle(x=1.0, y=2.0, radius=3.0).color == "green"
    from pyql3.core.regions_model import resolve_color
    assert resolve_color(Circle(x=1.0, y=2.0, radius=3.0).color) == "#00ff00"


def test_the_colour_name_is_what_gets_saved():
    """Names stay in the file so it reads as a ds9 file would, and round-trips unchanged."""
    text = RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0, color="green")]).to_yaml()
    assert "#00ff00" not in text, "the resolved RGB leaked into the saved file"

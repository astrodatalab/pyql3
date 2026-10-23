"""ds9 `.reg` import and export (`TODO_regions.md` Phase 2).

The `regions` package does most of the work, so these tests concentrate on the seams:

- the two things the library cannot do — `vector` (every ds9 arrow) and writing `textangle` —
  which this module hand-writes and hand-parses,
- the coordinate traps: ds9's 1-based pixels, sky frames going through
  `wcs.sub(['longitude','latitude'])`, and the fact that ds9's `image` frame means FITS axes 1
  and 2 while an OSIRIS cube is displayed on axes 3 and 2,
- and the promise that nothing is dropped in silence: whatever cannot be carried across appears
  in the `Report`.

The output rules being asserted here were checked against ds9 itself, not inferred; the
provenance is in `TODO_regions.md` under "ds9 format check".
"""
import astropy.units as u
import numpy as np
import pytest
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS

from pyql3.core.ds9_regions import from_ds9, to_ds9
from pyql3.core.regions_model import Arrow, Box, Circle, RegionFormatError, RegionList, Text
from pyql3.core.sky import CelestialMap

#: An OSIRIS cube's axis order: wavelength on FITS axis 1, Dec on 2, RA on 3.
OSIRIS_CTYPE = ['WAVE', 'DEC--TAN', 'RA---TAN']
#: ...displayed as RA against Dec, i.e. FITS axes 3 and 2.
OSIRIS_AXES = (2, 1)

#: A plain image: RA on axis 1, Dec on axis 2, which is also what ds9's image frame means.
IMAGE_AXES = (0, 1)

#: Sky coordinates go into a .reg file with 8 decimal places of degrees, which at these pixel
#: scales is ~1e-4 of a pixel. Round trips through the text are held to well under a pixel
#: rather than to floating-point equality.
SUB_PIXEL = 1e-3

#: ds9's header line, for files written out by hand here.
DS9_HEADER_LINE = "# Region file format: DS9 version 4.1\n"


def osiris_wcs():
    wcs = WCS(naxis=3)
    wcs.wcs.ctype = OSIRIS_CTYPE
    wcs.wcs.crval = [2.2, 34.0, 266.4]
    wcs.wcs.cdelt = [5e-4, 1e-4, -1e-4]
    wcs.wcs.crpix = [1, 10, 32]
    wcs.wcs.cunit = ['um', 'deg', 'deg']
    return wcs


def image_wcs():
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
    wcs.wcs.crval = [266.4, 34.0]
    wcs.wcs.cdelt = [-1e-4, 1e-4]
    wcs.wcs.crpix = [50, 50]
    wcs.wcs.cunit = ['deg', 'deg']
    return wcs


def sample_regions():
    return [
        Circle(x=31.0, y=9.0, radius=4.0, text="src A", color="red", line_width=3, tag="targets"),
        Box(x=18.0, y=9.0, width=10.0, height=6.0, angle=20.0, text="slit"),
        Text(x=20.0, y=14.0, text="knot", font_size=14),
        Arrow(x=4.0, y=4.0, length=12.0, angle=135.0, text="outflow", color="cyan"),
    ]


def shapes_by_type(region_list):
    return {type(region).__name__: region for region in region_list}


# ================================================================ writing

def test_written_file_has_the_shapes_and_the_hand_written_arrow():
    text, report = to_ds9(RegionList(regions=sample_regions()), axis_indices=IMAGE_AXES)

    assert "circle(" in text and "box(" in text and "text(" in text
    # The only arrow encoding ds9 accepts; the bare form makes it reject the whole file.
    assert "# vector(" in text
    assert "vector=1" in text
    assert not report.skipped, report.summary()


def test_ds9_pixel_numbers_are_one_based():
    """ds9 counts pixels from 1. `regions` applies that to what it writes; we must match it."""
    text, _ = to_ds9(RegionList(regions=[Circle(x=10.0, y=20.0, radius=5.0),
                                         Arrow(x=10.0, y=20.0, length=3.0, angle=0.0)]),
                     axis_indices=IMAGE_AXES)

    assert "circle(11.00000000,21.00000000,5.00000000)" in text
    assert "# vector(11.00000000,21.00000000" in text, text


def test_no_bare_vector_is_ever_written():
    """A bare `vector(...)` is a syntax error that makes ds9 refuse the entire file."""
    text, _ = to_ds9(RegionList(regions=[Arrow(x=1.0, y=1.0, length=5.0, angle=45.0)]),
                     axis_indices=IMAGE_AXES)

    for line in text.splitlines():
        if "vector(" in line:
            assert line.lstrip().startswith("#"), f"bare vector written: {line!r}"


def test_provenance_comment_is_written_after_the_header():
    text, _ = to_ds9(RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0)]),
                     axis_indices=IMAGE_AXES, written_by="QuickLook 3 v9.9")
    lines = text.splitlines()

    assert lines[0].startswith("# Region file format")
    assert lines[1] == "# written by QuickLook 3 v9.9"


def test_a_provenance_comment_starting_with_a_dash_is_refused():
    """A comment beginning with `-` (ds9's exclude prefix) makes ds9 reject the whole file."""
    text, _ = to_ds9(RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0)]),
                     axis_indices=IMAGE_AXES, written_by="-- v9.9 --")

    assert "written by" not in text
    for line in text.splitlines():
        assert not line.startswith("# -"), line


def test_style_and_labels_reach_the_file():
    text, _ = to_ds9(RegionList(regions=[
        Circle(x=1.0, y=2.0, radius=3.0, color="red", line_width=3, dash=True,
               text="src A", tag="grp1")]), axis_indices=IMAGE_AXES)

    assert "color=red" in text
    assert "width=3" in text
    assert "dash=1" in text
    assert "text={src A}" in text
    assert "tag={grp1}" in text


def test_a_rotated_label_keeps_its_angle_and_is_not_written_twice():
    """`regions` reads `textangle` but never writes it, so rotated text is hand-written."""
    text, _ = to_ds9(RegionList(regions=[Text(x=5.0, y=6.0, text="tilted", angle=30.0)]),
                     axis_indices=IMAGE_AXES)

    assert "textangle=30" in text
    assert text.count("text={tilted}") == 1, f"label written more than once:\n{text}"


def test_an_unrotated_label_goes_through_the_library():
    text, _ = to_ds9(RegionList(regions=[Text(x=5.0, y=6.0, text="flat")]),
                     axis_indices=IMAGE_AXES)

    assert "text={flat}" in text
    assert "textangle" not in text


def test_braces_in_a_label_cannot_break_the_property_syntax():
    """ds9 delimits property values with braces, so one inside a label would end it early."""
    text, _ = to_ds9(RegionList(regions=[
        Arrow(x=1.0, y=1.0, length=5.0, angle=0.0, text="a {brace} here")]),
        axis_indices=IMAGE_AXES)

    vector_line, = [line for line in text.splitlines() if "vector(" in line]
    assert vector_line.count("{") == 1 and vector_line.count("}") == 1, vector_line


def test_an_empty_region_list_still_declares_a_frame():
    """Otherwise ds9 has no coordinate system for anything appended later."""
    text, _ = to_ds9(RegionList(regions=[]), axis_indices=IMAGE_AXES)
    assert "image" in text.splitlines()


# ---------------------------------------------------------- frame selection

def test_image_frame_is_used_when_the_display_matches_ds9s_axes():
    text, report = to_ds9(RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0)]),
                          wcs=image_wcs(), axis_indices=IMAGE_AXES)

    assert "image" in text.splitlines()
    assert not report, report.summary()


def test_sky_frame_is_chosen_when_image_coordinates_would_not_line_up():
    """An OSIRIS cube shows FITS axes 3 and 2; ds9's image frame always means 1 and 2."""
    text, report = to_ds9(RegionList(regions=[Circle(x=31.0, y=9.0, radius=4.0)]),
                          wcs=osiris_wcs(), axis_indices=OSIRIS_AXES)

    assert "icrs" in text or "fk5" in text, text
    assert "image" not in text.splitlines()
    assert any("sky coordinates" in note for note in report.notes), report.summary()


def test_asking_for_image_coordinates_on_a_cube_warns_but_obeys():
    text, report = to_ds9(RegionList(regions=[Circle(x=31.0, y=9.0, radius=4.0)]),
                          wcs=osiris_wcs(), axis_indices=OSIRIS_AXES, frame="image")

    assert "image" in text.splitlines()
    assert any("will not line up" in note for note in report.notes), report.summary()


def test_asking_for_sky_without_a_wcs_falls_back_and_says_so():
    text, report = to_ds9(RegionList(regions=[Circle(x=1.0, y=2.0, radius=3.0)]),
                          wcs=None, axis_indices=IMAGE_AXES, frame="sky")

    assert "image" in text.splitlines()
    assert any("no WCS" in note for note in report.notes), report.summary()


def test_a_sky_export_puts_its_arrows_on_the_sky_too():
    """An arrow left in pixel coordinates would not follow the field, which defeats the export."""
    text, report = to_ds9(RegionList(regions=[
        Circle(x=31.0, y=9.0, radius=4.0),
        Arrow(x=4.0, y=4.0, length=12.0, angle=135.0)]),
        wcs=osiris_wcs(), axis_indices=OSIRIS_AXES)

    assert "image" not in text.splitlines(), f"arrows left behind in pixel coordinates:\n{text}"
    vector_line, = [line for line in text.splitlines() if "vector(" in line]
    ra = float(vector_line.split("(")[1].split(",")[0])
    assert 260.0 < ra < 270.0, vector_line       # an RA, not a pixel index
    assert not report.skipped, report.summary()


def test_an_unknown_output_frame_is_a_programming_error():
    with pytest.raises(ValueError, match="frame must be one of"):
        to_ds9(RegionList(regions=[]), frame="galactic")


# ================================================================ reading

def test_shapes_and_attributes_come_back():
    text = """# Region file format: DS9 version 4.1
image
circle(11,21,5) # color=red width=3 dash=1 text={src A} tag={grp1}
box(31,41,8,4,20)
# text(51,61) text={hello} textangle=30 color=cyan font="helvetica 18 bold roman"
"""
    regions, report = from_ds9(text, axis_indices=IMAGE_AXES)
    found = shapes_by_type(regions)

    circle = found["Circle"]
    assert (circle.x, circle.y, circle.radius) == (10.0, 20.0, 5.0)   # ds9 counts from 1
    assert circle.color == "red"
    assert circle.line_width == 3
    assert circle.dash is True
    assert circle.text == "src A"
    assert circle.tag == "grp1"

    box = found["Box"]
    assert (box.x, box.y, box.width, box.height) == (30.0, 40.0, 8.0, 4.0)
    assert box.angle == pytest.approx(20.0)

    label = found["Text"]
    assert (label.x, label.y) == (50.0, 60.0)
    assert label.text == "hello"
    assert label.angle == pytest.approx(30.0)
    assert label.color == "cyan"
    assert label.font_size == 18
    assert not report.skipped, report.summary()


@pytest.mark.parametrize("line", [
    "# vector(11,21,40,45) vector=1",
    "vector(11,21,40,45) vector=1",          # illegal in ds9, but people hand-edit
])
def test_arrows_are_parsed_in_both_forms(line):
    """`regions` drops vectors without a word, so this is entirely our own parsing."""
    regions, report = from_ds9(f"image\n{line}\n", axis_indices=IMAGE_AXES)

    arrow, = regions
    assert isinstance(arrow, Arrow)
    assert (arrow.x, arrow.y) == (10.0, 20.0)
    assert arrow.length == 40.0
    assert arrow.angle == pytest.approx(45.0)
    assert not report.skipped, report.summary()


def test_arrow_properties_are_parsed():
    regions, _ = from_ds9(
        "image\n# vector(11,21,40,45) vector=1 color=cyan width=4 dash=1 "
        "text={outflow} tag={jets}\n", axis_indices=IMAGE_AXES)

    arrow, = regions
    assert arrow.color == "cyan"
    assert arrow.line_width == 4
    assert arrow.dash is True
    assert arrow.text == "outflow"
    assert arrow.tag == "jets"


def test_a_line_becomes_an_arrow_and_is_reported():
    """ds9 arrowhead flags are dropped by the library, so a line is imported as an arrow."""
    regions, report = from_ds9("image\nline(11,21,11,61) # line=0 1\n", axis_indices=IMAGE_AXES)

    arrow, = regions
    assert isinstance(arrow, Arrow)
    assert (arrow.x, arrow.y) == (10.0, 20.0)
    assert arrow.length == pytest.approx(40.0)
    assert arrow.angle == pytest.approx(90.0)
    assert any("imported as an arrow" in note for note in report.notes), report.summary()


def test_an_unsupported_shape_is_reported_not_dropped():
    """The whole point of the Report: `regions` would take an ellipse, we do not draw one."""
    regions, report = from_ds9("image\ncircle(11,21,5)\nellipse(31,41,8,4,20)\n",
                               axis_indices=IMAGE_AXES)

    assert len(regions) == 1
    assert any("Ellipse" in item for item in report.skipped), report.summary()


def test_a_malformed_file_raises_a_region_format_error():
    """Callers catch the same exception type as for the native format."""
    with pytest.raises(RegionFormatError, match="ds9 region file"):
        from_ds9("image\ncircle(1,\n", axis_indices=IMAGE_AXES, where="broken.reg")


def test_an_empty_file_reads_as_no_regions():
    regions, report = from_ds9("# Region file format: DS9 version 4.1\nimage\n",
                               axis_indices=IMAGE_AXES)
    assert len(regions) == 0
    assert not report.skipped


def test_a_zero_length_arrow_is_reported():
    regions, report = from_ds9("image\n# vector(11,21,0,45) vector=1\n", axis_indices=IMAGE_AXES)
    assert len(regions) == 0
    assert any("zero length" in item for item in report.skipped), report.summary()


# ------------------------------------------------- frames the library refuses

PHYSICAL_FILE = """# Region file format: DS9 version 4.1
global color=green width=1
physical
circle(11,21,5) # color=red
box(31,41,8,4,20)
# vector(51,61,40,45) vector=1
"""


def test_a_physical_frame_reads_as_image_coordinates():
    """`physical` and `image` differ only through IRAF LTV/LTM keywords, which our files lack.

    `regions` 0.12 refuses `physical` and, worse, clears the current frame when it does — so every
    shape after that one line was dropped and a real GC region file loaded as nothing at all.
    """
    physical, report = from_ds9(PHYSICAL_FILE, axis_indices=IMAGE_AXES)
    image, _ = from_ds9(PHYSICAL_FILE.replace("physical", "image"), axis_indices=IMAGE_AXES)

    assert len(physical) == 3, report.summary()
    assert physical.regions == image.regions
    assert not report.skipped, report.summary()
    assert any("physical" in note for note in report.notes), report.summary()


def test_a_physical_frame_is_found_among_semicolon_separated_statements():
    """ds9 takes several statements on one line, and a label may hold a semicolon of its own."""
    regions, report = from_ds9(
        "physical; circle(11,21,5) # text={a;b}; box(31,41,8,4,20)\n", axis_indices=IMAGE_AXES)

    assert len(regions) == 2, report.summary()
    assert shapes_by_type(regions)["Circle"].text == "a;b"


def test_a_frame_the_library_refuses_is_reported_rather_than_printed(recwarn):
    """A warning on the terminal is invisible to a GUI user, and shown once per process at that.

    `detector` is genuinely unmappable — unlike `physical` — so the regions really are lost. What
    must not happen is losing them without a word.
    """
    regions, report = from_ds9("detector\ncircle(11,21,5)\n", axis_indices=IMAGE_AXES)

    assert len(regions) == 0
    assert any("detector" in item for item in report.skipped), report.summary()
    assert not recwarn.list, "the library's warnings belong in the report, not the terminal"


def test_repeated_complaints_are_reported_once():
    text = "image\n" + "".join(f"ruler({i},2,3,4)\n" for i in range(5))
    _, report = from_ds9(text, axis_indices=IMAGE_AXES)

    assert len([item for item in report.skipped if "ruler" in item]) == 1, report.summary()


def test_a_ds9_annotation_is_reported_not_dropped_in_silence():
    """A `#`-prefixed annotation is a plain comment to `regions`: no region, and no warning."""
    regions, report = from_ds9(
        "image\n# compass(1897,1787,66) compass=image {N} {W} 1 1\ncircle(11,21,5)\n",
        axis_indices=IMAGE_AXES)

    assert len(regions) == 1
    assert any("compass" in item for item in report.skipped), report.summary()


def test_a_long_report_is_counted_rather_than_listed():
    """One dropped region per line is one dialog line per line, up to a point."""
    text = "image\n" + "".join(f"ellipse({i},2,3,4,5)\n" for i in range(30))
    _, report = from_ds9(text, axis_indices=IMAGE_AXES)

    assert len(report.skipped) == 30, "the list itself stays complete"
    assert "...and 20 more" in report.summary()
    assert report.summary().count("•") == 11


# ------------------------------------------------------------- sky frames

def test_sky_regions_are_placed_through_the_wcs():
    wcs = osiris_wcs()
    mapping = CelestialMap(wcs, *OSIRIS_AXES)
    ra, dec = mapping.to_sky(31.0, 9.0)

    text = f"""# Region file format: DS9 version 4.1
icrs
circle({ra:.8f},{dec:.8f},0.00040000)
"""
    regions, report = from_ds9(text, wcs=wcs, axis_indices=OSIRIS_AXES)

    circle, = regions
    assert (circle.x, circle.y) == pytest.approx((31.0, 9.0), abs=SUB_PIXEL)
    assert not report.skipped, report.summary()


def test_sky_regions_without_a_wcs_are_reported():
    regions, report = from_ds9("icrs\ncircle(266.4,34.0,0.0004)\n", wcs=None,
                               axis_indices=IMAGE_AXES)

    assert len(regions) == 0
    assert any("no WCS" in item for item in report.skipped), report.summary()


def test_a_sky_region_off_the_image_is_reported_rather_than_placed_at_nan():
    """A position outside the projection comes back as NaN instead of raising."""
    regions, report = from_ds9("icrs\ncircle(266.4,-89.9,0.0004)\n", wcs=osiris_wcs(),
                               axis_indices=OSIRIS_AXES)

    assert len(regions) == 0
    assert report.skipped, "a NaN position must not become a region"


def test_a_sky_arrow_is_placed_and_measured_through_the_wcs():
    wcs = osiris_wcs()
    mapping = CelestialMap(wcs, *OSIRIS_AXES)
    ra, dec = mapping.to_sky(20.0, 10.0)
    length_deg = mapping.pixels_to_arcsec(6.0) / 3600.0

    regions, report = from_ds9(
        f"icrs\n# vector({ra:.8f},{dec:.8f},{length_deg:.8f},45) vector=1\n",
        wcs=wcs, axis_indices=OSIRIS_AXES)

    arrow, = regions
    assert (arrow.x, arrow.y) == pytest.approx((20.0, 10.0), abs=SUB_PIXEL)
    assert arrow.length == pytest.approx(6.0, rel=1e-3)
    assert not report.skipped, report.summary()


#: Every sky frame ds9 will declare, and how to say the same position in it. The tuple is
#: `(ds9 name, attribute chain onto a SkyCoord, longitude attribute, latitude attribute)`.
SKY_FRAMES = [
    ("icrs", "icrs", "ra", "dec"),
    ("fk5", "fk5", "ra", "dec"),
    ("j2000", "fk5", "ra", "dec"),
    ("fk4", "fk4", "ra", "dec"),
    ("b1950", "fk4", "ra", "dec"),
    ("galactic", "galactic", "l", "b"),
    ("ecliptic", "barycentricmeanecliptic", "lon", "lat"),
]


def in_frame(ra_deg, dec_deg, frame_attribute, lon_attribute, lat_attribute):
    """One ICRS position written in another frame, as the pair of degrees ds9 would hold."""
    coord = getattr(SkyCoord(ra_deg * u.deg, dec_deg * u.deg, frame="icrs"), frame_attribute)
    return (getattr(coord, lon_attribute).deg, getattr(coord, lat_attribute).deg)


def test_our_frame_table_matches_the_librarys():
    """A hand-parsed arrow and a library-parsed circle must mean the same by `galactic`.

    The two paths are independent — ours through `SkyCoord`, the library's through its own
    `ds9_frame_map` — so the tables have to agree or an arrow drifts away from the shapes around
    it. Asserted against the library's own table rather than a copy of it.
    """
    from regions.io.ds9.core import ds9_frame_map

    from pyql3.core.ds9_regions import _SKY_FRAMES

    theirs = {name: frame for name, frame in ds9_frame_map.items() if name != "image"}
    assert _SKY_FRAMES == theirs


def test_a_sky_anchor_can_name_every_frame_a_wcs_can_have():
    """`regions_model` lists the anchor frames itself, since it imports no astropy."""
    from pyql3.core.regions_model import ANCHOR_FRAMES
    from pyql3.core.sky import DS9_FRAME_NAMES

    assert sorted(ANCHOR_FRAMES) == sorted(DS9_FRAME_NAMES.values())


@pytest.mark.parametrize("ds9_name,frame_attribute,lon,lat", SKY_FRAMES)
def test_every_sky_frame_places_shapes_and_arrows_on_the_same_pixel(ds9_name, frame_attribute,
                                                                    lon, lat):
    """ds9 writes any of seven frame names, and a WCS is all that is needed to place them.

    Arrows are ours to parse and used to be read as ICRS degrees whatever the file said, which
    left a `fk4` arrow ~7000 px from the circle beside it and a `galactic` one off the image.
    """
    wcs = image_wcs()
    mapping = CelestialMap(wcs, *IMAGE_AXES)
    ra, dec = mapping.to_sky(31.0, 9.0)
    x, y = in_frame(ra, dec, frame_attribute, lon, lat)
    length_deg = mapping.pixels_to_arcsec(6.0) / 3600.0

    text = (f"{ds9_name}\n"
            f"circle({x:.9f},{y:.9f},0.0004)\n"
            f"# vector({x:.9f},{y:.9f},{length_deg:.9f},45) vector=1\n")
    regions, report = from_ds9(text, wcs=wcs, axis_indices=IMAGE_AXES)

    found = shapes_by_type(regions)
    assert len(regions) == 2, report.summary()
    assert (found["Circle"].x, found["Circle"].y) == pytest.approx((31.0, 9.0), abs=SUB_PIXEL)
    assert (found["Arrow"].x, found["Arrow"].y) == pytest.approx((31.0, 9.0), abs=SUB_PIXEL)
    assert found["Arrow"].length == pytest.approx(6.0, rel=1e-3)
    assert not report.skipped, report.summary()


def test_a_sexagesimal_position_reads_as_hours_of_right_ascension():
    """ds9's default equatorial output. `17:45:40` is 266.4°, not 17.8° — a 15× error."""
    wcs = image_wcs()
    mapping = CelestialMap(wcs, *IMAGE_AXES)
    ra, dec = mapping.to_sky(31.0, 9.0)
    # Written in fk5 because the file declares fk5: icrs and fk5 differ by ~0.02", which is
    # small but not nothing at 0.36"/pixel.
    sexagesimal = SkyCoord(ra * u.deg, dec * u.deg).fk5.to_string("hmsdms", sep=":").split()

    text = (f"fk5\ncircle({sexagesimal[0]},{sexagesimal[1]},1\")\n"
            f"# vector({sexagesimal[0]},{sexagesimal[1]},2\",45) vector=1\n")
    regions, report = from_ds9(text, wcs=wcs, axis_indices=IMAGE_AXES)

    found = shapes_by_type(regions)
    assert len(regions) == 2, report.summary()
    assert (found["Arrow"].x, found["Arrow"].y) == pytest.approx((31.0, 9.0), abs=SUB_PIXEL)


def test_a_sexagesimal_galactic_position_is_degrees_not_hours():
    """Only the equatorial frames put their longitude in hours."""
    wcs = image_wcs()
    mapping = CelestialMap(wcs, *IMAGE_AXES)
    ra, dec = mapping.to_sky(31.0, 9.0)
    galactic = SkyCoord(ra * u.deg, dec * u.deg).galactic.to_string("dms", sep=":").split()

    regions, report = from_ds9(
        f"galactic\n# vector({galactic[0]},{galactic[1]},0.001,45) vector=1\n",
        wcs=wcs, axis_indices=IMAGE_AXES)

    arrow, = regions
    assert (arrow.x, arrow.y) == pytest.approx((31.0, 9.0), abs=SUB_PIXEL)


@pytest.mark.parametrize("written,pixels", [
    ('15"', 15.0 / 0.36),        # arcseconds — what ds9 writes for a sky vector
    ("15'", 15.0 * 60 / 0.36),   # arcminutes
    ("15d", 15.0 * 3600 / 0.36), # degrees, said explicitly
    ("15", 15.0 * 3600 / 0.36),  # bare, which means degrees in a sky frame
    ("15p", 15.0),               # physical pixels
    ("15i", 15.0),               # image pixels
])
def test_an_arrow_length_carries_its_unit(written, pixels):
    """`15"` read as 15 degrees is an arrow 3600 times too long.

    The tail is the WCS's reference point, where the pixels are 0.36" in every direction. A
    length is measured along the arrow where it starts (`BUGS.md` R37), so a tail far from the
    tangent point, where the projection stretches the pixels, would test the projection instead.
    """
    wcs = image_wcs()        # 0.36 arcsec/pixel at the reference point
    regions, report = from_ds9(
        f"icrs\n# vector(266.40000000,34.00000000,{written},45) vector=1\n",
        wcs=wcs, axis_indices=IMAGE_AXES)

    arrow, = regions
    assert arrow.length == pytest.approx(pixels, rel=1e-6), report.summary()


def test_a_bare_length_in_an_image_frame_is_pixels():
    regions, _ = from_ds9("image\n# vector(50,50,15,45) vector=1\n", axis_indices=IMAGE_AXES)
    arrow, = regions
    assert arrow.length == pytest.approx(15.0)


def test_an_arrow_in_a_frame_we_cannot_place_is_reported():
    """Guessing that `wcsa` numbers are pixels would put the arrow somewhere arbitrary."""
    regions, report = from_ds9("wcsa\n# vector(50,50,15,45) vector=1\n", wcs=image_wcs(),
                               axis_indices=IMAGE_AXES)

    assert len(regions) == 0
    assert any("wcsa" in item for item in report.skipped), report.summary()


def _statement_line_file(form, ra, dec):
    """One circle and one vector at (ra, dec), with the `fk5` frame given in `form`."""
    circle = f'circle({ra:.9f},{dec:.9f},1")'
    vector = f'# vector({ra:.9f},{dec:.9f},2",45) vector=1'
    return {
        "fk5; # vector": f"fk5; {circle}\nfk5; {vector}\n",
        "after fk5;circle": f"fk5;{circle}\n{vector}\n",
        "fk5 # comment": f"fk5 # from ds9\n{circle}\n{vector}\n",
    }[form]


@pytest.mark.parametrize("form", ["fk5; # vector", "after fk5;circle", "fk5 # comment"])
def test_a_vector_follows_a_frame_given_on_a_statement_line(form):
    """ds9 and `regions` apply a frame given inside `fk5;circle(...)` or before a comment (R47)."""
    wcs = image_wcs()
    ra, dec = CelestialMap(wcs, *IMAGE_AXES).to_sky(40.0, 30.0)

    regions, report = from_ds9(DS9_HEADER_LINE + _statement_line_file(form, ra, dec), wcs=wcs,
                               axis_indices=IMAGE_AXES)

    found = shapes_by_type(regions)
    assert set(found) == {"Circle", "Arrow"}, report.summary()
    assert (found["Arrow"].x, found["Arrow"].y) == pytest.approx(
        (found["Circle"].x, found["Circle"].y), abs=SUB_PIXEL)
    assert not report.skipped, report.summary()


def test_a_label_behind_a_frame_word_is_read():
    """`fk5 # text(...)` is a frame and then a label, not a frame and a comment."""
    wcs = image_wcs()
    ra, dec = CelestialMap(wcs, *IMAGE_AXES).to_sky(40.0, 30.0)

    regions, report = from_ds9(f"fk5 # text({ra:.9f},{dec:.9f}) text={{knot}}\n", wcs=wcs,
                               axis_indices=IMAGE_AXES)

    label, = regions
    assert label.text == "knot"
    assert (label.x, label.y) == pytest.approx((40.0, 30.0), abs=0.1)
    assert not report.skipped, report.summary()


@pytest.mark.parametrize("frame", ["detector", "amplifier", "linear"])
def test_a_detector_frame_arrow_is_refused(frame):
    """These numbers are not pixels of this image, so reading them as such placed it at random."""
    regions, report = from_ds9(f"{frame}\n# vector(41,31,10,45) vector=1\n", wcs=image_wcs(),
                               axis_indices=IMAGE_AXES)

    assert len(regions) == 0
    assert any(frame in item and "arrow" in item for item in report.skipped), report.summary()


def test_image_frame_arrows_on_a_mismatched_cube_are_flagged():
    """An image-frame vector means FITS axes 1 and 2 as much as a circle does (R47)."""
    regions, report = from_ds9("image\n# vector(11,21,5,0) vector=1\n", wcs=osiris_wcs(),
                               axis_indices=OSIRIS_AXES)

    assert len(regions) == 1
    assert any("may not line up" in note for note in report.notes), report.summary()


def test_a_vector_takes_the_global_line_like_a_shape():
    """ds9 writes per-region properties only where they differ from `global` (R64)."""
    text = (f"{DS9_HEADER_LINE}global color=red width=3 dash=1\nimage\ncircle(11,11,3)\n"
            "# vector(21,21,10,45) vector=1\n# vector(31,31,10,45) vector=1 color=cyan width=1\n")

    found = from_ds9(text, axis_indices=IMAGE_AXES)[0].regions
    circle, arrow, own = found
    assert (arrow.color, arrow.line_width, arrow.dash) == (circle.color, circle.line_width,
                                                           circle.dash) == ("red", 3, True)
    assert (own.color, own.line_width) == ("cyan", 1), "a vector's own properties still win"


def test_an_exported_arrow_states_its_width():
    """Under a library `global width=3`, an arrow of width 2 with no `width=` is drawn at 3 (R64)."""
    text, _ = to_ds9(RegionList(regions=[Circle(x=1.0, y=1.0, radius=2.0, line_width=3),
                                         Arrow(x=5.0, y=5.0, length=4.0, angle=0.0)]),
                     axis_indices=IMAGE_AXES)

    line, = [ln for ln in text.splitlines() if "vector(" in ln]
    assert "width=2" in line, text
    arrow = shapes_by_type(from_ds9(text, axis_indices=IMAGE_AXES)[0])["Arrow"]
    assert arrow.line_width == 2


def test_an_exported_arrow_does_not_take_a_shapes_label():
    """With one labelled shape the library writes `global ... text={c}`, which ds9 applies to the
    vector lines after it, so an unlabelled arrow states `text={}` (review I2)."""
    text, _ = to_ds9(RegionList(regions=[Circle(x=1.0, y=1.0, radius=2.0, text="c"),
                                         Arrow(x=5.0, y=5.0, length=4.0, angle=0.0),
                                         Arrow(x=9.0, y=9.0, length=4.0, angle=0.0,
                                               text="labelled", font_size=20)]),
                     axis_indices=IMAGE_AXES)

    plain, labelled = [ln for ln in text.splitlines() if "vector(" in ln]
    assert "text={}" in plain, text
    assert 'font="helvetica 20 normal roman"' in labelled, text
    back = [r for r in from_ds9(text, axis_indices=IMAGE_AXES)[0] if isinstance(r, Arrow)]
    assert [(arrow.text, arrow.font_size) for arrow in back] == [("", 12), ("labelled", 20)]


def test_global_lines_add_to_each_other_for_arrows_as_for_shapes():
    """`regions` merges successive `global` lines; the arrow defaults must too (review I2)."""
    text = ("global color=red width=3\nglobal dash=1\nimage\ncircle(11,11,3)\n"
            "# vector(21,21,10,45) vector=1\n")

    circle, arrow = from_ds9(text, axis_indices=IMAGE_AXES)[0].regions
    assert (arrow.color, arrow.line_width, arrow.dash) == (circle.color, circle.line_width,
                                                           circle.dash) == ("red", 3, True)


def test_a_frame_ds9_cannot_name_is_named_in_the_report():
    """An FK4-no-E-terms WCS has no ds9 frame word; the arrow's tail does have a sky position
    (review M5)."""
    wcs = image_wcs()
    wcs.wcs.radesys, wcs.wcs.equinox = "FK4-NO-E", 1950.0
    _, report = to_ds9(RegionList(regions=[Arrow(x=5.0, y=5.0, length=4.0, angle=0.0)]),
                       wcs=wcs, axis_indices=IMAGE_AXES, frame="sky")

    assert any("fk4noeterms" in item.lower() for item in report.skipped), report.summary()
    assert not any("no sky position" in item for item in report.skipped), report.summary()


def test_a_newline_in_a_label_does_not_split_the_line():
    """A label with a newline split its line in two, and ds9 rejects such a file whole (R79)."""
    regions = [Circle(x=10.0, y=10.0, radius=3.0, text="two\nlines"),
               Text(x=20.0, y=20.0, text="tilted\r\nlabel", angle=30.0),
               Arrow(x=5.0, y=5.0, length=6.0, angle=10.0, text="an\rarrow")]

    text, out_report = to_ds9(RegionList(regions=regions), axis_indices=IMAGE_AXES)
    back, in_report = from_ds9(text, axis_indices=IMAGE_AXES)

    labels = sorted(region.text for region in back)
    assert labels == ["an arrow", "tilted label", "two lines"], text
    assert not in_report.skipped, in_report.summary()
    assert any("newline" in note for note in out_report.notes), out_report.summary()


def test_attributes_ds9_cannot_carry_are_reported():
    """The Report exists to announce what the export loses; it said nothing about these (R48)."""
    regions = [Circle(x=10.0, y=10.0, radius=3.0, z_range=(120, 180)),
               Box(x=20.0, y=20.0, width=4.0, height=2.0, visible=False),
               Circle(x=30.0, y=30.0, radius=3.0, text="Brg", font_size=20),
               Text(x=40.0, y=40.0, text="rotated", angle=30.0, tag="labels"),
               Text(x=50.0, y=50.0, text="big", font_size=20)]

    text, report = to_ds9(RegionList(regions=regions), axis_indices=IMAGE_AXES)

    skipped = " | ".join(report.skipped)
    assert "channel range" in skipped and "region 0" in skipped, report.summary()
    assert "hidden" in skipped and "region 1" in skipped, report.summary()
    assert "font size" in skipped and "region 2" in skipped, report.summary()
    assert "region 3" not in skipped, "a rotated label's tag is written, not lost"
    assert "region 4" not in skipped, "a text region's font size is written, not lost"

    back = from_ds9(text, axis_indices=IMAGE_AXES)[0]
    rotated, = [r for r in back if isinstance(r, Text) and r.text == "rotated"]
    big, = [r for r in back if isinstance(r, Text) and r.text == "big"]
    assert rotated.tag == "labels"
    assert big.font_size == 20, text


def test_an_excluded_region_is_reported():
    """ds9's `-circle(...)` excludes the area; it was imported as an ordinary circle in silence."""
    regions, report = from_ds9("image\n-circle(41,31,5)\ncircle(11,11,3)\n",
                               axis_indices=IMAGE_AXES)

    assert len(regions) == 2, "imported as an ordinary region, which the user may still want"
    assert any("region 0" in item and "exclu" in item for item in report.skipped), \
        report.summary()
    assert not any("region 1" in item for item in report.skipped), report.summary()


def test_an_unreadable_vector_is_reported_rather_than_vanishing():
    """A `# vector(...)` line is a comment to `regions`: if our regex misses it, nothing says so."""
    regions, report = from_ds9("image\n# vector(50,50,nonsense,45) vector=1\n",
                               axis_indices=IMAGE_AXES)

    assert len(regions) == 0
    assert any("vector" in item or "arrow" in item for item in report.skipped), report.summary()


def rotated_wcs(rotation_deg=30.0):
    """A field-rotated WCS, which is the norm for Keck data and where sky angles differ."""
    rotation = np.radians(rotation_deg)
    wcs = image_wcs()
    wcs.wcs.pc = np.array([[np.cos(rotation), -np.sin(rotation)],
                           [np.sin(rotation), np.cos(rotation)]])
    return wcs


def test_a_sky_angle_is_not_the_image_angle_on_a_rotated_field():
    """The reason arrows cannot simply carry their angle across: 45° is 75° after a 30° rotation.

    Confirmed in ds9 on a 30°-rotated field (ladder file 25): a sky-frame vector lies along a
    sky-frame box given the same angle, so ds9 measures both from the sky axes. On an *unrotated*
    field the two conventions coincide, which is why only a rotated field could settle it.
    """
    unrotated = to_ds9(RegionList(regions=[Arrow(x=50.0, y=50.0, length=10.0, angle=45.0)]),
                       wcs=rotated_wcs(0.0), axis_indices=IMAGE_AXES, frame="sky")[0]
    rotated = to_ds9(RegionList(regions=[Arrow(x=50.0, y=50.0, length=10.0, angle=45.0)]),
                     wcs=rotated_wcs(30.0), axis_indices=IMAGE_AXES, frame="sky")[0]

    def written_angle(text):
        line, = [ln for ln in text.splitlines() if "vector(" in ln]
        return float(line.split(",")[3].split(")")[0])

    assert written_angle(unrotated) == pytest.approx(45.0, abs=1e-3)
    assert written_angle(rotated) == pytest.approx(75.0, abs=1e-3)


@pytest.mark.parametrize("rotation", [0.0, 30.0, 90.0, -45.0])
def test_an_arrow_survives_a_sky_round_trip_on_a_rotated_field(rotation):
    wcs = rotated_wcs(rotation)
    original = Arrow(x=50.0, y=50.0, length=10.0, angle=135.0, text="outflow")

    text, out_report = to_ds9(RegionList(regions=[original]), wcs=wcs,
                              axis_indices=IMAGE_AXES, frame="sky")
    restored, in_report = from_ds9(text, wcs=wcs, axis_indices=IMAGE_AXES)

    arrow, = restored
    assert (arrow.x, arrow.y) == pytest.approx((50.0, 50.0), abs=SUB_PIXEL)
    assert arrow.length == pytest.approx(10.0, rel=1e-3)
    assert arrow.angle == pytest.approx(135.0, abs=1e-2)
    assert arrow.text == "outflow"
    assert not out_report.skipped and not in_report.skipped


@pytest.mark.parametrize("angle, written", [(30.0, 60.0), (0.0, 30.0)])
def test_a_label_angle_is_a_sky_angle_on_a_rotated_field(angle, written):
    """Seen in ds9 on a 30°-rotated field: a sky-frame `textangle=30` is drawn level and
    `textangle=20` at -10°, so ds9 measures a label's angle from the sky axes, as it does a
    box's or a vector's. A label level on the image therefore needs `textangle=30` there."""
    text, _ = to_ds9(RegionList(regions=[Text(x=50.0, y=50.0, text="label", angle=angle)]),
                     wcs=rotated_wcs(30.0), axis_indices=IMAGE_AXES, frame="sky")

    line, = [ln for ln in text.splitlines() if "text={label}" in ln]
    assert float(line.split("textangle=")[1].split()[0]) == pytest.approx(written, abs=1e-3)


@pytest.mark.parametrize("rotation", [0.0, 30.0, 90.0, -45.0])
@pytest.mark.parametrize("angle", [0.0, 30.0])
def test_a_label_survives_a_sky_round_trip_on_a_rotated_field(rotation, angle):
    wcs = rotated_wcs(rotation)
    original = Text(x=50.0, y=50.0, text="label", angle=angle)

    text, out_report = to_ds9(RegionList(regions=[original]), wcs=wcs,
                              axis_indices=IMAGE_AXES, frame="sky")
    restored, in_report = from_ds9(text, wcs=wcs, axis_indices=IMAGE_AXES)

    label, = restored
    assert (label.x, label.y) == pytest.approx((50.0, 50.0), abs=SUB_PIXEL)
    difference = (label.angle - angle + 180.0) % 360.0 - 180.0
    assert difference == pytest.approx(0.0, abs=1e-2)
    assert not out_report.skipped and not in_report.skipped


@pytest.mark.parametrize("cdelt, warned", [([-1e-4, 2e-4], True), ([-1e-4, 1e-4], False)],
                         ids=["non-square", "square"])
def test_a_sky_arrow_on_non_square_pixels_is_reported(cdelt, warned):
    """Seen in ds9 on 0.36" x 0.72" pixels: ds9 turns a vector's sky angle into a pixel angle
    by the field rotation alone and its length by one pixel scale, so an arrow written as the
    true direction and length on the sky is drawn off target there (`BUGS.md` R94)."""
    wcs = image_wcs()
    wcs.wcs.cdelt = cdelt
    text, report = to_ds9(RegionList(regions=[Arrow(x=50.0, y=50.0, length=20.0, angle=45.0)]),
                          wcs=wcs, axis_indices=IMAGE_AXES, frame="sky")

    assert "# vector(" in text
    assert any("pixels are not square" in note for note in report.notes) == warned, report.notes


#: Sgr A*, in ICRS.
SGR_A_STAR = SkyCoord(266.41683708 * u.deg, -29.00781056 * u.deg)


def framed_wcs(frame):
    """A 36 mas/px WCS in `frame`, pointed at Sgr A* with it on 0-based pixel (49, 49)."""
    wcs = WCS(naxis=2)
    if frame == "galactic":
        wcs.wcs.ctype = ['GLON-TAN', 'GLAT-TAN']
        here = SGR_A_STAR.galactic
        wcs.wcs.crval = [here.l.deg, here.b.deg]
    else:
        wcs.wcs.ctype = ['RA---TAN', 'DEC--TAN']
        here = getattr(SGR_A_STAR, frame)
        wcs.wcs.crval = [here.ra.deg, here.dec.deg]
        if frame != "icrs":
            wcs.wcs.radesys = frame.upper()
            wcs.wcs.equinox = 2000.0 if frame == "fk5" else 1950.0
    wcs.wcs.cdelt, wcs.wcs.crpix = [-1e-5, 1e-5], [50, 50]
    return wcs


@pytest.mark.parametrize("frame", ["icrs", "fk5", "fk4", "galactic"])
def test_an_arrow_only_sky_export_declares_the_wcs_frame(frame):
    """With no shape for the library to write, the frame line was a literal `icrs` (R17)."""
    from pyql3.core.ds9_regions import _SKY_FRAMES

    wcs = framed_wcs(frame)
    regions = [Arrow(x=49.0, y=49.0, length=10.0, angle=30.0),
               Text(x=49.0, y=49.0, text="tilted", angle=20.0)]

    text, out_report = to_ds9(RegionList(regions=regions), wcs=wcs, axis_indices=IMAGE_AXES,
                              frame="sky")
    library, _ = to_ds9(RegionList(regions=[Circle(x=49.0, y=49.0, radius=3.0)]), wcs=wcs,
                        axis_indices=IMAGE_AXES, frame="sky")

    def frame_line(written):
        line, = [ln for ln in written.splitlines() if ln in _SKY_FRAMES or ln == "image"]
        return line

    ours, theirs = frame_line(text), frame_line(library)
    assert _SKY_FRAMES[ours] == _SKY_FRAMES[theirs], (ours, theirs)

    back, in_report = from_ds9(text, wcs=wcs, axis_indices=IMAGE_AXES)
    assert len(back) == 2, in_report.summary()
    for region in back:
        assert (region.x, region.y) == pytest.approx((49.0, 49.0), abs=SUB_PIXEL), text
    assert not out_report.skipped and not in_report.skipped


def non_square_wcs():
    """0.36" x 0.72" pixels (aspect 1:2), unrotated."""
    wcs = image_wcs()
    wcs.wcs.cdelt = [-1e-4, 2e-4]
    return wcs


def test_an_arrow_points_the_same_way_on_the_sky_with_non_square_pixels():
    """A 2 x 1 rectangle probe's principal axis is not the arrow once pixels are 1:2 (R37)."""
    from pyql3.core.regions_io import placed_on_image, with_sky_anchors

    wcs = non_square_wcs()
    mapping = CelestialMap(wcs, *IMAGE_AXES)
    arrow = Arrow(x=30.0, y=20.0, length=10.0, angle=45.0)

    text, _ = to_ds9(RegionList(regions=[arrow]), wcs=wcs, axis_indices=IMAGE_AXES, frame="sky")
    line, = [ln for ln in text.splitlines() if "vector(" in ln]
    length_deg, written = (float(field) for field in line.split("(")[1].split(")")[0].split(",")[2:])

    tail, head = (SkyCoord(*mapping.to_sky(*point), unit="deg") for point in
                  ((arrow.x, arrow.y), arrow.end))
    assert written == pytest.approx((tail.position_angle(head).deg + 90.0) % 360.0, abs=0.01)
    assert length_deg * 3600.0 == pytest.approx(tail.separation(head).arcsec, rel=1e-4)

    back, report = from_ds9(text, wcs=wcs, axis_indices=IMAGE_AXES)
    restored, = back
    assert restored.angle == pytest.approx(45.0, abs=0.01)
    assert restored.length == pytest.approx(10.0, abs=1e-3)
    assert not report.skipped, report.summary()

    def refuse(offer):
        raise AssertionError(f"asked on its own image: {offer.summary()}")

    for region in (arrow, Box(x=30.0, y=20.0, width=6.0, height=3.0, angle=45.0)):
        placed, _ = placed_on_image(with_sky_anchors([region], wcs=wcs), wcs=wcs,
                                    choose_frame=refuse)
        assert placed[0].angle == pytest.approx(45.0, abs=0.01), type(region).__name__


def test_a_swapped_display_writes_the_same_sky_regions():
    """X = Dec, Y = RA is a reflection: a direction t there is 90 - t in the default (R38)."""
    wcs = osiris_wcs()
    default = [Box(x=30.0, y=12.0, width=10.0, height=3.0, angle=25.0),
               Arrow(x=30.0, y=12.0, length=8.0, angle=25.0)]
    swapped = [Box(x=12.0, y=30.0, width=10.0, height=3.0, angle=65.0),
               Arrow(x=12.0, y=30.0, length=8.0, angle=65.0)]

    a, _ = to_ds9(RegionList(regions=default), wcs=wcs, axis_indices=OSIRIS_AXES, frame="sky")
    b, _ = to_ds9(RegionList(regions=swapped), wcs=wcs, axis_indices=(1, 2), frame="sky")

    def shape_lines(text):
        return [ln for ln in text.splitlines() if "box(" in ln or "vector(" in ln]

    def numbers(line):
        return [float(field) for field in line.split("(")[1].split(")")[0].split(",")]

    for one, other in zip(shape_lines(a), shape_lines(b), strict=True):
        assert numbers(one) == pytest.approx(numbers(other), abs=1e-6), (one, other)

    back, report = from_ds9(a, wcs=wcs, axis_indices=(1, 2))
    found = shapes_by_type(back)
    assert found["Box"].angle == pytest.approx(65.0, abs=1e-3)
    assert found["Arrow"].angle == pytest.approx(65.0, abs=1e-3)
    assert (found["Arrow"].x, found["Arrow"].y) == pytest.approx((12.0, 30.0), abs=SUB_PIXEL)
    assert not report.skipped, report.summary()


def test_image_frame_regions_on_a_mismatched_cube_are_flagged():
    """ds9's image frame means FITS axes 1 and 2; this cube shows 3 and 2."""
    regions, report = from_ds9("image\ncircle(11,21,5)\n", wcs=osiris_wcs(),
                               axis_indices=OSIRIS_AXES)

    assert len(regions) == 1, "still imported — the user may want them anyway"
    assert any("may not line up" in note for note in report.notes), report.summary()


# ================================================================ round trips

@pytest.mark.parametrize("axes,wcs_factory,frame", [
    (IMAGE_AXES, image_wcs, "image"),
    (IMAGE_AXES, image_wcs, "sky"),
    (OSIRIS_AXES, osiris_wcs, "sky"),
])
def test_regions_survive_a_round_trip_through_ds9_text(axes, wcs_factory, frame):
    wcs = wcs_factory()
    original = sample_regions()

    text, out_report = to_ds9(RegionList(regions=original), wcs=wcs, axis_indices=axes,
                              frame=frame)
    restored, in_report = from_ds9(text, wcs=wcs, axis_indices=axes)

    assert not out_report.skipped, out_report.summary()
    assert not in_report.skipped, in_report.summary()

    before, after = shapes_by_type(original), shapes_by_type(restored)
    assert set(before) == set(after), f"shapes changed type:\n{text}"

    for name in before:
        assert (after[name].x, after[name].y) == pytest.approx((before[name].x, before[name].y),
                                                               abs=SUB_PIXEL), name
        assert after[name].text == before[name].text, name

    assert after["Circle"].radius == pytest.approx(before["Circle"].radius, rel=1e-4)
    assert after["Box"].width == pytest.approx(before["Box"].width, rel=1e-4)
    assert after["Box"].angle == pytest.approx(before["Box"].angle, abs=1e-3)
    assert after["Arrow"].length == pytest.approx(before["Arrow"].length, rel=1e-4)
    assert after["Arrow"].angle == pytest.approx(before["Arrow"].angle, abs=1e-3)


def test_a_rotated_label_survives_a_round_trip():
    original = [Text(x=5.0, y=6.0, text="tilted", angle=30.0)]
    text, _ = to_ds9(RegionList(regions=original), axis_indices=IMAGE_AXES)
    restored, report = from_ds9(text, axis_indices=IMAGE_AXES)

    label, = restored
    assert (label.x, label.y) == pytest.approx((5.0, 6.0))
    assert label.angle == pytest.approx(30.0)
    assert not report.skipped, report.summary()


def test_style_survives_a_round_trip():
    original = [Circle(x=1.0, y=2.0, radius=3.0, color="red", line_width=3, dash=True,
                       text="src", tag="grp")]
    text, _ = to_ds9(RegionList(regions=original), axis_indices=IMAGE_AXES)
    restored, _ = from_ds9(text, axis_indices=IMAGE_AXES)

    assert restored.regions == original


def test_the_verified_ds9_target_file_reads_back():
    """The file whose exact syntax was confirmed to load in ds9 (`TODO_regions.md`).

    Written out here rather than read from `agent_tests/`, which is gitignored scratch.
    """
    text = """# Region file format: DS9 astropy/regions
# written by QuickLook 3 v0.1.0
image
circle(100.00000000,100.00000000,18.00000000)
box(100.00000000,100.00000000,46.00000000,24.00000000,20.00000000)
text(100.00000000,140.00000000) # text={a label with (parens)}
# vector(60,60,45,0) vector=1 text={east}
# vector(60,60,45,90) vector=1 text={north}
"""
    regions, report = from_ds9(text, axis_indices=IMAGE_AXES)

    kinds = sorted(type(region).__name__ for region in regions)
    assert kinds == ["Arrow", "Arrow", "Box", "Circle", "Text"], kinds
    assert not report.skipped, report.summary()

    label = shapes_by_type(regions)["Text"]
    assert label.text == "a label with (parens)", "parentheses in a label must survive"


# ================================================================ CelestialMap

def test_celestial_map_uses_the_longitude_latitude_subset():
    """`wcs.celestial` would put Dec on axis 0 here and return NaN for (ra, dec)."""
    wcs = osiris_wcs()
    assert list(wcs.celestial.wcs.ctype) == ['DEC--TAN', 'RA---TAN'], "the trap still exists"
    assert np.all(np.isnan(wcs.celestial.world_to_pixel_values(266.4, 34.0))), "still NaN"

    mapping = CelestialMap(wcs, *OSIRIS_AXES)
    assert mapping.usable
    assert list(mapping.wcs2d.wcs.ctype) == ['RA---TAN', 'DEC--TAN']
    assert mapping.from_sky(266.4, 34.0) == pytest.approx((31.0, 9.0), abs=1e-6)


def test_celestial_map_round_trips_a_position():
    mapping = CelestialMap(osiris_wcs(), *OSIRIS_AXES)
    sky = mapping.to_sky(12.5, 7.5)
    assert mapping.from_sky(*sky) == pytest.approx((12.5, 7.5), abs=1e-6)


def test_celestial_map_converts_a_coordinate_from_another_frame():
    """`from_sky` takes the WCS's own world numbers; `from_skycoord` takes any frame."""
    mapping = CelestialMap(osiris_wcs(), *OSIRIS_AXES)
    ra, dec = mapping.to_sky(12.5, 7.5)
    galactic = SkyCoord(ra * u.deg, dec * u.deg, frame="icrs").galactic

    assert mapping.from_skycoord(galactic) == pytest.approx((12.5, 7.5), abs=1e-6)
    assert mapping.from_sky(galactic.l.deg, galactic.b.deg) != pytest.approx((12.5, 7.5),
                                                                            abs=1.0)


def test_celestial_map_without_a_wcs_places_no_coordinate():
    mapping = CelestialMap(None)
    assert mapping.from_skycoord(SkyCoord(266.4 * u.deg, 34.0 * u.deg)) is None
    assert mapping.from_skycoord(None) is None


def test_celestial_map_handles_swapped_display_axes():
    """Displaying Dec as X and RA as Y is legal; the sub-WCS is still longitude-first."""
    mapping = CelestialMap(osiris_wcs(), 1, 2)
    assert mapping.usable and mapping.swapped
    assert mapping.to_wcs_pixels(9.0, 31.0) == (31.0, 9.0)
    assert mapping.from_wcs_pixels(31.0, 9.0) == (9.0, 31.0)
    assert mapping.from_sky(266.4, 34.0) == pytest.approx((9.0, 31.0), abs=1e-6)


def test_celestial_map_refuses_a_non_celestial_plane():
    """Wavelength against Dec is a reasonable thing to display, and has no sky position."""
    mapping = CelestialMap(osiris_wcs(), 0, 1)

    assert not mapping.usable
    assert "no sky coordinates" in mapping.reason
    assert mapping.to_sky(1.0, 2.0) is None
    assert mapping.from_sky(266.4, 34.0) is None


def test_celestial_map_without_a_wcs_explains_itself():
    mapping = CelestialMap(None)
    assert not mapping.usable
    assert mapping.reason == "the file has no WCS"
    assert mapping.pixel_scale_arcsec() is None


def test_celestial_map_reports_a_wcs_with_no_celestial_axes():
    wcs = WCS(naxis=2)
    wcs.wcs.ctype = ['WAVE', 'AWAV']
    mapping = CelestialMap(wcs, 0, 1)

    assert not mapping.usable
    assert "no celestial" in mapping.reason


def test_pixel_scale_is_reported_in_arcseconds():
    mapping = CelestialMap(osiris_wcs(), *OSIRIS_AXES)
    assert mapping.pixel_scale_arcsec() == pytest.approx(0.36, rel=1e-6)
    assert mapping.pixels_to_arcsec(10) == pytest.approx(3.6, rel=1e-6)
    assert mapping.arcsec_to_pixels(3.6) == pytest.approx(10.0, rel=1e-6)

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
import numpy as np
import pytest
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

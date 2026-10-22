"""The display <-> orig coordinate mapping (`pyql3/core/coords.py`).

This mapping was written three times before it was written once (`BUGS.md` B14), so these
tests do two jobs:

1. **Tie it to the real data transform.** `ImageViewer.apply_spatial_transforms()` is what
   actually puts pixels on screen, so the oracle here is that method itself, applied to a
   labelled array: for every pixel of a non-square array, in all eight flip × rotation
   combinations, the mapper must say where the label actually went. Nothing weaker than this
   is worth asserting — a self-consistent round trip passes happily while both directions
   are wrong together.
2. **Pin the behaviour the old call sites had**, by keeping verbatim copies of the two
   correct implementations and asserting the new one agrees with them. Where the third
   implementation disagreed (B13), the disagreement is asserted explicitly, so the fix is
   recorded rather than assumed.

`apply_spatial_transforms` reads only `self.rot_angle` and `self.flip`, so it can be called
unbound against a stub — no `QApplication`, and no risk of testing a reimplementation of the
thing under test.
"""
import math
from types import SimpleNamespace

import numpy as np
import pytest

from pyql3.core import coords
from pyql3.gui.viewers.image_viewer import ImageViewer

#: Deliberately non-square and not a multiple of anything: a square array hides every
#: extent-swap bug, which is how B13 survived.
NX, NY = 5, 3

COMBINATIONS = [(flip, rot) for flip in (False, True) for rot in (0, 90, 180, 270)]
COMBINATION_IDS = [f"flip={f}-rot={r}" for f, r in COMBINATIONS]


def assert_same_direction(got, expected, message=""):
    """Compare two angles as directions, so 0° and 360° are equal.

    `atan2` returns a tiny negative number where the exact answer is zero, and `% 360` turns
    that into 359.999..., so a plain comparison fails on a difference of nothing.
    """
    difference = (float(got) - float(expected)) % 360.0
    assert min(difference, 360.0 - difference) < 1e-6, \
        message or f"{got}° and {expected}° are different directions"


def transform(arr, flip, rot_angle):
    """Run the production transform against a stub carrying just the two settings."""
    stub = SimpleNamespace(flip=flip, rot_angle=rot_angle)
    return ImageViewer.apply_spatial_transforms(stub, arr)


def oracle(flip, rot_angle, nx=NX, ny=NY):
    """Where each orig pixel really ends up: `{(ix, iy): (display_x, display_y)}`."""
    labels = np.arange(nx * ny).reshape(nx, ny)
    shown = transform(labels, flip, rot_angle)
    placed = {}
    for ix in range(nx):
        for iy in range(ny):
            (dx, dy), = np.argwhere(shown == labels[ix, iy])
            placed[(ix, iy)] = (int(dx), int(dy))
    return placed


# ----------------------------------------------------------------- against the data

@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_orig_to_display_matches_the_real_data_transform(flip, rot):
    for (ix, iy), (want_x, want_y) in oracle(flip, rot).items():
        got = coords.orig_to_display(ix, iy, NX, NY, flip=flip, rot_angle=rot)
        assert got == (float(want_x), float(want_y)), (
            f"orig ({ix},{iy}) with flip={flip} rot={rot}: apply_spatial_transforms puts it "
            f"at {(want_x, want_y)}, mapper says {got}")


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_display_to_orig_matches_the_real_data_transform(flip, rot):
    for (ix, iy), (dx, dy) in oracle(flip, rot).items():
        got = coords.display_to_orig(dx, dy, NX, NY, flip=flip, rot_angle=rot)
        assert got == (float(ix), float(iy)), (
            f"display ({dx},{dy}) with flip={flip} rot={rot} came from orig {(ix, iy)}, "
            f"mapper says {got}")


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_display_dims_matches_the_transformed_shape(flip, rot):
    shown = transform(np.zeros((NX, NY)), flip, rot)
    assert coords.display_dims(NX, NY, rot) == shown.shape
    assert coords.orig_dims(*shown.shape, rot) == (NX, NY)


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_the_transform_of_a_3d_cube_places_pixels_identically(flip, rot):
    """A cube is transformed on axes (1, 2); the spatial mapping must not depend on that."""
    labels = np.arange(NX * NY).reshape(1, NX, NY)
    shown = transform(labels, flip, rot)[0]
    for (ix, iy), (want_x, want_y) in oracle(flip, rot).items():
        (dx, dy), = np.argwhere(shown == labels[0, ix, iy])
        assert (dx, dy) == (want_x, want_y)
        assert coords.orig_to_display(ix, iy, NX, NY, flip=flip, rot_angle=rot) == \
            (float(want_x), float(want_y))


# ------------------------------------------------------------------- round trips

@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_round_trip_over_fractional_coordinates(flip, rot):
    """Regions and ROI centres are not on integers, so half-pixel values must survive."""
    for x in np.arange(0, NX, 0.5):
        for y in np.arange(0, NY, 0.5):
            dx, dy = coords.orig_to_display(x, y, NX, NY, flip=flip, rot_angle=rot)
            back = coords.display_to_orig(dx, dy, NX, NY, flip=flip, rot_angle=rot)
            assert back == pytest.approx((x, y)), f"({x},{y}) -> ({dx},{dy}) -> {back}"


@pytest.mark.parametrize("rot", [0, 90, 180, 270, 360, 450, -90])
def test_rotation_angle_is_taken_modulo_a_full_turn(rot):
    """`rot_angle` is set from a dialog and a full turn must not be a different transform."""
    equivalent = rot % 360
    assert coords.orig_to_display(1, 2, NX, NY, rot_angle=rot) == \
        coords.orig_to_display(1, 2, NX, NY, rot_angle=equivalent)


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_the_array_mapping_agrees_with_the_scalar_one(flip, rot):
    """The catalog maps whole columns at once (R31); NaN marks a row with no position."""
    xs = np.array([0.0, 1.5, 4.0, np.nan, 2.25])
    ys = np.array([0.0, 2.0, 0.5, 1.0, np.nan])

    got_x, got_y = coords.orig_to_display_arrays(xs, ys, NX, NY, flip=flip, rot_angle=rot)

    for x, y, gx, gy in zip(xs, ys, got_x, got_y, strict=True):
        if np.isnan(x) or np.isnan(y):
            assert np.isnan(gx) or np.isnan(gy)
            continue
        assert (gx, gy) == coords.orig_to_display(x, y, NX, NY, flip=flip, rot_angle=rot)


# ------------------------------------------------------------------------ angles

@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
@pytest.mark.parametrize("angle", [0, 30, 45, 90, 135, 180, 270, 315])
def test_angles_follow_the_mapped_geometry(flip, rot, angle):
    """An angle must agree with what the (already verified) point mapping does to a vector.

    A box's position angle and an arrow's heading are directions between two points, so the
    angle mapping is only right if it matches two mapped points. Point mapping is verified
    against the data transform above, which is what makes this a real check and not a
    restatement of the implementation.
    """
    x0, y0 = (NX - 1) / 2.0, (NY - 1) / 2.0
    r = 1.5
    x1 = x0 + r * math.cos(math.radians(angle))
    y1 = y0 + r * math.sin(math.radians(angle))

    d0 = coords.orig_to_display(x0, y0, NX, NY, flip=flip, rot_angle=rot)
    d1 = coords.orig_to_display(x1, y1, NX, NY, flip=flip, rot_angle=rot)
    expected = math.degrees(math.atan2(d1[1] - d0[1], d1[0] - d0[0])) % 360.0

    got = coords.orig_angle_to_display(angle, flip=flip, rot_angle=rot)
    assert_same_direction(
        got, expected,
        f"angle {angle} with flip={flip} rot={rot}: mapped points give {expected}, "
        f"angle mapper gives {got}")


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
@pytest.mark.parametrize("angle", [0, 30, 90, 200, 359])
def test_angle_round_trip(flip, rot, angle):
    there = coords.orig_angle_to_display(angle, flip=flip, rot_angle=rot)
    assert_same_direction(coords.display_angle_to_orig(there, flip=flip, rot_angle=rot), angle)


# ------------------------------------------- characterisation: the old implementations

def old_plot_catalog_map_to_display(display_shape, flip, rot_angle, orig_x, orig_y):
    """`plot_catalog.map_to_display` as it stood at commit `dfdda2d`, verbatim.

    B14 named this the correct one of the three, so agreeing with it is the guarantee that
    the catalog overlay did not move.
    """
    shape = display_shape
    is_3d = (len(shape) == 3)
    max_x = shape[1] if is_3d else shape[0]
    max_y = shape[2] if is_3d else shape[1]

    k = rot_angle // 90
    orig_max_x = max_y if k % 2 == 1 else max_x
    orig_max_y = max_x if k % 2 == 1 else max_y

    curr_x, curr_y = float(orig_x), float(orig_y)

    if flip:
        curr_x = orig_max_x - 1 - curr_x

    for _ in range(k):
        curr_x, curr_y = orig_max_y - 1 - curr_y, curr_x
        orig_max_x, orig_max_y = orig_max_y, orig_max_x

    return curr_x, curr_y


def old_readout_display_to_orig(display_shape, flip, rot_angle, x, y):
    """The inverse inlined in `image_viewer.mouse_moved` at commit `dfdda2d`, verbatim."""
    is_3d = (len(display_shape) == 3)
    max_x = display_shape[1] if is_3d else display_shape[0]
    max_y = display_shape[2] if is_3d else display_shape[1]

    orig_x, orig_y = x, y
    k = rot_angle // 90
    if k != 0:
        if k == 1:
            orig_x, orig_y = orig_y, max_x - 1 - orig_x
        elif k == 2:
            orig_x, orig_y = max_x - 1 - orig_x, max_y - 1 - orig_y
        elif k == 3:
            orig_x, orig_y = max_y - 1 - orig_y, orig_x

    if flip:
        orig_max_x = max_y if k % 2 == 1 else max_x
        orig_x = orig_max_x - 1 - orig_x

    return orig_x, orig_y


def old_depth_plot_display_to_orig(x_len, flip, rot_angle, cx, cy):
    """`depth_plot`'s un-rotation at commit `dfdda2d`, verbatim — the B13 version.

    `x_len` is the *display* x extent, reused for every step and for the flip.
    """
    k = rot_angle // 90
    for _ in range((4 - k) % 4):
        cx, cy = cy, x_len - 1 - cx
    if flip:
        cx = x_len - 1 - cx
    return cx, cy


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_agrees_with_the_previous_catalog_implementation(flip, rot):
    display_shape = coords.display_dims(NX, NY, rot)
    for ix in range(NX):
        for iy in range(NY):
            assert coords.orig_to_display(ix, iy, NX, NY, flip=flip, rot_angle=rot) == \
                old_plot_catalog_map_to_display(display_shape, flip, rot, ix, iy)


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_agrees_with_the_previous_readout_implementation(flip, rot):
    dx_len, dy_len = coords.display_dims(NX, NY, rot)
    for dx in range(dx_len):
        for dy in range(dy_len):
            assert coords.display_to_orig(dx, dy, NX, NY, flip=flip, rot_angle=rot) == \
                old_readout_display_to_orig((dx_len, dy_len), flip, rot, dx, dy)


@pytest.mark.parametrize("rot", [90, 270])
def test_the_old_depth_plot_un_rotation_was_wrong_for_non_square_planes(rot):
    """B13: it reused the display x extent for both axes, so 90°/270° landed elsewhere.

    Asserting the disagreement records what the fix changed. A square plane hid it, which is
    why this is parametrised on a non-square one only.
    """
    dx_len, _ = coords.display_dims(NX, NY, rot)
    disagreements = 0
    for (ix, iy), (dx, dy) in oracle(False, rot).items():
        correct = coords.display_to_orig(dx, dy, NX, NY, rot_angle=rot)
        assert correct == (float(ix), float(iy))
        if old_depth_plot_display_to_orig(dx_len, False, rot, dx, dy) != correct:
            disagreements += 1

    assert disagreements, (
        f"the old depth_plot un-rotation was expected to disagree at rot={rot} on a "
        f"{NX}x{NY} plane; if it no longer does, B13 was misdiagnosed")


def test_the_old_depth_plot_un_rotation_was_right_when_unrotated():
    """The B13 bug needed a rotation to show up, so the common case was unaffected."""
    for flip in (False, True):
        for (ix, iy), (dx, dy) in oracle(flip, 0).items():
            assert old_depth_plot_display_to_orig(NX, flip, 0, dx, dy) == \
                coords.display_to_orig(dx, dy, NX, NY, flip=flip, rot_angle=0) == \
                (float(ix), float(iy))


# ------------------------------------------------------------ pixel conventions

def test_pixel_convention_helpers_with_worked_examples():
    # FITS/ds9 count from 1 with the pixel centre on the integer; numpy counts from 0.
    assert coords.fits_to_index(1.0) == 0.0
    assert coords.index_to_fits(0.0) == 1.0
    assert coords.fits_to_index(coords.index_to_fits(7.0)) == 7.0
    assert coords.fits_to_index_array([1.0, 11.5, float("nan")])[:2].tolist() == [0.0, 10.5]
    assert np.isnan(coords.fits_to_index_array([1.0, 11.5, float("nan")])[2])

    # A pyqtgraph ImageItem draws pixel i across [i, i+1), so its centre is at i + 0.5.
    assert coords.index_to_item(0) == 0.5
    assert coords.item_to_index(0.5) == 0.0
    assert coords.item_to_index(coords.index_to_item(3)) == 3.0

    # Anywhere inside pixel 3 belongs to pixel 3; the boundary belongs to the pixel above.
    assert coords.item_to_pixel(3.0) == 3
    assert coords.item_to_pixel(3.99) == 3
    assert coords.item_to_pixel(4.0) == 4


def test_a_fits_pixel_centre_is_half_a_pixel_off_the_item_origin():
    """ds9 pixel 1 is numpy index 0, whose centre sits at 0.5 in ImageItem coordinates."""
    assert coords.index_to_item(coords.fits_to_index(1.0)) == 0.5


# ------------------------------------------------------- the viewer's own adapters

@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_viewer_adapters_use_the_orig_spatial_dimensions(loaded_viewer, flip, rot):
    """The adapters must read `transposed_data`'s extents, not `display_data`'s."""
    viewer = loaded_viewer
    viewer.flip = flip
    viewer.rot_angle = rot

    nx, ny = viewer.orig_spatial_dims()
    assert (nx, ny) == viewer.transposed_data.shape[-2:]

    for point in [(0, 0), (nx - 1, ny - 1), (2.5, 1.5)]:
        shown = viewer.orig_to_display(*point)
        assert shown == coords.orig_to_display(*point, nx, ny, flip=flip, rot_angle=rot)
        assert viewer.display_to_orig(*shown) == pytest.approx(point)

    xs, ys = viewer.orig_to_display_arrays(np.array([0.0, nx - 1, 2.5]),
                                           np.array([0.0, ny - 1, 1.5]))
    for point, gx, gy in zip([(0, 0), (nx - 1, ny - 1), (2.5, 1.5)], xs, ys, strict=True):
        assert (gx, gy) == viewer.orig_to_display(*point)


def test_viewer_reports_which_fits_axes_are_displayed(loaded_viewer):
    """An OSIRIS cube shows FITS axis 3 against axis 2, which ds9's image frame never means."""
    assert loaded_viewer.display_axis_indices() == (2, 1)


def test_a_two_d_image_is_always_displayed_on_axes_one_and_two(qapp, sample_2d_fits):
    from pyql3.core.fits_reader import FitsReader

    reader = FitsReader(sample_2d_fits)
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)

    assert viewer.display_axis_indices() == (0, 1)


def test_viewer_adapters_report_nothing_without_data(qapp):
    viewer = ImageViewer()
    assert viewer.orig_spatial_dims() is None
    assert viewer.orig_to_display(1, 2) is None
    assert viewer.display_to_orig(1, 2) is None
    assert viewer.orig_to_display_arrays(np.array([1.0]), np.array([2.0])) is None


def test_a_right_click_is_clipped_to_the_displayed_plane(qapp, tmp_path):
    """The bounds came from `transposed_data`, with its two extents the wrong way round.

    `shape[-1]` is the y extent, not x, so on a non-square cube a click near an edge was clipped to
    the wrong pixel — and under a 90° rotation the whole plane has different extents. A square
    fixture hides both.
    """
    from unittest.mock import Mock

    import numpy as np
    from astropy.io import fits
    from PySide6.QtCore import QPointF, Qt

    from pyql3.core.fits_reader import FitsReader

    path = str(tmp_path / "oblong.fits")
    fits.PrimaryHDU(np.zeros((6, 20, 9), dtype=np.float32)).writeto(path)
    reader = FitsReader(path)
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)

    nx, ny = viewer.orig_spatial_dims()
    assert nx != ny, "this test needs a non-square plane"

    def right_click_at(x, y):
        event = Mock()
        event.button.return_value = Qt.MouseButton.RightButton
        event.scenePos.return_value = viewer.imv.getImageItem().mapToScene(QPointF(x, y))
        viewer.on_view_clicked(event)
        return viewer.last_right_click_pixel_pos

    # Far outside the plane: the click clips to the last real pixel of each axis.
    assert right_click_at(999, 999) == (nx - 1, ny - 1)

    # ...and after a quarter turn the extents swap, so the clip has to follow.
    viewer.rot_angle = 90
    viewer.refresh_display()
    assert right_click_at(999, 999) == (ny - 1, nx - 1)


# ------------------------------------------------------------------ box_index_slices

@pytest.mark.parametrize("box, nx, ny, expected", [
    # A box on pixel edges covers exactly those pixels.
    ((35.0, 25.0, 10, 10), 80, 60, (slice(35, 45), slice(25, 35))),
    # Half a pixel over: the centres in [35.5, 45.5) are still pixels 35..44.
    ((35.5, 25.5, 10, 10), 80, 60, (slice(35, 45), slice(25, 35))),
    # Hanging off the low edge keeps only what is on the image, not the box's full width.
    ((-5, 0, 10, 4), 30, 30, (slice(0, 5), slice(0, 4))),
    # Wholly off either side: nothing, not the edge column.
    ((-50, 0, 10, 4), 30, 30, None),
    ((100, 0, 10, 4), 30, 30, None),
])
def test_box_index_slices(box, nx, ny, expected):
    assert coords.box_index_slices(*box, nx, ny) == expected


def test_box_index_slices_clips_the_high_edge_and_rejects_an_empty_box():
    # A box over the high edge keeps its low end, and a box too thin to contain a pixel
    # centre selects nothing.
    assert coords.box_index_slices(25.0, 0.0, 10, 4, 30, 30) == (slice(25, 30), slice(0, 4))
    assert coords.box_index_slices(35.2, 5.0, 1e-5, 4, 80, 60) is None


def test_circle_index_mask_keeps_the_drawn_circle_off_the_edge():
    """R80: the circle is the one the box draws, whatever part of the box is on the image."""
    # A 6 x 6 box on pixel edges: the circle of radius 3 about its centre, pixel by pixel
    whole = coords.circle_index_mask(10.0, 20.0, 6, 6, slice(10, 16), slice(20, 26))
    ix, iy = np.mgrid[:6, :6]
    assert (whole == (((ix - 2.5) ** 2 + (iy - 2.5) ** 2) <= 9.0)).all()

    # Off the low x edge: the on-image columns of that same circle, not a circle refitted to them
    x_slice, y_slice = coords.box_index_slices(-3.0, 20.0, 6, 6, 30, 30)
    clipped = coords.circle_index_mask(-3.0, 20.0, 6, 6, x_slice, y_slice)
    assert clipped.shape == (3, 6)
    assert (clipped == whole[3:]).all()


# ------------------------------------------------- directions on the sky (BUGS.md R37)

def _rotated_non_square_wcs(rotation_deg=30.0):
    """0.36" x 0.72" pixels, turned on the sky: the case a rectangle probe got wrong."""
    from astropy.wcs import WCS

    wcs = WCS(naxis=2)
    wcs.wcs.ctype, wcs.wcs.crval = ['RA---TAN', 'DEC--TAN'], [266.4, 34.0]
    wcs.wcs.cdelt, wcs.wcs.crpix = [-1e-4, 2e-4], [50, 50]
    turn = math.radians(rotation_deg)
    wcs.wcs.pc = [[math.cos(turn), -math.sin(turn)], [math.sin(turn), math.cos(turn)]]
    return wcs


@pytest.mark.parametrize("axes", [(0, 1), (1, 0)], ids=["x=ra", "x=dec"])
def test_direction_round_trip_on_a_rotated_non_square_wcs(axes):
    """A direction is mapped as a direction, both ways, from orig coordinates (R37, R38)."""
    from astropy.coordinates import SkyCoord

    from pyql3.core.sky import CelestialMap

    mapping = CelestialMap(_rotated_non_square_wcs(), *axes)
    x, y = 30.0, 20.0
    tail = mapping.skycoord(*mapping.to_sky(x, y))

    for angle in range(0, 360, 15):
        sky_angle = mapping.direction_to_sky(x, y, angle)

        # What it should be: the position angle of a point one pixel along, plus 90 degrees.
        step = (x + math.cos(math.radians(angle)), y + math.sin(math.radians(angle)))
        ahead = SkyCoord(*mapping.to_sky(*step), unit="deg", frame=mapping.frame)
        expected = (tail.position_angle(ahead).deg + 90.0) % 360.0
        difference = (sky_angle - expected + 180.0) % 360.0 - 180.0
        assert abs(difference) < 0.01, (angle, sky_angle, expected)

        back = mapping.direction_from_sky(tail, sky_angle)
        assert abs((back - angle + 180.0) % 360.0 - 180.0) < 0.01, (angle, back)

        length = mapping.length_to_sky(x, y, angle, 10.0)
        assert mapping.length_from_sky(tail, sky_angle, length) == pytest.approx(10.0, abs=1e-3)

"""The N/E position-angle compass, and the "North Up" button (`BUGS.md` B20).

Nothing covered the compass before this file, which is how B20 survived: the angle was
composed as `(base + rot + view_rotation)` and *then* mirrored for a flip, while the display
flips the array **before** rotating it and applies `view_rotation` afterwards as a transform
on the ImageItem. With a flip, that pointed N and E backwards at 90°/270° and turned the wrong
way for any view rotation.

The oracle is geometric rather than a restatement of the fix: North is a direction in orig
space (whatever `get_north_angle_base()` says), so a step from the centre toward North is two
orig points, and where those two points land on screen is what the arrow must follow. The
point mapping used for that is `coords.orig_to_display`, which `tests/test_coords.py` pins
against `ImageViewer.apply_spatial_transforms` itself — so this chains onto verified ground
rather than assuming the thing under test.

`test_unrotated_unflipped_compass_is_the_base_angle` anchors the chain independently: with no
transforms at all the drawn angle must simply be the base angle.
"""
import math

import pytest

from pyql3.core import coords
from pyql3.core.fits_reader import FitsReader
from pyql3.gui.tools.rotate import RotateDialog
from pyql3.gui.viewers.image_viewer import ImageViewer

COMBINATIONS = [(flip, rot) for flip in (False, True) for rot in (0, 90, 180, 270)]
COMBINATION_IDS = [f"flip={f}-rot={r}" for f, r in COMBINATIONS]


@pytest.fixture
def pa_viewer(qapp, sample_3d_fits):
    """A viewer with a real celestial WCS, so `get_north_angle_base` reports actual angles."""
    reader = FitsReader(sample_3d_fits)
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)
    _, _, is_wcs = viewer.get_north_angle_base()
    assert is_wcs, "fixture must have usable WCS/PA info or these tests prove nothing"
    return viewer


def assert_same_direction(got, expected, message=""):
    """Compare angles as directions, so 0° and 360° are the same."""
    difference = (float(got) - float(expected)) % 360.0
    assert min(difference, 360.0 - difference) < 1e-6, \
        message or f"{got}° and {expected}° are different directions"


def direction_on_screen(viewer, orig_angle):
    """Where a step in direction `orig_angle` (orig space) points on screen, in degrees.

    Independent of the angle code under test: it maps two *points* and measures the result.
    `view_rotation` is added afterwards because it is a transform on the ImageItem, applied
    to the already-flipped, already-rotated array.
    """
    nx, ny = viewer.orig_spatial_dims()
    x0, y0 = (nx - 1) / 2.0, (ny - 1) / 2.0
    r = 1.5
    x1 = x0 + r * math.cos(math.radians(orig_angle))
    y1 = y0 + r * math.sin(math.radians(orig_angle))

    dx0, dy0 = coords.orig_to_display(x0, y0, nx, ny, flip=viewer.flip, rot_angle=viewer.rot_angle)
    dx1, dy1 = coords.orig_to_display(x1, y1, nx, ny, flip=viewer.flip, rot_angle=viewer.rot_angle)
    return (math.degrees(math.atan2(dy1 - dy0, dx1 - dx0)) + viewer.view_rotation) % 360.0


def drawn_arrow_direction(viewer, which):
    """The direction the drawn compass arrow points, read back off the graphics items.

    The arrow's tip is placed at `centre + L * (cos, sin)` of the angle, so the direction is
    recovered from the tip's offset from the view centre rather than from the item's `angle`
    property, which is the tail-to-head convention plus 180°.
    """
    arrow = getattr(viewer, f'pa_arrow_{which}')
    assert arrow is not None, f"no {which} arrow was drawn"
    rect = viewer.imv.getView().viewRect()
    tip = arrow.pos()
    return math.degrees(math.atan2(tip.y() - rect.center().y(),
                                   tip.x() - rect.center().x())) % 360.0


def old_buggy_angle(base, flip, rot_angle, view_rotation):
    """The pre-B20 expression, verbatim, for asserting what actually changed."""
    theta = (base + rot_angle + view_rotation) % 360.0
    if flip:
        theta = (180.0 - theta) % 360.0
    return theta


# ------------------------------------------------------------------- the compass

def test_unrotated_unflipped_compass_is_the_base_angle(pa_viewer):
    """The anchor: with no transforms the drawn angle is the base angle, unmediated."""
    base_n, base_e, _ = pa_viewer.get_north_angle_base()
    pa_viewer.toggle_position_angle(True)

    assert_same_direction(drawn_arrow_direction(pa_viewer, 'n'), base_n)
    assert_same_direction(drawn_arrow_direction(pa_viewer, 'e'), base_e)


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
@pytest.mark.parametrize("view_rotation", [0.0, 30.0, -45.0])
def test_compass_follows_where_north_actually_points(pa_viewer, flip, rot, view_rotation):
    base_n, base_e, _ = pa_viewer.get_north_angle_base()
    pa_viewer.flip = flip
    pa_viewer.rot_angle = rot
    pa_viewer.refresh_display()
    pa_viewer.apply_view_rotation(view_rotation)
    pa_viewer.toggle_position_angle(True)

    assert_same_direction(
        drawn_arrow_direction(pa_viewer, 'n'), direction_on_screen(pa_viewer, base_n),
        f"North arrow: drawn {drawn_arrow_direction(pa_viewer, 'n')}, "
        f"mapped points say {direction_on_screen(pa_viewer, base_n)} "
        f"(flip={flip} rot={rot} view={view_rotation})")
    assert_same_direction(
        drawn_arrow_direction(pa_viewer, 'e'), direction_on_screen(pa_viewer, base_e))


@pytest.mark.parametrize("rot", [90, 270])
def test_a_flip_with_a_quarter_turn_used_to_point_backwards(pa_viewer, rot):
    """B20's headline symptom: N and E were 180° out, i.e. pointing exactly the wrong way."""
    base_n, _, _ = pa_viewer.get_north_angle_base()
    pa_viewer.flip = True
    pa_viewer.rot_angle = rot
    pa_viewer.refresh_display()
    pa_viewer.toggle_position_angle(True)

    correct = direction_on_screen(pa_viewer, base_n)
    old = old_buggy_angle(base_n, True, rot, 0.0)

    assert_same_direction(drawn_arrow_direction(pa_viewer, 'n'), correct)
    assert_same_direction(old, correct + 180.0,
                          f"expected the old expression to be 180° out at rot={rot}; "
                          f"it gave {old} against {correct}. If not, B20 was misdiagnosed")


def test_a_flip_with_a_view_rotation_used_to_turn_the_wrong_way(pa_viewer):
    """B20's second symptom: the mirror was applied to `view_rotation` as well."""
    base_n, _, _ = pa_viewer.get_north_angle_base()
    pa_viewer.flip = True
    pa_viewer.refresh_display()
    pa_viewer.apply_view_rotation(30.0)
    pa_viewer.toggle_position_angle(True)

    correct = direction_on_screen(pa_viewer, base_n)
    old = old_buggy_angle(base_n, True, 0, 30.0)

    assert_same_direction(drawn_arrow_direction(pa_viewer, 'n'), correct)
    # Mirroring the sum negates the view rotation, so the error is twice it.
    assert_same_direction(old, correct - 60.0)


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_no_flip_cases_are_unchanged_by_the_fix(pa_viewer, flip, rot):
    """With `flip` off the two expressions always agreed, and must still."""
    if flip:
        pytest.skip("this test is about the unaffected half of the matrix")
    base_n, _, _ = pa_viewer.get_north_angle_base()
    pa_viewer.rot_angle = rot
    pa_viewer.refresh_display()

    shown, _, _ = pa_viewer.north_east_display_angles()
    assert_same_direction(shown, old_buggy_angle(base_n, False, rot, 0.0))


def test_compass_reports_no_wcs_without_pa_information(qapp, sample_2d_fits):
    """A plain image has no celestial WCS, and the compass must decline rather than guess."""
    reader = FitsReader(sample_2d_fits)
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)

    theta_n, theta_e, is_wcs = viewer.north_east_display_angles()
    assert (theta_n, theta_e, is_wcs) == (None, None, False)

    viewer.toggle_position_angle(True)
    assert getattr(viewer, 'pa_arrow_n', None) is None


# ----------------------------------------------------------------- the North Up button

@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_north_up_actually_puts_north_up(qapp, pa_viewer, flip, rot):
    """The button's whole promise, which B20 broke for every flipped orientation."""
    base_n, _, _ = pa_viewer.get_north_angle_base()
    pa_viewer.flip = flip
    pa_viewer.rot_angle = rot
    pa_viewer.refresh_display()

    dialog = RotateDialog(None, pa_viewer)
    try:
        dialog.on_north_up_clicked()

        assert_same_direction(
            direction_on_screen(pa_viewer, base_n), 90.0,
            f"after North Up, North points {direction_on_screen(pa_viewer, base_n)}° "
            f"instead of straight up (flip={flip} rot={rot})")
        assert "North is Up" in dialog.lbl_pa.text(), dialog.lbl_pa.text()

        pa_viewer.toggle_position_angle(True)
        assert_same_direction(drawn_arrow_direction(pa_viewer, 'n'), 90.0)
    finally:
        dialog.close()


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_north_angle_readout_matches_the_drawn_compass(qapp, pa_viewer, flip, rot):
    """The label and the arrows must not disagree — they were two copies of the same bug."""
    base_n, _, _ = pa_viewer.get_north_angle_base()
    pa_viewer.flip = flip
    pa_viewer.rot_angle = rot
    pa_viewer.refresh_display()
    pa_viewer.apply_view_rotation(12.0)

    dialog = RotateDialog(None, pa_viewer)
    try:
        expected_offset = ((direction_on_screen(pa_viewer, base_n) - 90.0 + 180.0) % 360.0) - 180.0
        text = dialog.lbl_pa.text()

        if abs(expected_offset) < 0.05:
            assert "North is Up" in text, text
        else:
            assert f"{abs(expected_offset):.1f}°" in text, (text, expected_offset)
            assert ("CW" if expected_offset < 0 else "CCW") in text, (text, expected_offset)
    finally:
        dialog.close()

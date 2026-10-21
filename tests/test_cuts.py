import numpy as np
import pytest
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.cuts import CutPlotDialog


@pytest.fixture
def ridge_viewer(qapp):
    """Viewer holding a 2D image with a bright diagonal ridge.

    A diagonal ridge makes the extracted profile sensitive to both the
    endpoints and the extraction width of the diagonal cut ROI.
    """
    ny, nx = 80, 80
    yy, xx = np.mgrid[0:ny, 0:nx]
    img = (np.exp(-((yy - xx) ** 2) / (2 * 3.0 ** 2)) * 100.0 + 1.0).astype(np.float32)
    viewer = ImageViewer()
    viewer.set_data(img)
    return viewer


def _profile(dialog):
    _, y = dialog.plot_curve.getData()
    return None if y is None else np.asarray(y, dtype=float)


def test_diagonal_cut_spinboxes_move_roi(ridge_viewer):
    """Editing the Point 1 / Point 2 spinboxes must move the ROI, not raise.

    Regression: on_spin_changed() called QGraphicsItem.setPos() with a list,
    which raises TypeError in PySide6 and left roi.blockSignals(True) set, so
    the dialog stayed frozen for the rest of its life.
    """
    dialog = CutPlotDialog('diagonal', None, ridge_viewer)
    before = _profile(dialog)

    dialog.spin_x0.setValue(10)
    dialog.spin_y0.setValue(12)
    dialog.spin_x1.setValue(70)
    dialog.spin_y1.setValue(68)

    assert not dialog.roi.signalsBlocked(), "ROI signals were left blocked by on_spin_changed()"

    after = _profile(dialog)
    assert after is not None and len(after) > 0, "Diagonal cut produced no profile"
    assert not np.array_equal(before, after), "Profile did not change after moving the endpoints"

    # The ROI must still be live: a subsequent drag has to update the plot
    dragged_from = _profile(dialog)
    dialog.roi.setPos(dialog.roi.pos().x() + 6, dialog.roi.pos().y() - 6)
    assert not np.array_equal(dragged_from, _profile(dialog)), \
        "Dragging the ROI no longer updates the profile"

    dialog.close()


def test_diagonal_cut_spin_roi_round_trip_is_stable(ridge_viewer):
    """spin -> ROI -> spin must round trip, or on_spin_changed and
    sync_spins_and_plot will fight each other on every drag."""
    dialog = CutPlotDialog('diagonal', None, ridge_viewer)

    expected = (10, 12, 70, 68)
    dialog.spin_x0.setValue(expected[0])
    dialog.spin_y0.setValue(expected[1])
    dialog.spin_x1.setValue(expected[2])
    dialog.spin_y1.setValue(expected[3])

    dialog.sync_spins_and_plot()   # what a drag triggers
    first = (dialog.spin_x0.value(), dialog.spin_y0.value(),
             dialog.spin_x1.value(), dialog.spin_y1.value())
    for got, want in zip(first, expected, strict=True):
        assert abs(got - want) <= 1, f"Endpoint round trip drifted: {first} vs {expected}"

    dialog.sync_spins_and_plot()   # and it must be idempotent, not drift further
    second = (dialog.spin_x0.value(), dialog.spin_y0.value(),
              dialog.spin_x1.value(), dialog.spin_y1.value())
    assert first == second, f"Repeated sync drifted the endpoints: {first} -> {second}"
    assert dialog.spin_w.value() == 5, f"Width drifted during round trip: {dialog.spin_w.value()}"

    dialog.close()


def test_diagonal_cut_width_changes_extraction(ridge_viewer):
    """The Width box must widen the ROI geometry and change the profile.

    Regression: it used to call self.roi.pen.setWidth(), a cosmetic no-op that
    left the sampled region untouched.
    """
    dialog = CutPlotDialog('diagonal', None, ridge_viewer)
    dialog.spin_x0.setValue(5)
    dialog.spin_y0.setValue(5)
    dialog.spin_x1.setValue(75)
    dialog.spin_y1.setValue(75)

    narrow = _profile(dialog)
    narrow_size = (dialog.roi.size().x(), dialog.roi.size().y())
    assert narrow_size[1] == pytest.approx(5.0), f"Unexpected initial ROI width: {narrow_size}"

    dialog.spin_w.setValue(25)
    wide = _profile(dialog)
    wide_size = (dialog.roi.size().x(), dialog.roi.size().y())

    assert wide_size[1] == pytest.approx(25.0), f"ROI width did not follow the spinbox: {wide_size}"
    assert wide_size[0] == pytest.approx(narrow_size[0]), \
        f"ROI length changed when only the width was edited: {narrow_size} -> {wide_size}"
    assert not np.array_equal(narrow, wide), "Extracted profile ignored the width change"

    # A wider box over a narrow ridge must dilute the mean signal
    assert np.nanmean(wide) < np.nanmean(narrow), \
        f"Widening did not dilute the ridge: {np.nanmean(narrow)} -> {np.nanmean(wide)}"

    dialog.close()


def test_diagonal_cut_width_syncs_from_geometry(ridge_viewer):
    """Dragging the ROI's width handle must be reflected in the Width box."""
    dialog = CutPlotDialog('diagonal', None, ridge_viewer)
    dialog.roi.setSize([dialog.roi.size().x(), 17.0])
    assert dialog.spin_w.value() == 17, \
        f"Width spinbox did not follow the ROI geometry: {dialog.spin_w.value()}"
    dialog.close()


@pytest.mark.parametrize("cut_type,lo_attr,hi_attr", [
    ('horizontal', 'spin_y0', 'spin_y1'),
    ('vertical', 'spin_x0', 'spin_x1'),
])
def test_linear_cut_spinboxes_still_track(ridge_viewer, cut_type, lo_attr, hi_attr):
    """The horizontal/vertical branches of on_spin_changed must keep working."""
    dialog = CutPlotDialog(cut_type, None, ridge_viewer)
    getattr(dialog, lo_attr).setValue(20)
    getattr(dialog, hi_attr).setValue(50)

    lo, hi = dialog.roi.getRegion()
    assert (round(lo), round(hi)) == (20, 50), f"{cut_type} region did not track the spinboxes"
    assert not dialog.roi.signalsBlocked(), "ROI signals were left blocked"

    profile = _profile(dialog)
    assert profile is not None and len(profile) > 0, f"{cut_type} cut produced no profile"
    dialog.close()


def test_diagonal_cut_on_cube(loaded_viewer):
    """Interleaved spin edits, width changes, drags and slice steps on a 3D cube."""
    dialog = CutPlotDialog('diagonal', None, loaded_viewer)

    dialog.spin_x0.setValue(4)
    dialog.spin_y0.setValue(4)
    dialog.spin_x1.setValue(30)
    dialog.spin_y1.setValue(20)
    dialog.spin_w.setValue(9)
    dialog.roi.setPos(dialog.roi.pos().x() + 2, dialog.roi.pos().y() + 1)
    dialog.spin_w.setValue(3)
    dialog.combo_calc.setCurrentText("Median")
    loaded_viewer.imv.setCurrentIndex(7)

    assert not dialog.roi.signalsBlocked(), "ROI signals were left blocked"
    profile = _profile(dialog)
    assert profile is not None and len(profile) > 0, "Diagonal cut on a cube produced no profile"
    dialog.close()


def _dn_viewer(shape=(40, 40), value=7.0, itime_coadds=10.0):
    img = np.full(shape, value, dtype=np.float32)
    viewer = ImageViewer()
    viewer.set_data(img)
    viewer._itime_coadds = itime_coadds
    return viewer


@pytest.mark.parametrize("cut_type", ["horizontal", "vertical"])
def test_dn_multiplier_is_applied_exactly_once(qapp, cut_type):
    """Cut profiles read itime*coadds too high in Total DN mode.

    update_plot() took its pixels from display_data, which apply_transforms() has
    already multiplied, and then multiplied by data_multiplier again -- squaring the
    DN/s to Total DN conversion. With itime*coadds = 10 a flat 7.0 image plotted 700.
    """
    viewer = _dn_viewer()

    viewer.disp_as_dn = False
    viewer.refresh_display()
    dialog = CutPlotDialog(cut_type, None, viewer)
    dialog.combo_calc.setCurrentText("Average")
    dialog.update_plot()
    assert np.allclose(_profile(dialog)[20], 7.0)

    viewer.disp_as_dn = True
    viewer.refresh_display()
    dialog.update_plot()
    assert np.allclose(_profile(dialog)[20], 70.0), "multiplier applied twice"


def test_cut_uses_the_displayed_plane_in_collapse_mode(qapp):
    """In Z Range mode the screen shows a collapsed plane belonging to no single
    channel, and imv.currentIndex goes stale. The profile must follow what is
    displayed, which is what current_plane() returns."""
    cube = np.random.default_rng(0).normal(100, 10, (20, 30, 30)).astype(np.float32)
    viewer = ImageViewer()
    viewer.set_data(cube)
    viewer._itime_coadds = 4.0
    viewer.disp_as_dn = True
    viewer.txt_zmin.setText("4")
    viewer.txt_zmax.setText("6")
    viewer.radio_range.setChecked(True)

    dialog = CutPlotDialog("horizontal", None, viewer)
    dialog.combo_calc.setCurrentText("Average")
    dialog.update_plot()
    got = _profile(dialog)

    plane = viewer.current_plane()
    region = dialog.roi.getRegion()
    y0, y1 = int(round(min(region))), int(round(max(region)))
    expected = np.nanmean(plane[:, y0:y1], axis=1) * viewer.data_multiplier
    assert np.allclose(got, expected)

    # And it must track the range rather than a frozen channel.
    viewer.txt_zmin.setText("10")
    viewer.txt_zmax.setText("18")
    viewer.radio_range.setChecked(True)
    dialog.update_plot()
    assert not np.allclose(got, _profile(dialog))


def test_cuts_follow_boxcar_and_range(qapp):
    """The cuts listened to `imv.sigTimeChanged`, which a Z Range apply and a Boxcar width
    change at the same slider value never send: those are drawn past the ImageView, so the
    profile stayed on the last single channel (BUGS.md R14)."""
    channel_values = np.array([0., 50., 3., 80., 1., 90., 7., 20., 60., 5.])
    cube = np.broadcast_to(channel_values, (40, 30, 10)).astype(np.float32)  # channel k: values[k]
    viewer = ImageViewer()
    viewer.set_data(cube)
    dialogs = [CutPlotDialog(cut_type, None, viewer) for cut_type in ("horizontal", "vertical")]
    for dialog in dialogs:
        dialog.combo_calc.setCurrentText("Average")
        dialog.show()
    try:
        viewer.txt_boxcar.setText("3")
        viewer.btn_apply_slice.click()
        viewer.slider_slice.setValue(6)             # median of channels 5-7: 90, 7, 20
        for dialog in dialogs:
            assert np.allclose(_profile(dialog), 20.0), dialog.cut_type

        viewer.combo_collapse.setCurrentText("Mean")
        viewer.txt_zmin.setText("2")
        viewer.txt_zmax.setText("4")
        viewer.radio_range.setChecked(True)         # mean of channels 2-4: 3, 80, 1
        for dialog in dialogs:
            assert np.allclose(_profile(dialog), 28.0), dialog.cut_type

        viewer.txt_zmax.setText("3")
        viewer.btn_apply_range.click()              # mean of channels 2-3
        for dialog in dialogs:
            assert np.allclose(_profile(dialog), 41.5), dialog.cut_type
    finally:
        for dialog in dialogs:
            dialog.close()

"""Executable form of the CRITICAL data-state rule in AGENTS.md.

`ImageViewer` keeps three arrays. `raw_data` is the untouched FITS array and is what
every analytical calculation, export and header write must use. `transposed_data` is
reordered for pyqtgraph, and `display_data` additionally carries the DN multiplier,
flips and rotations. Confusing them writes cubes with permanently swapped physical
axes and a WCS that no longer describes the data — corrupted science that nothing
downstream would flag.

That rule has lived in prose. These tests make it fail loudly instead: whatever the
viewer does for display, the bytes and the WCS that reach disk must be unchanged.
"""

import numpy as np
import pytest
from astropy.io import fits
from astropy.wcs import WCS

from pyql3.core.fits_reader import FitsReader
from pyql3.gui.viewers.image_viewer import ImageViewer


WCS_KEYWORDS = (
    "CTYPE1", "CTYPE2", "CTYPE3",
    "CRVAL1", "CRVAL2", "CRVAL3",
    "CRPIX1", "CRPIX2", "CRPIX3",
    "CDELT1", "CDELT2", "CDELT3",
    "CUNIT1", "CUNIT2", "CUNIT3",
    "NAXIS", "NAXIS1", "NAXIS2", "NAXIS3",
)


@pytest.fixture
def osiris_cube(tmp_path):
    """An OSIRIS-ordered cube with asymmetric content in all three axes.

    Asymmetry matters: a cube that is symmetric under transposition would pass a
    round-trip test even if the axes were swapped.
    """
    nwave, ndec, nra = 12, 8, 5  # deliberately all different
    data = np.arange(nwave * ndec * nra, dtype=np.float32)
    data = data.reshape((nra, ndec, nwave))  # numpy order: (RA, DEC, WAVE)

    w = WCS(naxis=3)
    w.wcs.ctype = ["WAVE", "DEC--TAN", "RA---TAN"]
    w.wcs.crval = [2.1, 34.25, -118.5]
    w.wcs.cdelt = [2.5e-4, 1.1e-5, 1.3e-5]
    w.wcs.crpix = [1, 4, 3]
    w.wcs.cunit = ["um", "deg", "deg"]

    header = w.to_header()
    header["ITIME"] = 5.0
    header["COADDS"] = 3
    header["BUNIT"] = "DN/s"
    header["OBJECT"] = "GC"

    path = tmp_path / "cube.fits"
    fits.PrimaryHDU(data=data, header=header).writeto(path)
    return str(path), data


def _load_into_viewer(path):
    reader = FitsReader(path)
    viewer = ImageViewer()
    viewer.set_data(reader.get_data(), reader.get_header())
    return reader, viewer


def _wcs_cards(header):
    return {k: header[k] for k in WCS_KEYWORDS if k in header}


# ------------------------------------------------------- raw_data is inviolate


@pytest.mark.parametrize(
    "rot,flip,dn",
    [(0, False, False), (90, False, False), (180, True, False),
     (270, True, True), (90, True, True)],
)
def test_display_transforms_never_touch_raw_data(qapp, osiris_cube, rot, flip, dn):
    """Rotation, flip and the DN multiplier are display concerns only."""
    path, original = osiris_cube
    reader, viewer = _load_into_viewer(path)
    try:
        viewer.rot_angle = rot
        viewer.flip = flip
        viewer.disp_as_dn = dn
        viewer.apply_axis_mapping()

        assert np.array_equal(viewer.raw_data, original), (
            f"raw_data mutated by rot={rot} flip={flip} dn={dn}"
        )
        assert viewer.raw_data.shape == original.shape
    finally:
        reader.close()


def test_axis_remapping_never_touches_raw_data(qapp, osiris_cube):
    """Choosing different display axes reorders transposed_data, never the source."""
    path, original = osiris_cube
    reader, viewer = _load_into_viewer(path)
    try:
        for x_axis, y_axis in [("AXIS 1", "AXIS 2"), ("AXIS 2", "AXIS 3"),
                               ("AXIS 3", "AXIS 1"), ("AXIS 3", "AXIS 2")]:
            viewer.combo_x.setCurrentText(x_axis)
            viewer.combo_y.setCurrentText(y_axis)
            viewer.apply_axis_mapping()
            assert np.array_equal(viewer.raw_data, original), (
                f"raw_data mutated by axis mapping x={x_axis} y={y_axis}"
            )
    finally:
        reader.close()


def test_dn_multiplier_is_not_folded_into_raw_data(qapp, osiris_cube):
    """display_data carries the multiplier; raw_data must not."""
    path, original = osiris_cube
    reader, viewer = _load_into_viewer(path)
    try:
        viewer.disp_as_dn = True
        viewer.refresh_display()

        assert viewer.data_multiplier != 1.0, "fixture should have ITIME*COADDS != 1"
        assert np.array_equal(viewer.raw_data, original)
        assert not np.array_equal(viewer.display_data, viewer.transposed_data), (
            "display_data should differ once the multiplier is applied"
        )
    finally:
        reader.close()


# ------------------------------------------------------------- save round trip


@pytest.mark.parametrize(
    "rot,flip,x_axis,y_axis",
    [(0, False, "AXIS 3", "AXIS 2"),
     (90, False, "AXIS 1", "AXIS 2"),
     (180, True, "AXIS 2", "AXIS 3"),
     (270, True, "AXIS 3", "AXIS 1")],
)
def test_save_round_trip_is_bit_identical_under_display_transforms(
    qapp, osiris_cube, tmp_path, rot, flip, x_axis, y_axis
):
    """load -> transform for display -> save must return the original bytes and WCS.

    This is the guard the whole data-state rule exists for. If a future change makes a
    write path read transposed_data or display_data, the saved cube comes back with
    swapped physical axes or a stale WCS and this fails.
    """
    path, original = osiris_cube
    reader = FitsReader(path)
    original_wcs = _wcs_cards(reader.get_header())
    try:
        viewer = ImageViewer()
        viewer.set_data(reader.get_data(), reader.get_header())

        # Everything a user might do to the display before saving.
        viewer.combo_x.setCurrentText(x_axis)
        viewer.combo_y.setCurrentText(y_axis)
        viewer.rot_angle = rot
        viewer.flip = flip
        viewer.disp_as_dn = True
        viewer.apply_axis_mapping()

        out = str(tmp_path / "saved.fits")
        reader.save(out)
    finally:
        reader.close()

    check = FitsReader(out)
    try:
        saved = check.get_data()
        assert saved.shape == original.shape, (
            f"axes were reordered on save: {saved.shape} != {original.shape}"
        )
        assert np.array_equal(saved, original), "pixel values changed on save"
        assert _wcs_cards(check.get_header()) == original_wcs, "WCS drifted on save"
    finally:
        check.close()


def test_header_edit_saves_without_disturbing_the_data(qapp, osiris_cube, tmp_path):
    """The Header Editor path: a card changes, the cube does not."""
    path, original = osiris_cube
    reader = FitsReader(path)
    original_wcs = _wcs_cards(reader.get_header())
    try:
        viewer = ImageViewer()
        viewer.set_data(reader.get_data(), reader.get_header())
        viewer.rot_angle = 90
        viewer.flip = True
        viewer.apply_axis_mapping()

        reader.update_header_card("OBSERVER", "Nobody", comment="added by test")
        out = str(tmp_path / "edited.fits")
        reader.save(out)
    finally:
        reader.close()

    check = FitsReader(out)
    try:
        assert np.array_equal(check.get_data(), original)
        assert check.get_header()["OBSERVER"] == "Nobody"
        assert _wcs_cards(check.get_header()) == original_wcs
    finally:
        check.close()


def test_save_in_place_is_bit_identical(qapp, osiris_cube):
    """Saving over the open file is the risky path: there is no second copy to fall
    back on if the wrong array reaches disk."""
    path, original = osiris_cube
    reader = FitsReader(path)
    original_wcs = _wcs_cards(reader.get_header())
    try:
        viewer = ImageViewer()
        viewer.set_data(reader.get_data(), reader.get_header())
        viewer.rot_angle = 270
        viewer.flip = True
        viewer.disp_as_dn = True
        viewer.apply_axis_mapping()
        reader.save()
    finally:
        reader.close()

    check = FitsReader(path)
    try:
        assert np.array_equal(check.get_data(), original)
        assert _wcs_cards(check.get_header()) == original_wcs
    finally:
        check.close()


# --------------------------------------------------- the guard actually guards


def test_the_round_trip_test_would_catch_a_transposed_write(qapp, osiris_cube, tmp_path):
    """Negative control.

    A test that always passes is worse than no test. This writes the viewer's
    transposed_data instead of raw_data — the exact mistake the rule forbids — and
    asserts the round-trip comparison rejects it.
    """
    path, original = osiris_cube
    reader, viewer = _load_into_viewer(path)
    try:
        viewer.apply_axis_mapping()
        wrong = viewer.transposed_data
        out = str(tmp_path / "wrong.fits")
        fits.PrimaryHDU(data=wrong, header=reader.get_header()).writeto(out)
    finally:
        reader.close()

    check = FitsReader(out)
    try:
        saved = check.get_data()
        assert saved.shape != original.shape or not np.array_equal(saved, original), (
            "the round-trip assertions cannot distinguish a transposed write"
        )
    finally:
        check.close()


# ----------------------------------------------- the other two export paths


def test_save_file_as_writes_raw_orientation(qapp, osiris_cube, tmp_path, monkeypatch):
    """File -> Save As is a separate write path from FitsReader.save().

    It builds its own PrimaryHDU, so it could independently regress to reading the
    viewer's arrays. Drive it with the display fully transformed.
    """
    from PySide6.QtWidgets import QFileDialog, QMessageBox
    from pyql3.gui.main_window import MainWindow

    path, original = osiris_cube
    out = str(tmp_path / "saved_as.fits")
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (out, "")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))
    monkeypatch.setattr(QMessageBox, "critical", staticmethod(lambda *a, **k: None))

    win = MainWindow()
    try:
        win.load_fits(path)
        win.image_viewer.rot_angle = 90
        win.image_viewer.flip = True
        win.image_viewer.disp_as_dn = True
        win.image_viewer.apply_axis_mapping()

        win.save_file_as()
    finally:
        win.close()

    check = FitsReader(out)
    try:
        assert check.get_data().shape == original.shape
        assert np.array_equal(check.get_data(), original)
    finally:
        check.close()


def test_arithmetic_result_is_saved_in_raw_orientation(qapp, osiris_cube, tmp_path):
    """Arithmetic produces new data and hands it back through load_from_memory.

    It reads raw_data by design; if it ever read display_data instead, the result
    would carry the flip/rotation and the DN multiplier into a file whose WCS still
    described the original.
    """
    from pyql3.gui.main_window import MainWindow

    path, original = osiris_cube
    win = MainWindow()
    try:
        win.load_fits(path)
        win.image_viewer.rot_angle = 180
        win.image_viewer.flip = True
        win.image_viewer.disp_as_dn = True
        win.image_viewer.apply_axis_mapping()

        # What the Arithmetic tool feeds its operands from.
        operand = win.image_viewer.raw_data.copy()
        assert np.array_equal(operand, original), "arithmetic operand is not raw_data"

        result = operand * 2.0
        header = win.fits_reader.get_header()

        out_win = MainWindow()
        try:
            out_win.load_from_memory(result, header, title="(a * 2)")
            assert np.array_equal(out_win.image_viewer.raw_data, original * 2.0)

            out = str(tmp_path / "arith.fits")
            out_win.fits_reader.save(out)
        finally:
            out_win.close()
    finally:
        win.close()

    check = FitsReader(out)
    try:
        assert check.get_data().shape == original.shape
        assert np.array_equal(check.get_data(), original * 2.0)
    finally:
        check.close()

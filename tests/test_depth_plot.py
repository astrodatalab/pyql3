import pytest
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog
from pyql3.core import coords
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog, latex_to_html


def test_depth_plot_region_extraction(loaded_viewer):
    """The independent-region background: still available, no longer the default."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    dialog.combo_calc.setCurrentText("Median")
    dialog.update_plot()
    assert dialog.plot_widget is not None, "Plot widget None in DepthPlotDialog"

    # Enable background subtraction over a free-standing region rather than an annulus.
    dialog.chk_enable_bg.setChecked(True)
    dialog.combo_bg_mode.setCurrentText("Region")
    dialog.combo_bg_calc.setCurrentText("Average")
    dialog.update_plot()
    assert dialog.bg_roi is not None, "Background ROI was not initialized"
    assert dialog.ring_inner is None, "the annulus was left on the image beside the region"
    dialog.close()


def test_depth_plot_background_toggle_without_initial_center(loaded_viewer):
    """The background checkbox must work when the dialog is opened from the Plot menu.

    Regression: the chk_enable_bg / combo_bg_calc connections lived at the end of
    set_center(), which is only reached when an initial_center is supplied, so
    ticking the box did nothing on the Plot -> Depth Plot path.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)   # no initial_center
    assert dialog.bg_roi is None, "Background ROI should not exist before enabling"
    dialog.combo_bg_mode.setCurrentText("Region")

    dialog.chk_enable_bg.setChecked(True)   # signal path only, no manual call
    assert dialog.bg_roi is not None, "Ticking the checkbox did not create the background ROI"
    assert dialog.spin_bg_x0.isEnabled(), "Background spinboxes were not enabled"
    assert dialog.combo_bg_calc.isEnabled(), "Background calc combo was not enabled"

    bg_x, _ = dialog.plot_bg.getData()
    sub_x, _ = dialog.plot_sub.getData()
    assert bg_x is not None and len(bg_x) > 0, "Background spectrum was not plotted"
    assert sub_x is not None and len(sub_x) > 0, "Subtracted spectrum was not plotted"

    dialog.chk_enable_bg.setChecked(False)
    assert dialog.bg_roi is None, "Unticking the checkbox did not remove the background ROI"
    dialog.close()


def test_depth_plot_background_handler_not_duplicated(loaded_viewer):
    """set_center() must not re-connect the background handlers on every call."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    dialog.set_center((22, 22))
    dialog.set_center((18, 18))

    calls = []
    original = dialog.toggle_background

    def counting(*args, **kwargs):
        calls.append(1)
        return original(*args, **kwargs)

    dialog.toggle_background = counting
    dialog.chk_enable_bg.setChecked(True)
    assert len(calls) == 1, f"toggle_background ran {len(calls)}x for a single click (duplicate connections)"
    dialog.close()


def test_depth_plot_set_center_moves_roi(loaded_viewer):
    """set_center() must centre the ROI on the requested pixel."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    dialog.set_center((15, 25))

    pos, size = dialog.roi.pos(), dialog.roi.size()
    center = (pos.x() + size.x() / 2.0, pos.y() + size.y() / 2.0)
    # (15, 25) is a numpy index; the ImageItem draws that pixel's centre half a pixel in.
    assert center == pytest.approx((15.5, 25.5)), f"ROI centre is {center}, expected (15.5, 25.5)"
    dialog.close()


def test_a_circle_drawn_on_a_pixel_measures_that_pixel(qapp):
    """The measured circle is the drawn circle (BUGS.md R2).

    An ImageItem draws pixel i over [i, i+1), so a circle drawn on pixel (12, 9) has its
    centre at the item coordinate (12.5, 9.5). The Total of an r = 1 circle there is the
    whole of that pixel, not the 78.54 an aperture half a pixel away gives.
    """
    data = np.zeros((4, 21, 31))      # numpy (z, y, x), no header
    data[:, 9, 12] = 100.0
    viewer = ImageViewer()
    viewer.set_data(data, None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.combo_shape.setCurrentText("Circle")
        dialog.combo_calc.setCurrentText("Total")
        dialog.roi.setPos([12.5 - 1.0, 9.5 - 1.0])
        dialog.roi.setSize([2.0, 2.0])
        _, total = dialog.plot_data.getData()
        assert np.asarray(total) == pytest.approx(100.0, abs=1e-9)
        assert dialog.aperture_geometry() == pytest.approx((12.0, 9.0, 1.0))
        assert (dialog.spin_cx.value(), dialog.spin_cy.value()) == pytest.approx((12.0, 9.0))
    finally:
        dialog.close()


def test_depth_plot_latex_conversion():
    """Test conversion of LaTeX strings like $$H_\\alpha$$ and $$P_{2f}$$ to HTML for line labels."""
    test_cases = [
        ("$$H_\\alpha$$", "H<sub>&alpha;</sub>"),
        ("$$P_{2f}6.5$$", "P<sub>2f</sub>6.5"),
        ("Ti I", "Ti I"),
    ]
    for inp, expected in test_cases:
        res = latex_to_html(inp)
        assert expected in res, f"Expected '{expected}' in latex_to_html('{inp}'), got '{res}'"


def test_depth_plot_linelist_loading_and_overlays(loaded_viewer):
    """Test spectral line list loading, default selection, line item labels, rotation, and dataBounds exclusion."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)

    # Check populated default linelists
    items = [dialog.combo_linelist.itemText(i) for i in range(dialog.combo_linelist.count())]
    assert "nir_stellar_lines.txt" in items, "Default nir_stellar_lines.txt missing from linelist combo"
    assert dialog.combo_linelist.currentText() == "nir_stellar_lines.txt", "nir_stellar_lines.txt is not selected by default"
    assert len(dialog.loaded_lines) > 0, "Failed to parse default line list lines"

    # Turn on line list overlays
    dialog.chk_enable_lines.setChecked(True)
    dialog.update_line_overlays()

    # Verify line items have dataBounds set to (None, None) so they don't break Y-axis scaling
    for line_item, text_item in dialog.line_items:
        db_line = line_item.dataBounds(0)
        db_text = text_item.dataBounds(0)
        assert db_line == (None, None), "InfiniteLine dataBounds must return (None, None)"
        assert db_text == (None, None), "TextItem dataBounds must return (None, None)"

    dialog.close()


def test_depth_plot_auto_y_range(loaded_viewer):
    """Test that Auto Y-range scales around spectral data without being distorted by vertical line overlay labels."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    dialog.chk_enable_lines.setChecked(True)
    dialog.update_line_overlays()

    # Call auto_y_range
    dialog.auto_y_range()

    view_box = dialog.plot_widget.getViewBox()
    y_range = view_box.viewRange()[1]
    assert y_range[1] > y_range[0], "Auto Y-range produced inverted bounds"
    assert abs(y_range[1] - y_range[0]) < 100000.0, f"Auto Y-range produced unscaled infinite bounds: {y_range}"
    dialog.close()


def test_depth_plot_no_spurious_file_dialog(qapp, monkeypatch):
    """Ensure opening DepthPlotDialog does not pop up QFileDialog."""
    file_dialog_opened = False

    def mock_get_open_filename(*args, **kwargs):
        nonlocal file_dialog_opened
        file_dialog_opened = True
        return ("", "")

    monkeypatch.setattr(QFileDialog, "getOpenFileName", mock_get_open_filename)

    viewer = ImageViewer()
    dialog = DepthPlotDialog(image_viewer=viewer)

    assert not file_dialog_opened, "DepthPlotDialog popped up a QFileDialog during initialization!"
    dialog.close()


def test_depth_plot_wavelength_x_axis_and_csv_export(loaded_viewer):
    """Verify that primary X-axis data is set to physical wavelengths for CSV export."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    dialog.update_plot()

    x_data, y_data = dialog.plot_data.getData()
    assert x_data is not None and len(x_data) > 0, "No X data returned from plot_data"
    
    # Check that x_data matches physical wavelengths rather than 0-indexed integers
    # For OSIRIS Kn5 cube, wavelengths are around ~2.2 µm
    assert np.mean(x_data) > 1.0, f"Expected physical wavelength X-axis (> 1.0 µm), got mean: {np.mean(x_data)}"
    
    # Bottom label check
    bottom_label = dialog.plot_widget.getAxis('bottom').labelText
    assert "Wavelength" in bottom_label, f"Expected 'Wavelength' in bottom axis label, got '{bottom_label}'"
    
    # Top axis check
    assert dialog.top_axis.wavelengths is not None, "Top axis wavelengths not set"
    dialog.close()


def test_depth_plot_export_button(loaded_viewer):
    """Verify that Export... button exists on top layout and triggers export dialog."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    assert hasattr(dialog, "btn_export"), "DepthPlotDialog missing btn_export button"
    assert dialog.btn_export.text() == "Export...", "Export button text mismatch"

    dialog.btn_export.click()
    scene = dialog.plot_widget.scene()
    assert scene.exportDialog is not None, "Export dialog was not created on scene"
    assert scene.exportDialog.isVisible(), "Export dialog is not visible after clicking Export... button"
    dialog.close()


# --------------------------------------------------- circular aperture + sky annulus


def test_the_defaults_are_a_circular_aperture_totalled(loaded_viewer):
    """Total over a circle, because that is what an annulus subtraction is defined against."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        assert dialog.combo_shape.currentText() == "Circle"
        assert dialog.combo_calc.currentText() == "Total"
        assert dialog.combo_bg_mode.currentText() == "Annulus"
        assert isinstance(dialog.roi, pg.CircleROI), "the source ROI is not a circle"
        assert dialog.spin_radius.value() == pytest.approx(dialog.aperture_geometry()[2])
    finally:
        dialog.close()


def test_enabling_the_background_draws_an_annulus_not_a_second_region(loaded_viewer):
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)

        assert dialog.ring_inner is not None and dialog.ring_outer is not None
        assert dialog.bg_roi is None, "an independent background region was created too"
        assert dialog.bg_annulus_row.isVisibleTo(dialog), "the sky radii are not shown"
        assert not dialog.bg_region_rows.isVisibleTo(dialog), \
            "the region box spins describe a region that no longer exists"

        bg_x, bg_y = dialog.plot_bg.getData()
        sub_x, _ = dialog.plot_sub.getData()
        assert bg_x is not None and len(bg_x) > 0, "no background spectrum was plotted"
        assert sub_x is not None and len(sub_x) > 0, "no subtracted spectrum was plotted"
    finally:
        dialog.close()


def _ring_center(ring):
    """A ring's centre as a numpy index, the convention `aperture_geometry()` uses."""
    pos, size = ring.pos(), ring.size()
    return (coords.item_to_index(pos.x() + size.x() / 2.0),
            coords.item_to_index(pos.y() + size.y() / 2.0))


def test_the_annulus_follows_the_aperture_it_is_not_independent(loaded_viewer):
    """The whole point of annulus mode: one centre, not two."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)

        for target in [(15, 25), (30, 12), (20, 20)]:
            dialog.set_center(target)
            cx, cy, _ = dialog.aperture_geometry()
            assert (cx, cy) == pytest.approx(target), "the aperture did not move"
            assert _ring_center(dialog.ring_inner) == pytest.approx((cx, cy)), \
                f"inner ring left behind at {target}"
            assert _ring_center(dialog.ring_outer) == pytest.approx((cx, cy)), \
                f"outer ring left behind at {target}"
    finally:
        dialog.close()


def test_dragging_the_aperture_also_moves_the_annulus(loaded_viewer):
    """set_center() is not the only way the ROI moves -- a drag emits sigRegionChanged."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        dialog.roi.setPos([31.0, 7.0])          # as a drag would

        cx, cy, _ = dialog.aperture_geometry()
        assert _ring_center(dialog.ring_inner) == pytest.approx((cx, cy))
        assert _ring_center(dialog.ring_outer) == pytest.approx((cx, cy))
    finally:
        dialog.close()


def test_the_radius_spin_resizes_the_aperture_about_its_centre(loaded_viewer):
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.spin_radius.setValue(7.5)
        cx, cy, r = dialog.aperture_geometry()
        assert r == pytest.approx(7.5)
        assert (cx, cy) == pytest.approx((20.0, 20.0)), "resizing moved the aperture"
    finally:
        dialog.close()


def test_the_sky_radii_size_the_rings(loaded_viewer):
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        dialog.spin_r_in.setValue(6.0)
        dialog.spin_r_out.setValue(11.0)

        assert dialog.ring_inner.size().x() == pytest.approx(12.0)
        assert dialog.ring_outer.size().x() == pytest.approx(22.0)
    finally:
        dialog.close()


def test_an_outer_radius_inside_the_inner_one_is_pushed_out(loaded_viewer):
    """Refusing the edit would leave the box unresponsive; reordering keeps it usable."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        dialog.spin_r_in.setValue(20.0)
        assert dialog.spin_r_out.value() > dialog.spin_r_in.value()
    finally:
        dialog.close()


def test_a_background_total_is_unavailable_for_an_annulus(loaded_viewer):
    """Per-pixel subtraction of a summed annulus would scale with the annulus width."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        index = dialog.combo_bg_calc.findText("Total")
        assert not dialog.combo_bg_calc.model().item(index).isEnabled()
        assert dialog.combo_bg_calc.currentText() == "Median"

        dialog.combo_bg_mode.setCurrentText("Region")
        assert dialog.combo_bg_calc.model().item(index).isEnabled(), \
            "the region mode lost an option it always had"
    finally:
        dialog.close()


def test_switching_to_a_rectangle_gives_up_the_annulus(loaded_viewer):
    """An annulus has nothing to be concentric with once the aperture is a box."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        assert dialog.ring_inner is not None

        dialog.combo_shape.setCurrentText("Rectangle")
        assert dialog.ring_inner is None, "the rings outlived the circle"
        assert dialog.combo_bg_mode.currentText() == "Region"
        assert dialog.bg_roi is not None, "background was silently switched off"

        # The combo must not merely read "Region" -- Annulus has to be unselectable, or a
        # user picks it again and the tool goes on measuring something else.
        index = dialog.combo_bg_mode.findText("Annulus")
        assert not dialog.combo_bg_mode.model().item(index).isEnabled()

        dialog.combo_shape.setCurrentText("Circle")
        assert dialog.combo_bg_mode.model().item(index).isEnabled()
        assert dialog.combo_bg_mode.currentText() == "Annulus", \
            "the mode this took away was not given back"
        assert dialog.ring_inner is not None
    finally:
        dialog.close()


def test_a_region_the_user_chose_survives_a_trip_through_rectangle(loaded_viewer):
    """Restoring Annulus is undoing our own override, not overriding the user's choice."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        dialog.combo_bg_mode.setCurrentText("Region")      # deliberate

        dialog.combo_shape.setCurrentText("Rectangle")
        dialog.combo_shape.setCurrentText("Circle")

        assert dialog.combo_bg_mode.currentText() == "Region", \
            "a deliberately chosen Region was overwritten"
    finally:
        dialog.close()


def test_the_rings_are_taken_off_the_image_when_the_dialog_closes(loaded_viewer):
    """`BUGS.md` B7: a parented item detached the wrong way stays painted."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    dialog.chk_enable_bg.setChecked(True)
    rings = [dialog.ring_inner, dialog.ring_outer]
    dialog.close()

    scene_items = loaded_viewer.imv.getView().scene().items()
    for ring in rings:
        assert ring not in scene_items, "a sky ring was left on the image"
    assert dialog.ring_inner is None and dialog.ring_outer is None


def test_the_spectrum_does_not_depend_on_how_the_view_is_oriented(loaded_viewer):
    """A circular aperture is invariant under flips and 90 degree steps; assert it.

    This is the class of error `BUGS.md` B13/B14/B20 records three times over -- a
    transform applied in the wrong order or with one axis length used for both.
    """
    reference = None
    for flip in (False, True):
        for rot in (0, 90, 180, 270):
            loaded_viewer.flip, loaded_viewer.rot_angle = flip, rot
            loaded_viewer.refresh_display()

            dialog = DepthPlotDialog(image_viewer=loaded_viewer)
            try:
                ox, oy = 12.0, 17.0
                dx, dy = loaded_viewer.orig_to_display(ox, oy)
                dialog.set_center((dx, dy))
                dialog.spin_radius.setValue(4.0)
                dialog.chk_enable_bg.setChecked(True)
                _, y = dialog.plot_sub.getData()
                assert y is not None and len(y), f"no spectrum at flip={flip} rot={rot}"
                if reference is None:
                    reference = np.asarray(y)
                else:
                    assert np.asarray(y) == pytest.approx(reference, rel=1e-6, abs=1e-9), \
                        f"spectrum changed under flip={flip} rot={rot}"
            finally:
                dialog.close()

    loaded_viewer.flip, loaded_viewer.rot_angle = False, 0
    loaded_viewer.refresh_display()


def test_an_aperture_off_the_cube_says_so_instead_of_drawing_a_line(loaded_viewer):
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        dialog.set_center((5000, 5000))

        x, _ = dialog.plot_data.getData()
        assert x is None or len(x) == 0, "an unmeasurable aperture still drew a spectrum"
        assert "overlap" in dialog.lbl_bg_info.text(), dialog.lbl_bg_info.text()
    finally:
        dialog.close()


def test_the_sky_annulus_defaults_just_outside_the_aperture(loaded_viewer):
    """Inner = aperture + 1, outer = inner + 2."""
    from pyql3.gui.tools import depth_plot as dp_mod

    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        r_ap = dialog.spin_radius.value()
        assert dialog.spin_r_in.value() == pytest.approx(r_ap + dp_mod.SKY_INNER_GAP)
        assert dialog.spin_r_out.value() == pytest.approx(
            dialog.spin_r_in.value() + dp_mod.SKY_WIDTH)
    finally:
        dialog.close()


def test_the_annulus_keeps_its_offset_when_the_aperture_grows(loaded_viewer):
    """Fixed radii would end up measuring sky from inside an enlarged aperture."""
    from pyql3.gui.tools import depth_plot as dp_mod

    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        for r_ap in (6.0, 9.5, 2.0):
            dialog.spin_radius.setValue(r_ap)
            assert dialog.spin_r_in.value() == pytest.approx(r_ap + dp_mod.SKY_INNER_GAP), \
                f"the annulus did not follow an aperture of {r_ap}"
            assert dialog.spin_r_out.value() == pytest.approx(
                r_ap + dp_mod.SKY_INNER_GAP + dp_mod.SKY_WIDTH)
            assert dialog.spin_r_in.value() > r_ap, "sky is being measured inside the source"
    finally:
        dialog.close()


def test_radii_the_user_typed_are_not_overwritten(loaded_viewer):
    """Following the aperture is a default, not a policy."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        dialog.spin_r_in.setValue(12.0)
        dialog.spin_r_out.setValue(18.0)

        dialog.spin_radius.setValue(5.0)
        assert dialog.spin_r_in.value() == pytest.approx(12.0), "overwrote a chosen radius"
        assert dialog.spin_r_out.value() == pytest.approx(18.0)

        dialog.set_center((25, 25))
        assert dialog.spin_r_in.value() == pytest.approx(12.0), "a move reset the radii"
    finally:
        dialog.close()


# ------------------------------------------------------------------ control grouping


def test_the_geometry_row_matches_the_shape(loaded_viewer):
    """A circle is a centre and a radius; only a rectangle gets corner spins."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        assert dialog.geom_circle.isVisibleTo(dialog)
        assert not dialog.geom_rect.isVisibleTo(dialog), \
            "a circle was described by its bounding box as well"

        dialog.combo_shape.setCurrentText("Rectangle")
        assert dialog.geom_rect.isVisibleTo(dialog)
        assert not dialog.geom_circle.isVisibleTo(dialog), \
            "a rectangle was offered a radius"
    finally:
        dialog.close()


def test_the_centre_spins_track_and_drive_the_aperture(loaded_viewer):
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        assert (dialog.spin_cx.value(), dialog.spin_cy.value()) == pytest.approx((20.0, 20.0))

        dialog.set_center((28, 11))
        assert (dialog.spin_cx.value(), dialog.spin_cy.value()) == pytest.approx((28.0, 11.0))

        dialog.spin_cx.setValue(33.0)
        cx, cy, _ = dialog.aperture_geometry()
        assert (cx, cy) == pytest.approx((33.0, 11.0)), "typing a centre did not move it"
    finally:
        dialog.close()


def test_inapplicable_background_rows_are_hidden_not_greyed(loaded_viewer):
    """A disabled spin box reading 0 still reads as a measurement."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        assert not dialog.bg_annulus_row.isVisibleTo(dialog), \
            "sky radii shown with background subtraction switched off"
        assert not dialog.bg_region_rows.isVisibleTo(dialog)

        dialog.chk_enable_bg.setChecked(True)
        assert dialog.bg_annulus_row.isVisibleTo(dialog)
        assert not dialog.bg_region_rows.isVisibleTo(dialog)

        dialog.combo_bg_mode.setCurrentText("Region")
        assert dialog.bg_region_rows.isVisibleTo(dialog)
        assert not dialog.bg_annulus_row.isVisibleTo(dialog)
    finally:
        dialog.close()


def test_the_aperture_is_described_in_one_place(loaded_viewer):
    """Shape, combine and radius moved out of the toolbar into the extraction group."""
    from PySide6.QtWidgets import QGroupBox

    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        for widget in (dialog.combo_shape, dialog.combo_calc, dialog.spin_radius):
            box = widget
            while box is not None and not isinstance(box, QGroupBox):
                box = box.parentWidget()
            assert box is not None and "APERTURE" in box.title(), \
                f"{widget} is not in the extraction group"
    finally:
        dialog.close()


def _legend_names(dialog):
    """The series the plot legend currently claims are drawn."""
    return [label.text for _, label in dialog.plot_legend.items]


def _cut_viewer(tmp_path, spike_at):
    """A viewer on a cube of ones with one bright pixel at display `spike_at` (x, y).

    The cube is written in OSIRIS order (WAVE, DEC, RA) and every axis is the same
    length, so the default axis mapping cannot make one of them special. `data[x, y, :]`
    is the column that lands at display `(x, y)` in every plane -- asserted below, since
    an off-by-one axis here would place the spike outside the aperture and the test would
    pass for the wrong reason.
    """
    from astropy.io import fits
    from astropy.wcs import WCS
    from pyql3.core.fits_reader import FitsReader

    data = np.ones((40, 40, 40), dtype=np.float32)
    data[spike_at[0], spike_at[1], :] = 1000.0

    w = WCS(naxis=3)
    w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
    w.wcs.crval = [2.2, 34.0, -118.0]
    w.wcs.cdelt = [0.0005, 0.0001, 0.0001]
    w.wcs.crpix = [1, 20, 20]
    w.wcs.cunit = ['um', 'deg', 'deg']
    header = w.to_header()
    header['ITIME'] = 1.0
    header['BUNIT'] = 'DN/s'

    path = tmp_path / "cut_cube.fits"
    fits.PrimaryHDU(data=data, header=header).writeto(path, overwrite=True)
    reader = FitsReader(str(path))
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)
    assert viewer.current_plane()[spike_at] == 1000.0, \
        "the bright pixel is not at the display position this test assumes"
    return viewer


def test_a_cut_does_not_inherit_the_depth_plot_aperture_readout(loaded_viewer):
    """Switching to a cut must not leave the previous mode's measurement on screen.

    The cut branches never touched `lbl_bg_info`, so `Aperture 28.3 px^2` -- computed for a
    circular extraction that a cut does not perform -- stayed under the plot describing
    numbers it had nothing to do with.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        assert "Aperture" in dialog.lbl_bg_info.text(), "no readout to go stale"

        dialog.combo_type.setCurrentText("Horizontal Cut")
        assert "Aperture" not in dialog.lbl_bg_info.text(), \
            "the depth plot's aperture area survived into a cut"
    finally:
        dialog.close()


def test_a_cut_says_why_the_background_and_line_groups_are_inert(loaded_viewer):
    """The reason a group is disabled must not be greyed out along with it.

    `lbl_line_info` lives inside SPECTRAL LINE LIST, so the one sentence explaining the
    mode was dimmed with everything it explained. The status line under both groups is
    outside them and stays readable.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.combo_type.setCurrentText("Vertical Cut")
        assert "Depth Plot" in dialog.lbl_bg_info.text(), \
            "nothing readable explains the disabled groups"
    finally:
        dialog.close()


def test_a_horizontal_cut_honours_a_circular_aperture(tmp_path):
    """`Shape: Circle` must mean a circle in every plot type.

    The cut branches sliced the ROI's bounding box, so a circular aperture silently
    measured its corners -- the control stated one thing and the arithmetic did another.
    """
    viewer = _cut_viewer(tmp_path, spike_at=(17, 17))   # a corner of the 17..23 box
    dialog = DepthPlotDialog(image_viewer=viewer, initial_center=(20, 20))
    try:
        assert (int(dialog.roi.pos().x()), int(dialog.roi.pos().y())) == (17, 17), \
            "the aperture is not where this test placed the spike"

        dialog.combo_shape.setCurrentText("Circle")
        dialog.combo_calc.setCurrentText("Total")
        dialog.combo_type.setCurrentText("Horizontal Cut")

        _, cut = dialog.plot_data.getData()
        assert cut is not None and len(cut) > 0, "the cut drew nothing"
        assert cut[0] < 100.0, \
            f"the corner pixel outside the circle was included: {cut[0]}"
    finally:
        dialog.close()
        viewer.close()


def test_a_vertical_cut_honours_a_circular_aperture(tmp_path):
    viewer = _cut_viewer(tmp_path, spike_at=(17, 17))
    dialog = DepthPlotDialog(image_viewer=viewer, initial_center=(20, 20))
    try:
        dialog.combo_shape.setCurrentText("Circle")
        dialog.combo_calc.setCurrentText("Total")
        dialog.combo_type.setCurrentText("Vertical Cut")

        _, cut = dialog.plot_data.getData()
        assert cut is not None and len(cut) > 0, "the cut drew nothing"
        assert cut[0] < 100.0, \
            f"the corner pixel outside the circle was included: {cut[0]}"
    finally:
        dialog.close()
        viewer.close()


def test_a_rectangular_cut_still_measures_the_whole_box(tmp_path):
    """The mask follows the shape control; it is not applied unconditionally."""
    viewer = _cut_viewer(tmp_path, spike_at=(17, 17))
    dialog = DepthPlotDialog(image_viewer=viewer, initial_center=(20, 20))
    try:
        dialog.combo_shape.setCurrentText("Rectangle")
        dialog.combo_calc.setCurrentText("Total")
        dialog.combo_type.setCurrentText("Horizontal Cut")

        _, cut = dialog.plot_data.getData()
        assert cut[0] > 100.0, \
            f"a rectangle dropped a pixel inside it: {cut[0]}"
    finally:
        dialog.close()
        viewer.close()


def test_the_legend_lists_only_the_curves_that_are_drawn(loaded_viewer):
    """Three entries with two of them empty describes a plot that is not on screen."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        assert _legend_names(dialog) == ["Source"], \
            "Background and Subtracted are listed before anything is subtracted"

        dialog.chk_enable_bg.setChecked(True)
        assert _legend_names(dialog) == ["Source", "Background", "Subtracted"]

        dialog.combo_type.setCurrentText("Horizontal Cut")
        assert _legend_names(dialog) == ["Source"], \
            "a cut draws one curve and must say so"
    finally:
        dialog.close()


def test_the_legend_is_anchored_away_from_the_start_of_the_spectrum(loaded_viewer):
    """At the top left the box covers the first channels of every curve."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        assert dialog.plot_legend.offset[0] < 0, \
            "the legend still sits over the leading edge of the data"
    finally:
        dialog.close()


def test_every_spectrum_curve_is_drawn_solid(loaded_viewer):
    """Dashes read as gaps in the spectrum, so the curves separate by colour alone."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        for curve in (dialog.plot_data, dialog.plot_bg, dialog.plot_sub):
            assert curve.opts['pen'].style() == Qt.SolidLine
    finally:
        dialog.close()


def test_a_vertical_cut_does_not_keep_the_previous_background_curve(loaded_viewer):
    """The vertical branch cleared `plot_sub` but not `plot_bg`.

    A background measured for a depth plot stayed drawn over a cut it had no part in.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        assert len(dialog.plot_bg.getData()[0]) > 0, "no background curve to leave behind"

        dialog.combo_type.setCurrentText("Vertical Cut")
        x_bg, _ = dialog.plot_bg.getData()
        assert x_bg is None or len(x_bg) == 0, \
            "the depth plot's background curve survived into a cut"
    finally:
        dialog.close()


def _label_positions(dialog):
    return [text.pos().y() for _, text in dialog.line_items]


def test_line_labels_are_placed_along_the_bottom(loaded_viewer):
    """A spectrum has its headroom below the trace, not above it.

    Moved to a gutter at the top these were harder to read and ran through the spectrum,
    which sits near the top of the view whenever autoranging has to fit a subtracted curve
    near zero as well. The floor is the room that is actually free.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_lines.setChecked(True)
        dialog.update_line_overlays()
        assert dialog.line_items, "no line labels were drawn"

        y_min, y_max = dialog.plot_widget.getViewBox().viewRange()[1]
        middle = (y_min + y_max) / 2.0
        above = [y for y in _label_positions(dialog) if y >= middle]
        assert not above, f"{len(above)} labels are anchored in the upper half of the view"
    finally:
        dialog.close()


def test_crowded_line_labels_still_stagger(loaded_viewer):
    """The gutter must not cost the stagger that keeps neighbouring names apart."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_lines.setChecked(True)
        # Four lines far closer together than the stagger threshold, inside the view.
        # Taken from the drawn curve, not `viewRange()`: with no window on screen the view
        # is still (0, 1) and `update_line_overlays` falls back to the data extent itself.
        x_data, _ = dialog.plot_data.getData()
        x_min, x_max = float(x_data[0]), float(x_data[-1])
        mid = (x_min + x_max) / 2.0
        step = (x_max - x_min) * 1e-4
        dialog.loaded_lines = [(mid + i * step, f"X {i}") for i in range(4)]
        dialog.update_line_overlays()

        rows = _label_positions(dialog)
        assert len(rows) == 4, "the crowded lines were not all drawn"
        assert len(set(rows)) == 4, f"crowded names landed on the same row: {rows}"
    finally:
        dialog.close()


def test_line_labels_rise_from_their_row(loaded_viewer):
    """Rotated text extends from its anchor, and from the floor it must extend upwards."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_lines.setChecked(True)
        dialog.update_line_overlays()
        for _, text in dialog.line_items:
            assert text.anchor.x() == 0.0 and text.anchor.y() == 0.5, \
                f"label anchor {text.anchor} does not stand the text up from its row"
    finally:
        dialog.close()


def test_line_lists_are_offered_by_filename(loaded_viewer):
    """A line list is data, and the name on screen has to be the file it came from.

    Titles like "NIR Stellar Lines" read better and cost more than they are worth: they
    break the association with the file on disk that the numbers can be checked against.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    try:
        items = [dialog.combo_linelist.itemText(i)
                 for i in range(dialog.combo_linelist.count())]
        assert "nir_stellar_lines.txt" in items, f"line lists are not named by file: {items}"
        assert "arcturus_molecular_lines.txt" in items
        assert dialog.combo_linelist.currentText() == "nir_stellar_lines.txt"
    finally:
        dialog.close()


def test_a_line_list_entry_carries_its_full_path(loaded_viewer):
    """The filename says which file; the tooltip says which copy of it.

    Several data directories can supply a list (a frozen bundle, the repo, a browsed file),
    and the names collide. The path is carried alongside without changing what is displayed.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer)
    try:
        idx = dialog.combo_linelist.findText("nir_stellar_lines.txt")
        assert idx >= 0
        assert dialog.combo_linelist.itemData(idx).endswith("nir_stellar_lines.txt"), \
            "the file behind a list entry is not recoverable"
    finally:
        dialog.close()


def _drag(dialog, roi, positions):
    """Emulate a mouse drag of `roi`, the way pyqtgraph's drag handler does it."""
    roi.sigRegionChangeStarted.emit(roi)
    for pos in positions:
        roi.setPos(list(pos), finish=False)
    roi.sigRegionChangeFinished.emit(roi)


def test_dragging_the_aperture_holds_the_y_range_until_release(loaded_viewer):
    """The AxisItem caches its rendering in a QPicture that a Y-range change throws away,
    so re-ranging on every mouse-move event made a drag cost 39.6% more than it needed to.

    `viewRange()` only reflects a pending auto-range after `prepareForPaint()` runs -- that
    is the only place `ViewBox.updateAutoRange()` is called from, and nothing paints
    offscreen with no display. Without it, `vb.viewRange()[1]` never moves from its initial
    `[0, 1]` regardless of whether the freeze is wired at all, and the mid-drag assertion
    below is comparing `[0, 1]` to itself.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        vb = dialog.plot_widget.getViewBox()
        assert vb.autoRangeEnabled()[1], "the Y axis should auto-range before any drag"

        dialog.roi.sigRegionChangeStarted.emit(dialog.roi)
        vb.prepareForPaint()
        frozen = list(vb.viewRange()[1])

        for pos in ((5, 5), (12, 18), (25, 9)):
            dialog.roi.setPos(list(pos), finish=False)
            vb.prepareForPaint()
            assert not vb.autoRangeEnabled()[1], "the Y axis is still auto-ranging mid-drag"
            assert list(vb.viewRange()[1]) == frozen, \
                f"the Y range moved to {vb.viewRange()[1]} mid-drag"

        dialog.roi.sigRegionChangeFinished.emit(dialog.roi)
        assert vb.autoRangeEnabled()[1], "releasing the drag must restore auto-range"
    finally:
        dialog.close()


def test_a_fixed_y_range_is_not_re_enabled_by_a_drag(loaded_viewer):
    """`Fix Y` means fixed. The thaw restores what auto-range was, not what it might be."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.plot_widget.setYRange(0.0, 5.0, padding=0)
        dialog.chk_fix_y.setChecked(True)
        vb = dialog.plot_widget.getViewBox()

        _drag(dialog, dialog.roi, [(5, 5), (12, 18)])

        assert not vb.autoRangeEnabled()[1], "a drag re-enabled auto-range over 'Fix Y'"
        assert list(vb.viewRange()[1]) == [0.0, 5.0]
    finally:
        dialog.close()


def test_a_manual_y_zoom_survives_a_drag(loaded_viewer):
    """Zooming the Y axis with the wheel disables auto-range without ticking `Fix Y`, so
    restoring from the checkbox rather than the view would discard the user's zoom.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        vb = dialog.plot_widget.getViewBox()
        vb.setYRange(1.0, 9.0, padding=0)          # as a wheel zoom does
        assert not vb.autoRangeEnabled()[1]
        assert not dialog.chk_fix_y.isChecked(), "this test is about the two disagreeing"

        _drag(dialog, dialog.roi, [(5, 5), (12, 18)])

        assert not vb.autoRangeEnabled()[1], "the drag threw away a manual Y zoom"
        assert list(vb.viewRange()[1]) == [1.0, 9.0]
    finally:
        dialog.close()


def test_dragging_the_background_region_also_holds_the_y_range(loaded_viewer):
    """The background region is dragged exactly as the aperture is, and costs the same."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.chk_enable_bg.setChecked(True)
        dialog.combo_bg_mode.setCurrentText("Region")
        assert dialog.bg_roi is not None

        vb = dialog.plot_widget.getViewBox()
        dialog.bg_roi.sigRegionChangeStarted.emit(dialog.bg_roi)
        vb.prepareForPaint()
        frozen = list(vb.viewRange()[1])

        for pos in ((2, 2), (8, 14)):
            dialog.bg_roi.setPos(list(pos), finish=False)
            vb.prepareForPaint()
            assert not vb.autoRangeEnabled()[1], "the Y axis is still auto-ranging mid-drag"
            assert list(vb.viewRange()[1]) == frozen

        dialog.bg_roi.sigRegionChangeFinished.emit(dialog.bg_roi)
        assert vb.autoRangeEnabled()[1]
    finally:
        dialog.close()


def test_an_interrupted_drag_does_not_leave_the_y_axis_frozen(loaded_viewer):
    """Switching shape destroys the ROI mid-drag, so `sigRegionChangeFinished` never
    arrives. Without a thaw on that path the Y axis stays frozen for the life of the
    dialog, which reads as a bug rather than as a fast drag.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        vb = dialog.plot_widget.getViewBox()
        dialog.roi.sigRegionChangeStarted.emit(dialog.roi)
        assert not vb.autoRangeEnabled()[1], "precondition: the drag froze the axis"

        dialog.combo_shape.setCurrentText("Rectangle")   # fires toggle_roi_shape()

        assert vb.autoRangeEnabled()[1], "the Y axis is still frozen after the ROI vanished"
        assert dialog._y_autorange_before_drag is None
    finally:
        dialog.close()


def test_disabling_the_background_mid_drag_does_not_leave_the_y_axis_frozen(loaded_viewer):
    """`remove_bg_roi()` destroys the background ROI, which is also reached by unticking
    Enable Background Subtraction (via `toggle_background()`) and by switching Mode back to
    Annulus (via `on_bg_mode_changed()`) -- neither of which is `toggle_roi_shape()` or
    `closeEvent()`, the two paths that already thaw. Without a thaw at the top of
    `remove_bg_roi()` itself, a drag interrupted this way leaves the axis frozen for the
    rest of the dialog's life.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.combo_bg_mode.setCurrentText("Region")
        dialog.chk_enable_bg.setChecked(True)
        assert dialog.bg_roi is not None

        vb = dialog.plot_widget.getViewBox()
        dialog.bg_roi.sigRegionChangeStarted.emit(dialog.bg_roi)
        assert not vb.autoRangeEnabled()[1], "precondition: the drag froze the axis"

        dialog.chk_enable_bg.setChecked(False)   # fires toggle_background() -> remove_bg_roi()

        assert vb.autoRangeEnabled()[1], "the Y axis is still frozen after the bg ROI vanished"
        assert dialog._y_autorange_before_drag is None
    finally:
        dialog.close()


# ------------------------------------------- box edges and boxes off the image (BUGS.md R19)

@pytest.fixture
def x_cube_dialog(qapp):
    """A rectangular-aperture Depth Plot over a cube whose value is the display x column."""
    data = np.broadcast_to(np.arange(30.0)[None, None, :], (3, 20, 30)).copy()
    viewer = ImageViewer()
    viewer.set_data(data, None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    dialog.combo_shape.setCurrentText("Rectangle")
    dialog.combo_calc.setCurrentText("Average")
    yield dialog
    dialog.close()


def _first_spectrum_value(dialog):
    return dialog.plot_data.getData()[1][0]


def test_a_box_half_off_the_image_measures_only_what_it_covers(x_cube_dialog):
    d = x_cube_dialog
    d.roi.setPos([-5, 2])
    d.roi.setSize([10, 4])
    # Columns 0-4 are on the image: mean 2.0, not the 4.5 of columns 0-9.
    assert _first_spectrum_value(d) == pytest.approx(2.0)
    assert "overlap" not in d.lbl_bg_info.text()

    # A box on the high edge is likewise cut to the columns that exist (25-29).
    d.roi.setPos([25, 2])
    assert _first_spectrum_value(d) == pytest.approx(27.0)

    # A box on pixel edges still reads exactly those pixels.
    d.roi.setPos([10, 2])
    assert _first_spectrum_value(d) == pytest.approx(14.5)


@pytest.mark.parametrize("x0", [-50, 100])
def test_a_box_wholly_off_the_image_is_reported_not_measured(x_cube_dialog, x0):
    d = x_cube_dialog
    d.roi.setPos([10, 2])
    d.roi.setSize([10, 4])
    assert len(d.plot_data.getData()[0]) > 0
    d.roi.setPos([x0, 2])
    xs, _ = d.plot_data.getData()
    assert xs is None or len(xs) == 0, "the edge column was plotted for a box that misses"
    assert "overlap" in d.lbl_bg_info.text()
    # Moving back onto the image replaces the message with the measurement.
    d.roi.setPos([10, 2])
    assert len(d.plot_data.getData()[0]) > 0
    assert "overlap" not in d.lbl_bg_info.text()


def test_a_cut_over_a_box_half_off_the_image_covers_only_the_image(x_cube_dialog):
    d = x_cube_dialog
    d.combo_type.setCurrentText("Horizontal Cut")
    d.roi.setPos([-5, 2])
    d.roi.setSize([10, 4])
    assert d.plot_data.getData()[0].tolist() == [0, 1, 2, 3, 4]

    d.combo_type.setCurrentText("Vertical Cut")
    d.roi.setPos([4, -3])
    d.roi.setSize([4, 8])
    assert d.plot_data.getData()[0].tolist() == [0, 1, 2, 3, 4]

    d.roi.setPos([4, -30])
    xs, _ = d.plot_data.getData()
    assert xs is None or len(xs) == 0
    assert "overlap" in d.lbl_bg_info.text()


def test_a_background_box_half_off_the_image_measures_only_what_it_covers(x_cube_dialog):
    d = x_cube_dialog
    d.roi.setPos([10, 2])
    d.roi.setSize([10, 4])
    d.combo_bg_mode.setCurrentText("Region")
    d.combo_bg_calc.setCurrentText("Average")
    d.chk_enable_bg.setChecked(True)
    assert d.bg_roi is not None

    d.bg_roi.setPos([-5, 2])
    d.bg_roi.setSize([10, 4])
    assert d.plot_bg.getData()[1][0] == pytest.approx(2.0)

    d.bg_roi.setPos([-50, 2])
    xs, _ = d.plot_bg.getData()
    assert xs is None or len(xs) == 0, "a background box off the image measured the edge"
    xs, _ = d.plot_sub.getData()
    assert xs is None or len(xs) == 0
    assert "overlap" in d.lbl_bg_info.text()
    # The source spectrum is still drawn: only the background is missing.
    assert _first_spectrum_value(d) == pytest.approx(14.5)


def test_a_circular_aperture_with_a_background_box_off_the_image_says_so(loaded_viewer):
    d = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    d.combo_bg_mode.setCurrentText("Region")
    d.chk_enable_bg.setChecked(True)
    d.bg_roi.setPos([-60, 2])
    d.update_plot()
    xs, _ = d.plot_bg.getData()
    assert xs is None or len(xs) == 0
    assert "overlap" in d.lbl_bg_info.text()
    d.close()


# ---------------------------------------------------------------------------
# The Depth Plot follows the viewer (BUGS.md R7, R28)
# ---------------------------------------------------------------------------

def _channel_cube(base, shape=(5, 20, 30)):
    """A cube, numpy (z, y, x) with no header, whose channel z holds `base + z` everywhere."""
    return np.broadcast_to((base + np.arange(float(shape[0])))[:, None, None], shape).copy()


def _plotted(dialog):
    return np.asarray(dialog.plot_data.getData()[1], dtype=float)


def _curve_is_empty(curve):
    x, y = curve.getData()
    return x is None or y is None or len(x) == 0 or len(y) == 0


def test_a_new_cube_replaces_the_plotted_spectrum(qapp):
    """Loading a cube, as File > Open and the poller do, redraws the spectrum (R7)."""
    viewer = ImageViewer()
    viewer.set_data(_channel_cube(0.0), None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.combo_calc.setCurrentText("Average")
        assert _plotted(dialog) == pytest.approx([0, 1, 2, 3, 4])

        viewer.set_data(_channel_cube(50.0), None)      # no update_plot() call
        assert _plotted(dialog) == pytest.approx([50, 51, 52, 53, 54])
    finally:
        dialog.close()
        viewer.close()


def test_a_flip_replaces_the_plotted_spectrum(qapp):
    """A flip moves the aperture over different pixels, so the spectrum changes (R7)."""
    x_ramp = np.broadcast_to(np.arange(30.0)[None, None, :], (5, 20, 30)).copy()
    viewer = ImageViewer()
    viewer.set_data(x_ramp, None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.combo_calc.setCurrentText("Average")
        dialog.set_center((5, 10))
        dialog.spin_radius.setValue(1.0)
        before = _plotted(dialog)
        assert before == pytest.approx(5.0, abs=1e-6)

        viewer.flip = True
        viewer.refresh_display()                        # what Rotate Image > Flip does
        assert _plotted(dialog) == pytest.approx(24.0, abs=1e-6)
    finally:
        dialog.close()
        viewer.close()


def test_a_2d_load_clears_every_curve(qapp):
    """A 2-D image has no spectrum; the previous cube's must not stay drawn (R7)."""
    viewer = ImageViewer()
    viewer.set_data(_channel_cube(0.0), None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.chk_enable_bg.setChecked(True)           # fills all three curves
        for curve in (dialog.plot_data, dialog.plot_bg, dialog.plot_sub):
            assert len(curve.getData()[0]) > 0, "no curve to go stale"

        viewer.set_data(np.full((40, 40), 7.0), None)
        for curve in (dialog.plot_data, dialog.plot_bg, dialog.plot_sub):
            assert _curve_is_empty(curve)
        assert _legend_names(dialog) == ["Source"]
        assert "spectrum" in dialog.lbl_bg_info.text().lower()

        dialog.plot_data.setData([1.0, 2.0], [3.0, 4.0])
        dialog.update_plot()                            # an explicit call clears too
        assert _curve_is_empty(dialog.plot_data)
    finally:
        dialog.close()
        viewer.close()


def test_cut_modes_follow_the_slider(qapp):
    """A cut is taken through the plane on screen, so a slider step recomputes it (R7)."""
    viewer = ImageViewer()
    viewer.set_data(_channel_cube(0.0), None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.combo_calc.setCurrentText("Average")
        for mode in ("Horizontal Cut", "Vertical Cut"):
            dialog.combo_type.setCurrentText(mode)
            viewer.slider_slice.setValue(1)
            assert _plotted(dialog)[0] == pytest.approx(1.0), mode
            viewer.slider_slice.setValue(4)
            assert _plotted(dialog)[0] == pytest.approx(4.0), mode
    finally:
        dialog.close()
        viewer.close()


def test_the_intensity_label_follows_the_display_unit(qapp):
    """The left label names the unit the plotted values are in, in every mode (R28, R83)."""
    from astropy.io import fits
    header = fits.Header()
    header['ITIME'] = 10.0
    header['BUNIT'] = 'DN/s'
    viewer = ImageViewer()
    viewer.set_data(np.ones((30, 20, 5)), header)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.combo_calc.setCurrentText("Average")

        def left_label():
            return dialog.plot_widget.getAxis('left').labelText

        for mode in ("Depth Plot", "Horizontal Cut"):
            dialog.combo_type.setCurrentText(mode)
            viewer.disp_as_dn = False
            viewer.refresh_display()
            assert left_label() == "Intensity (DN/s)", mode
            value = _plotted(dialog)[0]

            viewer.disp_as_dn = True                    # MainWindow.set_display_unit(True)
            viewer.refresh_display()
            assert left_label() == "Intensity (Total DN)", mode
            assert _plotted(dialog)[0] == pytest.approx(value * 10.0), mode
    finally:
        dialog.close()
        viewer.close()


def test_each_change_recomputes_the_depth_plot_once(qapp):
    """Depth mode: a flip or a load recomputes once and a slider step not at all; a cut mode
    recomputes once per slider step and, again, once per flip."""
    viewer = ImageViewer()
    viewer.set_data(_channel_cube(0.0), None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    calls = []
    original = dialog.update_plot
    dialog.update_plot = lambda *a, **k: (calls.append(1), original(*a, **k))[1]
    try:
        dialog.show()
        for action in (lambda: setattr(viewer, 'flip', True) or viewer.refresh_display(),
                       lambda: viewer.set_data(_channel_cube(3.0), None)):
            calls.clear()
            action()
            assert len(calls) == 1
        calls.clear()
        viewer.slider_slice.setValue(2)
        assert calls == []

        dialog.combo_type.setCurrentText("Vertical Cut")
        calls.clear()
        viewer.slider_slice.setValue(3)
        assert len(calls) == 1
        viewer.flip = False
        calls.clear()
        viewer.refresh_display()
        assert len(calls) == 1
    finally:
        dialog.close()
        viewer.close()


def test_a_2d_load_leaves_no_line_overlay_or_wavelength_readout(loaded_viewer):
    """The previous cube's spectral lines and wavelength axis must not outlive it (R7)."""
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        dialog.show()
        dialog.chk_enable_lines.setChecked(True)
        dialog.update_plot()
        assert dialog.line_items, "no line overlay to go stale"
        assert dialog.current_wavelengths is not None

        loaded_viewer.set_data(np.full((40, 40), 7.0), None)
        assert dialog.line_items == []
        assert dialog.current_wavelengths is None

        centre = dialog.plot_widget.plotItem.vb.sceneBoundingRect().center()
        dialog.mouse_moved([centre])
        assert "Wavelength" not in dialog.lbl_cursor.text()
    finally:
        dialog.close()


def test_the_line_list_needs_a_known_wavelength_unit(qapp, nifs_like_header):
    """R82: a NIFS cube states no wavelength unit. Its axis is shown in the header's own
    numbers and labelled Unknown, and the line list (in µm) is not matched against it."""
    cube = np.ones((1000, 12, 10), dtype=np.float32)
    viewer = ImageViewer()
    viewer.set_data(cube, nifs_like_header)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.update_plot()
        dialog.chk_enable_lines.setChecked(True)
        dialog.update_line_overlays()

        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (Unknown)"
        assert dialog.plot_data.getData()[0][0] == pytest.approx(19968.66)
        # The Cube wavelength unit combo is how the user supplies one (Task 23). It sits with
        # the plot's axes, not the line list, because it is the unit of the cube's numbers.
        assert dialog.combo_wave_unit.parent().title() == "PLOT AXES"
        assert dialog.lbl_line_info.text() == ("Set the cube's wavelength unit under Plot Axes to show the line list "
                                                "(the line list is in µm)")
        assert not dialog.line_items
    finally:
        dialog.close()

    # Stated in the WAT card, the unit is used: 1000 channels from 1.997 µm reach Brγ
    nifs_like_header['WAT3_001'] = 'wtype=linear axtype=wave units=angstroms'
    viewer.set_data(cube, nifs_like_header)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.update_plot()
        dialog.chk_enable_lines.setChecked(True)
        dialog.update_line_overlays()

        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (µm)"
        assert dialog.plot_data.getData()[0][0] == pytest.approx(1.99687, abs=1e-5)
        assert dialog.line_items, dialog.lbl_line_info.text()
    finally:
        dialog.close()


def test_a_velocity_axis_is_not_a_wavelength(qapp):
    """A `VELO` axis is spectral, but its unit (m/s) is not a wavelength: the line list says
    so rather than "WCS conversion failed.", and the timeline has no wavelength axis whose
    ticks would only repeat the channel numbers below it."""
    from astropy.io import fits
    header = fits.Header({'NAXIS': 3, 'CTYPE1': 'RA---TAN', 'CTYPE2': 'DEC--TAN',
                          'CTYPE3': 'VELO', 'CUNIT1': 'deg', 'CUNIT2': 'deg', 'CUNIT3': 'm/s',
                          'CRVAL1': 266.4, 'CRVAL2': -29.0, 'CRVAL3': 0.0,
                          'CDELT1': -1e-5, 'CDELT2': 1e-5, 'CDELT3': 1000.0,
                          'CRPIX1': 5.0, 'CRPIX2': 5.0, 'CRPIX3': 1.0})
    viewer = ImageViewer()
    viewer.set_data(np.ones((20, 12, 10), dtype=np.float32), header)
    assert viewer.wcs_z_idx == 2
    assert not viewer.top_axis.isVisible()

    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.update_plot()
        dialog.chk_enable_lines.setChecked(True)
        dialog.update_line_overlays()

        assert dialog.lbl_line_info.text() == "Z-axis is not a wavelength (speed/velocity)."
        assert not dialog.line_items
    finally:
        dialog.close()


# ---------------------------------------------------------------------------
# Spectral photometry edge cases (BUGS.md R20, R22)
# ---------------------------------------------------------------------------

def test_a_region_background_uses_the_per_channel_area(qapp):
    """R20: a Region background is charged for the aperture pixels that hold data in each
    channel, as the annulus is. Charging the full area over-subtracted by the level times
    the number of NaN pixels, in exactly the channels that have them."""
    data = np.full((3, 30, 40), 5.0)          # numpy (z, y, x): a flat sky of 5, no source
    data[1, 15, 12:14] = np.nan               # two bad pixels inside the aperture, channel 1
    viewer = ImageViewer()
    viewer.set_data(data, None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.set_center((12.0, 15.0))
        dialog.chk_enable_bg.setChecked(True)
        dialog.combo_bg_mode.setCurrentText("Region")
        dialog.bg_roi.setPos([25, 5])
        dialog.bg_roi.setSize([8, 8])
        assert dialog.combo_calc.currentText() == "Total"
        assert dialog.plot_bg.getData()[1] == pytest.approx([5.0, 5.0, 5.0])
        assert dialog.plot_sub.getData()[1] == pytest.approx([0.0, 0.0, 0.0], abs=1e-9)
    finally:
        dialog.close()
        viewer.close()


def test_growing_the_aperture_past_the_sky_annulus_says_so(qapp):
    """R22: sky radii the user chose stay put when the aperture grows past them, and the
    status line says the annulus now starts inside the aperture instead of subtracting
    source flux as sky."""
    xx, yy = np.mgrid[:41, :41]
    star = 1000.0 * np.exp(-((xx - 20) ** 2 + (yy - 20) ** 2) / (2 * 2.5 ** 2))
    viewer = ImageViewer()
    viewer.set_data(np.repeat((star + 2.0)[None, :, :], 2, axis=0), None)   # sky = 2
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.set_center((20.0, 20.0))
        dialog.chk_enable_bg.setChecked(True)
        dialog.spin_r_in.setValue(5.0)
        dialog.spin_r_out.setValue(8.0)
        dialog.spin_radius.setValue(7.0)

        assert (dialog.spin_r_in.value(), dialog.spin_r_out.value()) == (5.0, 8.0)
        assert "the sky annulus starts inside the aperture" in dialog.lbl_bg_info.text()
        assert _curve_is_empty(dialog.plot_sub) and _curve_is_empty(dialog.plot_bg)
    finally:
        dialog.close()
        viewer.close()


# ---------------------------------------------------------------------------
# Line lists, Log X, the rectangle spins, the off-image circle mask (BUGS.md R53-R55, R80)
# and a wavelength unit chosen by the user (R82)
# ---------------------------------------------------------------------------

def _k_band_viewer():
    """A cube with a WAVE axis in nm on FITS axis 1, 2100-2200 nm (R53, R54)."""
    from astropy.wcs import WCS
    w = WCS(naxis=3)
    w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
    w.wcs.cunit = ['nm', 'deg', 'deg']
    w.wcs.crval = [2100.0, 0, 0]
    w.wcs.cdelt = [0.25, 1e-5, 1e-5]
    w.wcs.crpix = [1, 1, 1]
    viewer = ImageViewer()
    viewer.set_data(np.random.default_rng(1).normal(10, 1, (30, 20, 400)), w.to_header())
    return viewer


def test_the_line_list_survives_log_x(qapp):
    """R53: in log mode pyqtgraph's view x is log10(wavelength). The lines, the cursor
    readout and the channel axis convert it back; each line is placed at log10 of its
    wavelength."""
    from PySide6.QtCore import QPointF
    viewer = _k_band_viewer()
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.chk_enable_lines.setChecked(True)
        qapp.processEvents()
        dialog.update_line_overlays()
        linear = dialog.lbl_line_info.text()
        x0, x1 = dialog.plot_widget.getViewBox().viewRange()[0]
        expected = [wl for wl, _ in dialog.loaded_lines if x0 <= wl <= x1]
        assert expected, "no line in the K-band view to check"

        dialog.chk_log_x.setChecked(True)
        qapp.processEvents()
        dialog.update_line_overlays()

        assert dialog.lbl_line_info.text() == linear
        placed = [line.value() for line, _ in dialog.line_items]
        assert placed == pytest.approx(np.log10(expected).tolist())
        assert [text.pos().x() for _, text in dialog.line_items] == pytest.approx(placed)

        vb = dialog.plot_widget.getViewBox()
        (lx0, lx1), (y0, y1) = vb.viewRange()
        mid = (lx0 + lx1) / 2
        dialog.mouse_moved((vb.mapViewToScene(QPointF(mid, (y0 + y1) / 2)),))
        assert dialog.lbl_cursor.text().startswith(f"Wavelength: {10 ** mid:.4f} µm")
        # Channel 200 is 2150 nm
        assert dialog.top_axis.tickStrings([np.log10(2.15)], 1, 1) == ["200"]
    finally:
        dialog.close()
        viewer.close()


def _browse(dialog, monkeypatch, path, text):
    """Write `text` to `path` and load it through the Browse button, its dialog stubbed."""
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text)
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))
    dialog.browse_custom_linelist()
    return str(path)


def test_two_line_lists_with_one_name_load_their_own_files(qapp, tmp_path, monkeypatch):
    """R54: the combo entry carries its path, and that path is what is loaded."""
    viewer = _k_band_viewer()
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.chk_enable_lines.setChecked(True)
        a = _browse(dialog, monkeypatch, tmp_path / "A" / "lines.csv", "2.1661,from_A\n")
        b = _browse(dialog, monkeypatch, tmp_path / "B" / "lines.csv", "2.2000,from_B\n")
        assert dialog.combo_linelist.findData(a) != dialog.combo_linelist.findData(b)

        dialog.combo_linelist.setCurrentIndex(0)
        dialog.combo_linelist.setCurrentIndex(dialog.combo_linelist.findData(a))
        assert dialog.combo_linelist.currentData() == a
        assert dialog.loaded_lines == [(2.1661, 'from_A')]

        dialog.combo_linelist.setCurrentIndex(dialog.combo_linelist.findData(b))
        assert dialog.loaded_lines == [(2.2, 'from_B')]
    finally:
        dialog.close()
        viewer.close()


def test_an_unreadable_line_list_says_so(qapp, tmp_path, monkeypatch):
    """R54: a file with no line in it leaves no previous list's status on screen."""
    viewer = _k_band_viewer()
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.chk_enable_lines.setChecked(True)
        _browse(dialog, monkeypatch, tmp_path / "good.csv", "2.1661,Brgamma\n")
        assert dialog.lbl_line_info.text().startswith("1 line(s) visible")

        _browse(dialog, monkeypatch, tmp_path / "notes.txt", "no wavelengths here\n# 2.1\n")
        assert dialog.loaded_lines == []
        assert not dialog.line_items
        assert dialog.lbl_line_info.text() == "No lines could be read from notes.txt"
    finally:
        dialog.close()
        viewer.close()


def test_a_whitespace_separated_line_list_loads(qapp, tmp_path, monkeypatch):
    """R54: a line with no comma is split on whitespace; the name keeps its spaces."""
    viewer = _k_band_viewer()
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.chk_enable_lines.setChecked(True)
        _browse(dialog, monkeypatch, tmp_path / "ws_lines.txt",
                "2.1661 Brgamma\n2.1218\tH2 1-0 S(1)\n2.1500, comma, kept\n")
        assert dialog.loaded_lines == [(2.1661, 'Brgamma'), (2.1218, 'H2 1-0 S(1)'),
                                       (2.15, 'comma')]
        assert len(dialog.line_items) == 3
    finally:
        dialog.close()
        viewer.close()


def test_the_rectangle_spins_follow_a_radius_edit(qapp):
    """R55: a radius edit moves the box, and the hidden rectangle spins with it, so the first
    rectangle edit starts from the box on screen."""
    viewer = ImageViewer()
    viewer.set_data(np.ones((3, 20, 30)), None)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.set_center((15.0, 10.0))
        dialog.spin_radius.setValue(6.0)

        def edges():
            pos, size = dialog.roi.pos(), dialog.roi.size()
            x0, y0 = int(pos.x()), int(pos.y())
            return (x0, x0 + int(size.x()), y0, y0 + int(size.y()))

        def spins():
            return (dialog.spin_x0.value(), dialog.spin_x1.value(),
                    dialog.spin_y0.value(), dialog.spin_y1.value())

        assert spins() == edges()

        dialog.combo_shape.setCurrentText("Rectangle")
        assert spins() == edges()
        width, height = dialog.roi.size().x(), dialog.roi.size().y()
        dialog.spin_y1.setValue(dialog.spin_y1.value() + 1)
        assert (dialog.roi.size().x(), dialog.roi.size().y()) == (width, height + 1)
    finally:
        dialog.close()
        viewer.close()


def test_a_circle_off_the_image_masks_the_drawn_circle(qapp):
    """R80: a Circle background box half off the low edge keeps exactly the drawn circle's
    on-image pixels, not a smaller circle centred on the part of the box that is on it."""
    viewer = ImageViewer()
    viewer.set_data(np.ones((2, 30, 40)), None)        # display (z, x=40, y=30)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.set_center((20.0, 15.0))
        dialog.combo_bg_mode.setCurrentText("Region")
        dialog.chk_enable_bg.setChecked(True)
        dialog.combo_bg_calc.setCurrentText("Total")
        dialog.bg_roi.setPos([-4.0, 10.0])
        dialog.bg_roi.setSize([10.0, 10.0])

        # The drawn circle: centre (1, 15) in ImageItem coordinates, radius 5
        xx, yy = np.mgrid[:40, :30]
        inside = ((xx + 0.5 - 1.0) ** 2 + (yy + 0.5 - 15.0) ** 2) <= 5.0 ** 2
        shape = (2, 40, 30)
        ones = np.ones(shape)
        xs = np.broadcast_to(xx.astype(float), shape).copy()
        ys = np.broadcast_to(yy.astype(float), shape).copy()
        assert dialog.region_background_level(ones)[0] == inside.sum()
        assert dialog.region_background_level(xs)[0] == xx[inside].sum()
        assert dialog.region_background_level(ys)[0] == yy[inside].sum()
    finally:
        dialog.close()
        viewer.close()


def _unit_index(dialog, text):
    index = dialog.combo_wave_unit.findText(text)
    assert index >= 0, f"no {text!r} in the wavelength unit combo"
    return index


def test_choosing_a_wavelength_unit_enables_the_line_list(qapp, nifs_like_header):
    """R82: a NIFS cube states no wavelength unit, so the line list waits for the user to
    choose one. Chosen, it is shown with the header's numbers and labelled as theirs."""
    import astropy.units as u
    from pyql3.core.header_info import in_microns
    cube = np.ones((1000, 12, 10), dtype=np.float32)
    viewer = ImageViewer()
    viewer.set_data(cube, nifs_like_header)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.chk_enable_lines.setChecked(True)
        dialog.update_line_overlays()
        assert dialog.combo_wave_unit.currentText() == "From header (Unknown)"
        assert dialog.lbl_line_info.text() == ("Set the cube's wavelength unit under Plot Axes to show the line list "
                                                "(the line list is in µm)")
        assert not dialog.line_items

        dialog.combo_wave_unit.setCurrentIndex(_unit_index(dialog, "Å"))

        assert viewer.wavelength_unit_override == u.AA
        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (Å, set by you)"
        x0 = dialog.plot_data.getData()[0][0]
        assert x0 == pytest.approx(19968.66)
        assert in_microns(x0, u.AA) == pytest.approx(1.99687, abs=1e-5)
        assert dialog.line_items, dialog.lbl_line_info.text()
        # Brγ, 2.1661 µm, is drawn at 21661 Å
        assert any(line.value() == pytest.approx(21661.0) for line, _ in dialog.line_items)

        dialog.combo_wave_unit.setCurrentIndex(0)
        assert viewer.wavelength_unit_override is None
        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (Unknown)"
        assert not dialog.line_items
    finally:
        dialog.close()
        viewer.close()


def test_the_wavelength_unit_choice_survives_a_reload_without_a_unit(qapp, nifs_like_header):
    """Successive frames from one instrument keep the unit the user chose for the first."""
    import astropy.units as u
    cube = np.ones((1000, 12, 10), dtype=np.float32)
    viewer = ImageViewer()
    viewer.set_data(cube, nifs_like_header)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.combo_wave_unit.setCurrentIndex(_unit_index(dialog, "Å"))

        viewer.set_data(cube * 2, nifs_like_header)

        assert viewer.wavelength_unit_override == u.AA
        assert dialog.combo_wave_unit.currentText() == "Å"
        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (Å, set by you)"
        assert dialog.plot_data.getData()[0][0] == pytest.approx(19968.66)
    finally:
        dialog.close()
        viewer.close()


def test_a_header_unit_replaces_an_unused_override(qapp, nifs_like_header):
    """A unit chosen for a file that stated none gives way to the next file that states one.
    A unit chosen over a stated unit corrects that header, and stays for files stating it."""
    import astropy.units as u
    cube = np.ones((1000, 12, 10), dtype=np.float32)
    stated = nifs_like_header.copy()
    stated['WAT3_001'] = 'wtype=linear axtype=wave units=angstroms'
    viewer = ImageViewer()
    viewer.set_data(cube, nifs_like_header)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.combo_wave_unit.setCurrentIndex(_unit_index(dialog, "nm"))
        assert viewer.wavelength_unit_override == u.nm

        viewer.set_data(cube, stated)

        assert viewer.wavelength_unit_override is None
        assert viewer.wavelength_unit_label() == "µm"
        assert dialog.combo_wave_unit.currentIndex() == 0
        assert dialog.combo_wave_unit.currentText() == "From header (Å)"
        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (µm)"

        # Chosen over the stated Å, nm is kept by the next file that states Å
        dialog.combo_wave_unit.setCurrentIndex(_unit_index(dialog, "nm"))
        viewer.set_data(cube, stated)
        assert viewer.wavelength_unit_override == u.nm
        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (nm, set by you)"
    finally:
        dialog.close()
        viewer.close()


def _stated_wave_header(unit, crval, cdelt):
    """A WAVE axis on FITS axis 1 with `CUNIT1 = unit`, or no CUNIT1 when `unit` is None."""
    from astropy.io import fits
    header = fits.Header({'NAXIS': 3, 'CTYPE1': 'WAVE', 'CTYPE2': 'DEC--TAN',
                          'CTYPE3': 'RA---TAN', 'CUNIT2': 'deg', 'CUNIT3': 'deg',
                          'CRVAL1': crval, 'CRVAL2': -29.0, 'CRVAL3': 266.4,
                          'CDELT1': cdelt, 'CDELT2': 1e-5, 'CDELT3': -1e-5,
                          'CRPIX1': 1.0, 'CRPIX2': 1.0, 'CRPIX3': 1.0})
    if unit is not None:
        header['CUNIT1'] = unit
    return header


def test_a_unit_chosen_over_a_stated_unit_gives_way_to_a_different_one(qapp):
    """A unit chosen over the unit one file states corrects that file's header. It is kept for
    later files that state the same unit or none, and cleared by a file that states another:
    that header says what its own numbers are."""
    import astropy.units as u
    cube = np.ones((30, 20, 400))                      # numpy (RA, Dec, wave)
    nm = _stated_wave_header('nm', 2100.0, 0.25)
    viewer = ImageViewer()
    viewer.set_data(cube, nm)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.chk_enable_lines.setChecked(True)
        dialog.combo_wave_unit.setCurrentIndex(_unit_index(dialog, "nm"))
        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (nm, set by you)"

        viewer.set_data(cube, nm)                       # the same stated unit: kept
        assert viewer.wavelength_unit_override == u.nm
        viewer.set_data(cube, _stated_wave_header(None, 2100.0, 0.25))   # none stated: kept
        assert viewer.wavelength_unit_override == u.nm

        viewer.set_data(cube, _stated_wave_header('um', 2.1, 0.00025))   # another: cleared
        assert viewer.wavelength_unit_override is None
        assert dialog.combo_wave_unit.currentText() == "From header (µm)"
        assert dialog.plot_widget.getAxis('bottom').labelText == "Wavelength (µm)"
        assert dialog.plot_data.getData()[0][0] == pytest.approx(2.1)
        dialog.update_line_overlays()
        assert dialog.line_items, dialog.lbl_line_info.text()
    finally:
        dialog.close()
        viewer.close()


def test_log_x_with_a_chosen_unit_places_the_lines(qapp, nifs_like_header):
    """I1 (Phase 6 review): ticking Log X redrew the lines against the still-linear range and
    raised OverflowError computing 10 ** 19968. The lines follow the range once it is in log."""
    import astropy.units as u
    viewer = ImageViewer()
    viewer.set_data(np.ones((1000, 12, 10), dtype=np.float32), nifs_like_header)
    dialog = DepthPlotDialog(image_viewer=viewer)
    try:
        dialog.show()
        dialog.chk_enable_lines.setChecked(True)
        dialog.combo_wave_unit.setCurrentIndex(_unit_index(dialog, "Å"))
        qapp.processEvents()
        linear = sorted(line.value() for line, _ in dialog.line_items)
        assert linear

        for log in (True, False):
            dialog.chk_log_x.blockSignals(True)
            dialog.chk_log_x.setChecked(log)
            dialog.chk_log_x.blockSignals(False)
            dialog.toggle_log_scale()                   # called directly, so errors propagate
            qapp.processEvents()
            placed = sorted(line.value() for line, _ in dialog.line_items)
            assert placed, dialog.lbl_line_info.text()
            in_angstrom = [10 ** x for x in placed] if log else placed
            # Every line placed is a line of the list, in Å
            lines_aa = [(wl * u.um).to_value(u.AA) for wl, _ in dialog.loaded_lines]
            for x in in_angstrom:
                assert min(abs(x - wl) for wl in lines_aa) < 1e-6 * x
        assert placed == pytest.approx(linear)
    finally:
        dialog.close()
        viewer.close()


def test_a_rectangle_total_is_nan_where_no_pixel_is_finite(x_cube_dialog):
    """R21, the rectangle path: a channel with no finite pixel in the box has no total. 0.0
    would be drawn as a measurement."""
    d = x_cube_dialog
    cube = d.image_viewer.raw_data.copy()
    cube[1] = np.nan
    d.image_viewer.set_data(cube, None)
    d.combo_calc.setCurrentText("Total")
    d.roi.setPos([10, 2])
    d.roi.setSize([10, 4])
    y = d.plot_data.getData()[1]
    assert np.isnan(y[1]), f"an all-NaN channel totalled {y[1]}"
    assert np.isfinite(y[[0, 2]]).all()

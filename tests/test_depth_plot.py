import pytest
import numpy as np
import pyqtgraph as pg
from PySide6.QtWidgets import QFileDialog
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
    assert center == pytest.approx((15.0, 25.0)), f"ROI centre is {center}, expected (15, 25)"
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
        assert dialog.spin_r_in.isEnabled() and dialog.spin_r_out.isEnabled()
        assert not dialog.spin_bg_x0.isEnabled(), \
            "the region box spins describe a region that no longer exists"

        bg_x, bg_y = dialog.plot_bg.getData()
        sub_x, _ = dialog.plot_sub.getData()
        assert bg_x is not None and len(bg_x) > 0, "no background spectrum was plotted"
        assert sub_x is not None and len(sub_x) > 0, "no subtracted spectrum was plotted"
    finally:
        dialog.close()


def _ring_center(ring):
    pos, size = ring.pos(), ring.size()
    return pos.x() + size.x() / 2.0, pos.y() + size.y() / 2.0


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

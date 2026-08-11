import pytest
import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import Qt
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
    so re-ranging on every mouse-move event made a drag cost 37.7% more than it needed to.
    """
    dialog = DepthPlotDialog(image_viewer=loaded_viewer, initial_center=(20, 20))
    try:
        vb = dialog.plot_widget.getViewBox()
        assert vb.autoRangeEnabled()[1], "the Y axis should auto-range before any drag"

        dialog.roi.sigRegionChangeStarted.emit(dialog.roi)
        frozen = list(vb.viewRange()[1])

        for pos in ((5, 5), (12, 18), (25, 9)):
            dialog.roi.setPos(list(pos), finish=False)
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

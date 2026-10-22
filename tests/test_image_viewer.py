import pytest
import numpy as np
import pyqtgraph as pg
from PySide6.QtTest import QTest
from types import SimpleNamespace
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.core.fits_reader import FitsReader


def test_image_viewer_data_state(loaded_viewer):
    """CRITICAL RULE TEST: Ensure raw_data preserves original FITS array shape while transposed_data is formatted for PyQtGraph rendering."""
    assert loaded_viewer.raw_data is not None, "ImageViewer raw_data is None after loading FITS"
    assert loaded_viewer.transposed_data is not None, "ImageViewer transposed_data is None after loading FITS"

    # raw_data retains original un-transposed shape: (WAVE, DEC, RA)
    assert loaded_viewer.raw_data.ndim == 3
    assert loaded_viewer.transposed_data.ndim == 3
    assert loaded_viewer.raw_data.shape == (50, 40, 40), f"raw_data shape mismatch: {loaded_viewer.raw_data.shape}"


def test_image_viewer_rotation_and_flip(loaded_viewer):
    """Test image viewer rotation angle and flip transformations."""
    loaded_viewer.rot_angle = 90
    assert loaded_viewer.rot_angle == 90
    loaded_viewer.rot_angle = 180
    assert loaded_viewer.rot_angle == 180
    loaded_viewer.rot_angle = 0
    assert loaded_viewer.rot_angle == 0

    loaded_viewer.flip = True
    assert loaded_viewer.flip is True
    loaded_viewer.flip = False
    assert loaded_viewer.flip is False


def test_image_viewer_scaling_and_units(loaded_viewer):
    """Test log vs linear display scaling and DN/s vs Total DN unit toggling."""
    loaded_viewer.exptime = 10.0
    loaded_viewer.disp_as_dn = True
    mult = loaded_viewer.data_multiplier
    assert mult == 10.0, f"Expected multiplier 10.0 for EXPTIME=10 with Total DN, got {mult}"

    loaded_viewer.disp_as_dn = False
    mult_sec = loaded_viewer.data_multiplier
    assert mult_sec == 1.0, f"Expected multiplier 1.0 for DN/s, got {mult_sec}"

    loaded_viewer.use_log_scale = True
    assert loaded_viewer.use_log_scale is True
    loaded_viewer.use_log_scale = False
    assert loaded_viewer.use_log_scale is False


def test_image_viewer_colormap_fallback(qapp):
    """Test colormap setting with native pyqtgraph, matplotlib, cmcrameri, and invalid fallbacks."""
    viewer = ImageViewer()

    colormaps_to_test = [
        "viridis",
        "plasma",
        "grey",
        "coolwarm",
        "cmc.oslo",
        "cmc.hawaii",
        "cmc.batlow",
        "non_existent_colormap_12345" # Should trigger fallback to viridis without crash
    ]

    for cmap in colormaps_to_test:
        try:
            viewer.set_colormap(cmap)
            viewer.set_colormap(cmap, invert=True)
        except Exception as e:
            pytest.fail(f"ImageViewer.set_colormap('{cmap}') crashed with error: {e}")


SCALE_MODES = ["Linear", "Negative", "HistEq", "Logarithmic", "Sqrt", "AsinH"]


@pytest.mark.parametrize("dtype", ["int16", "int32", "uint16", "float32", "float64"])
@pytest.mark.parametrize("scale", SCALE_MODES)
def test_scaling_modes_preserve_contrast_for_all_dtypes(qapp, dtype, scale):
    """B3: every scale mode must render varying values, including for integer FITS data.

    HistEq built its output with np.zeros_like(), so the normalized [0, 1) values
    truncated to 0 for integer dtypes and the image went uniformly flat. Negative
    additionally wrapped for unsigned data.
    """
    viewer = ImageViewer()
    data = (np.arange(64 * 32) % 500).reshape(64, 32).astype(dtype)

    viewer.set_data(data)
    viewer.combo_scale.setCurrentText(scale)
    viewer.update_image_display()

    render = viewer.imv.getImageItem().image
    assert np.issubdtype(render.dtype, np.floating), \
        f"{scale}/{dtype} rendered as {render.dtype}, which truncates scaled values"
    assert np.nanmax(render) > np.nanmin(render), \
        f"{scale}/{dtype} rendered a flat image (min == max == {np.nanmin(render)})"


def test_negative_scale_does_not_wrap_for_unsigned(qapp):
    """B3: -display_data wraps around for unsigned integers, inverting the contrast."""
    viewer = ImageViewer()
    data = np.arange(64 * 32, dtype=np.uint16).reshape(64, 32) % 500
    viewer.set_data(data)
    viewer.combo_scale.setCurrentText("Negative")
    viewer.update_image_display()

    render = viewer.imv.getImageItem().image
    assert np.nanmin(render) < 0, \
        f"Negative of unsigned data should be <= 0, got min {np.nanmin(render)} (wrapped)"


def test_histeq_handles_nans_without_dividing_by_zero(qapp):
    """B3: HistEq maps only the valid pixels; NaNs stay 0 and must not poison the rest."""
    data = ((np.arange(64 * 32) % 500).reshape(64, 32)).astype(np.float32)
    data[:8, :] = np.nan

    viewer = ImageViewer()
    viewer.set_data(data)
    viewer.combo_scale.setCurrentText("HistEq")
    with np.errstate(divide="raise", invalid="raise"):
        viewer.update_image_display()

    # set_data transposes into pyqtgraph's (X, Y) order, so locate the NaNs via display_data
    nan_mask = np.isnan(viewer.display_data)
    assert nan_mask.sum() == 8 * 32

    render = viewer.imv.getImageItem().image
    assert np.all(render[nan_mask] == 0), "NaN pixels should render as 0"
    finite = render[~nan_mask]
    assert np.all(np.isfinite(finite)), "Valid pixels should not become NaN"
    assert 0.0 < finite.max() <= 1.0, f"HistEq output out of range: max {finite.max()}"


def _make_cube(nz=10, nx=12, ny=14):
    np.random.seed(0)
    return np.random.rand(nz, nx, ny).astype(np.float32)


def test_timeline_drag_syncs_slider_labels_and_readout(qapp):
    """B4: dragging the pyqtgraph timeline must write back to the Z Slice slider.

    Previously only update_slice_info was connected, and it read the slider rather than
    the displayed index, so the slider, the labels, the Value: readout and every analysis
    tool reported a different slice than the one on screen.
    """
    viewer = ImageViewer()
    viewer.set_data(_make_cube())

    viewer.imv.setCurrentIndex(7)  # equivalent to dragging the timeline handle

    assert viewer.slider_slice.value() == 7
    assert viewer.lbl_slice_val.text() == "7"
    assert "Slice: 7" in viewer.lbl_slice_info.text()
    assert viewer.current_z() == 7
    assert viewer.imv.currentIndex == 7


def test_slider_still_drives_the_timeline(qapp):
    """B4: the sync must not break the slider -> ImageView direction, or recurse."""
    viewer = ImageViewer()
    viewer.set_data(_make_cube())

    for value in (3, 9, 0, 5):
        viewer.slider_slice.setValue(value)
        assert viewer.imv.currentIndex == value
        assert viewer.current_z() == value
        assert f"Slice: {value}" in viewer.lbl_slice_info.text()


def test_reload_preserves_displayed_slice(qapp):
    """B4 (reverse direction): setImage() resets the ImageView to 0 and emits
    sigTimeChanged, so a reload or poller auto-load showed slice 0 while the slider,
    labels and tools still reported the old index."""
    viewer = ImageViewer()
    cube = _make_cube()
    viewer.set_data(cube)
    viewer.slider_slice.setValue(6)

    viewer.set_data(cube + 1.0)  # a new frame for the same target

    assert viewer.slider_slice.value() == 6
    assert viewer.imv.currentIndex == 6, "display dropped to slice 0 behind the slider"
    assert "Slice: 6" in viewer.lbl_slice_info.text()


def test_slice_index_clamped_when_cube_shrinks(qapp):
    """A shorter cube must leave the slider and the ImageView agreeing."""
    viewer = ImageViewer()
    viewer.set_data(_make_cube(nz=10))
    viewer.slider_slice.setValue(viewer.slider_slice.maximum())

    viewer.set_data(_make_cube(nz=4))

    nz = viewer.transposed_data.shape[0]
    assert viewer.slider_slice.value() <= nz - 1
    assert viewer.imv.currentIndex == viewer.slider_slice.value()
    assert viewer.current_z() == viewer.slider_slice.value()


def test_hover_readout_reports_the_displayed_slice(qapp):
    """B4: the Value: readout took z from the slider, so a timeline drag reported the
    pixel value of the previously displayed slice."""
    viewer = ImageViewer()
    cube = _make_cube()
    viewer.set_data(cube)
    viewer.imv.setCurrentIndex(7)

    z = viewer.current_z()
    x, y = 3, 4
    expected = viewer.display_data[z, x, y]

    scene_pos = viewer.imv.getView().mapViewToScene(pg.Point(x + 0.5, y + 0.5))
    viewer.mouse_moved([scene_pos])

    assert viewer.lbl_val.text().startswith("Value:")
    assert f"{expected:.5g}" in viewer.lbl_val.text(), \
        f"readout {viewer.lbl_val.text()!r} does not match slice {z} value {expected:.5g}"


def test_current_z_tracks_boxcar_and_collapse_range(qapp):
    """B4: imv.currentIndex goes stale once a boxcar/range is drawn via bypass_imv, so
    tools that index the cube with it disagree with the display."""
    viewer = ImageViewer()
    viewer.set_data(_make_cube(nz=20))
    viewer.slider_slice.setValue(2)
    assert viewer.current_z() == 2

    # Boxcar: centre of the averaged window
    viewer.txt_boxcar.setText("5")
    viewer.slider_slice.setValue(9)
    assert viewer.current_z() == 9
    viewer.txt_boxcar.setText("1")

    # Collapse range: middle of the range
    viewer.txt_zmin.setText("4")
    viewer.txt_zmax.setText("8")
    viewer.radio_range.setChecked(True)
    viewer.z_mode_changed()
    assert viewer.current_z() == 6

    # Reversed / out-of-range entries must not produce an out-of-bounds index
    viewer.txt_zmin.setText("18")
    viewer.txt_zmax.setText("3")
    assert 0 <= viewer.current_z() < viewer.transposed_data.shape[0]


def test_timeline_sync_ignored_in_collapse_range_mode(qapp):
    """The timeline is hidden while a range is collapsed; a stray sigTimeChanged must not
    move the slider and silently switch the display out of range mode."""
    viewer = ImageViewer()
    viewer.set_data(_make_cube(nz=20))
    viewer.txt_zmin.setText("2")
    viewer.txt_zmax.setText("5")
    viewer.radio_range.setChecked(True)
    viewer.z_mode_changed()

    before = viewer.slider_slice.value()
    viewer.imv.sigTimeChanged.emit(19, 19.0)

    assert viewer.slider_slice.value() == before
    assert "Collapsed: 2-5" in viewer.lbl_slice_info.text()


@pytest.fixture
def channel_cube(qapp):
    """Viewer on a cube whose wavelength channel k holds the constant value k.

    Any reported number then names exactly which channel(s) were used. Uses a real
    OSIRIS-ordered WCS, so numpy order is (RA, DEC, WAVE) and wavelength is axis 2.
    """
    from astropy.wcs import WCS

    nra, ndec, nz = 12, 16, 20
    data = np.zeros((nra, ndec, nz), dtype=np.float32)
    for k in range(nz):
        data[:, :, k] = k

    w = WCS(naxis=3)
    w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
    w.wcs.crval = [2.2, 34.0, -118.0]
    w.wcs.cdelt = [0.0005, 0.0001, 0.0001]
    w.wcs.crpix = [1, 8, 6]
    w.wcs.cunit = ['um', 'deg', 'deg']
    header = w.to_header()
    header['EXPTIME'] = 1.0
    header['ITIME'] = 1.0

    viewer = ImageViewer()
    viewer.set_data(data, header)
    assert viewer.transposed_data.shape[0] == nz, "wavelength should be the viewer's z axis"
    return viewer


# --- B12: collapse-range clamping -------------------------------------------------

@pytest.mark.parametrize("typed,expected", [
    (("10", "3"), (3, 10)),      # reversed
    (("99", "3"), (3, 19)),      # past the end
    (("-5", "4"), (0, 4)),       # before the start
    (("7", "7"), (7, 7)),        # single channel
    (("0", "19"), (0, 19)),      # whole cube
])
def test_collapse_range_is_clamped_and_written_back(channel_cube, typed, expected):
    """B12: a reversed or out-of-range entry sliced an empty subcube, so nanmedian
    returned an all-NaN plane: blank display, console warning, and a label that still
    read e.g. 'Collapsed: 10-3'."""
    v = channel_cube
    v.combo_collapse.setCurrentText("Mean")
    v.radio_range.setChecked(True)
    v.txt_zmin.setText(typed[0])
    v.txt_zmax.setText(typed[1])
    v.apply_z_range()

    zmin, zmax = expected
    # the boxes are corrected so the UI shows the range actually used
    assert (v.txt_zmin.text(), v.txt_zmax.text()) == (str(zmin), str(zmax))
    assert f"Collapsed: {zmin}-{zmax}" in v.lbl_slice_info.text()

    render = v.imv.getImageItem().image
    assert np.all(np.isfinite(render)), "collapse produced an all-NaN plane"
    assert np.isclose(np.nanmean(render), np.mean(np.arange(zmin, zmax + 1)))


def test_collapse_range_emits_no_empty_slice_warning(channel_cube):
    """B12: the empty subcube used to raise 'Mean of empty slice' on the console."""
    v = channel_cube
    v.combo_collapse.setCurrentText("Median")
    v.radio_range.setChecked(True)
    v.txt_zmin.setText("15")
    v.txt_zmax.setText("2")

    import warnings as _warnings
    with _warnings.catch_warnings(record=True) as caught:
        _warnings.simplefilter("always")
        v.apply_z_range()
    runtime = [str(x.message) for x in caught if issubclass(x.category, RuntimeWarning)]
    assert runtime == [], f"unexpected RuntimeWarnings: {runtime}"


def test_unparsable_collapse_range_is_reported_and_collapses_the_full_range(channel_cube):
    """Garbage in the box says so and must not be rewritten. What is drawn is the whole cube
    collapsed, so the screen, `display_data` and `current_plane()` still describe one plane
    (R84; it used to leave the previous collapse on screen)."""
    v = channel_cube
    v.combo_collapse.setCurrentText("Sum")
    v.radio_range.setChecked(True)
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.apply_z_range()
    assert np.allclose(v.imv.getImageItem().image, 30.0)

    v.txt_zmin.setText("abc")
    v.apply_z_range()

    assert "Invalid Range" in v.lbl_slice_info.text()
    assert v.txt_zmin.text() == "abc", "the user's text should not be silently rewritten"
    full = float(sum(range(20)))
    assert np.allclose(v.imv.getImageItem().image, full)
    assert np.allclose(v.current_plane(), full)


def test_clamp_z_range_helper_is_the_single_definition(channel_cube):
    """update_slice_info used to repeat the half-clamped arithmetic independently."""
    v = channel_cube
    assert v.clamp_z_range(10, 3) == (3, 10)
    assert v.clamp_z_range(-4, 999) == (0, 19)
    assert v.clamp_z_range(5, 5) == (5, 5)

    v.radio_range.setChecked(True)
    v.txt_zmin.setText("12")
    v.txt_zmax.setText("2")
    v.update_slice_info()
    assert "Collapsed: 2-12" in v.lbl_slice_info.text()


# --- B16: all-invalid planes ------------------------------------------------------

@pytest.mark.parametrize("scale", SCALE_MODES)
def test_all_nan_image_does_not_raise(qapp, scale):
    """B16: pyqtgraph derives its autorange from nanmin/nanmax and raised
    'Cannot set range [nan, nan]' for a plane with no finite pixels."""
    viewer = ImageViewer()
    viewer.combo_scale.setCurrentText(scale)
    viewer.set_data(np.full((32, 16), np.nan, dtype=np.float32))
    viewer.update_image_display()

    render = viewer.imv.getImageItem().image
    assert np.all(np.isfinite(render)), "an all-invalid plane must not reach pyqtgraph"
    assert viewer._plane_all_invalid is True
    assert "no valid data" in viewer.lbl_slice_info.text()


def test_all_nan_cube_does_not_raise(qapp):
    """Same for a cube where every channel is dead."""
    viewer = ImageViewer()
    viewer.set_data(np.full((10, 12, 14), np.nan, dtype=np.float32))
    assert np.all(np.isfinite(viewer.imv.getImageItem().image))
    assert "no valid data" in viewer.lbl_slice_info.text()


def test_dead_channel_is_flagged_only_while_displayed(qapp):
    """The marker has to track slice changes: paging onto a dead channel does not
    redisplay the image, it only moves the ImageView index."""
    np.random.seed(0)
    cube = np.random.rand(14, 12, 10).astype(np.float32)   # numpy (z, y, x), no header
    cube[5] = np.nan                # wavelength channel 5 is dead

    viewer = ImageViewer()
    viewer.set_data(cube)

    viewer.slider_slice.setValue(4)
    assert "no valid data" not in viewer.lbl_slice_info.text()
    viewer.slider_slice.setValue(5)
    assert "no valid data" in viewer.lbl_slice_info.text()
    viewer.slider_slice.setValue(6)
    assert "no valid data" not in viewer.lbl_slice_info.text()

    # and via a timeline drag
    viewer.imv.setCurrentIndex(5)
    assert "no valid data" in viewer.lbl_slice_info.text()
    viewer.imv.setCurrentIndex(6)
    assert "no valid data" not in viewer.lbl_slice_info.text()


def test_non_finite_manual_levels_do_not_blank_the_display(qapp):
    """float('nan') parses happily from the Min/Max boxes and poisoned every scale mode."""
    np.random.seed(0)
    viewer = ImageViewer()
    viewer.set_data(np.random.rand(32, 16).astype(np.float32))

    viewer.txt_min.setText("nan")
    viewer.txt_max.setText("inf")
    viewer.update_image_display(use_manual_levels=True)

    render = viewer.imv.getImageItem().image
    assert np.all(np.isfinite(render))
    assert viewer._plane_all_invalid is False


# --- B17: cuts must follow the displayed plane ------------------------------------

@pytest.mark.parametrize("method,expected", [
    ("Sum", 30.0),        # 4+5+6+7+8
    ("Mean", 6.0),
    ("Median", 6.0),
])
def test_depth_plot_cuts_use_the_collapsed_plane(channel_cube, method, expected):
    """B17: the cut branches indexed a single channel of the always-3D cube, so with a
    collapse displayed they plotted data that is not on screen. Sum is the case that
    exposes it -- for Mean/Median of a linear cube the midpoint channel coincides."""
    from pyql3.gui.tools.depth_plot import DepthPlotDialog

    v = channel_cube
    v.slider_slice.setValue(3)                 # leave the ImageView index at 3
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.combo_collapse.setCurrentText(method)
    v.radio_range.setChecked(True)
    v.z_mode_changed()

    on_screen = float(np.nanmean(v.imv.getImageItem().image))
    assert np.isclose(on_screen, expected)

    dp = DepthPlotDialog(None, v)
    # These assert how a *cut* aggregates across the ROI width. The `calc using`
    # combo is shared with the Depth Plot, whose default is now Total, so say which
    # one is meant rather than inheriting it.
    dp.combo_calc.setCurrentText("Average")
    try:
        dp.roi.setPos(2, 2)
        dp.roi.setSize([6, 6])
        for cut in ("Horizontal Cut", "Vertical Cut"):
            dp.combo_type.setCurrentText(cut)
            dp.update_plot()
            _, ydata = dp.plot_data.getData()
            assert ydata is not None and len(ydata) > 0
            assert np.allclose(ydata, expected), \
                f"{cut} plotted {np.unique(ydata)}, screen shows {expected}"
    finally:
        dp.close()


def test_depth_plot_cuts_follow_boxcar(channel_cube):
    """Boxcar renders a median over a window; the cuts must match that, not one channel."""
    from pyql3.gui.tools.depth_plot import DepthPlotDialog

    v = channel_cube
    v.txt_boxcar.setText("5")
    v.slider_slice.setValue(9)                 # median of channels 7..11 == 9
    assert np.isclose(float(np.nanmean(v.imv.getImageItem().image)), 9.0)

    dp = DepthPlotDialog(None, v)
    # These assert how a *cut* aggregates across the ROI width. The `calc using`
    # combo is shared with the Depth Plot, whose default is now Total, so say which
    # one is meant rather than inheriting it.
    dp.combo_calc.setCurrentText("Average")
    try:
        dp.roi.setPos(2, 2)
        dp.roi.setSize([6, 6])
        dp.combo_type.setCurrentText("Horizontal Cut")
        dp.update_plot()
        _, ydata = dp.plot_data.getData()
        assert np.allclose(ydata, 9.0), f"boxcar cut plotted {np.unique(ydata)}, expected 9.0"
    finally:
        dp.close()


def test_depth_plot_cuts_still_follow_a_single_slice(channel_cube):
    """Plain Z Slice mode must be unchanged: the cut is that channel."""
    from pyql3.gui.tools.depth_plot import DepthPlotDialog

    v = channel_cube
    v.txt_boxcar.setText("1")
    v.slider_slice.setValue(13)

    dp = DepthPlotDialog(None, v)
    # These assert how a *cut* aggregates across the ROI width. The `calc using`
    # combo is shared with the Depth Plot, whose default is now Total, so say which
    # one is meant rather than inheriting it.
    dp.combo_calc.setCurrentText("Average")
    try:
        dp.roi.setPos(2, 2)
        dp.roi.setSize([6, 6])
        dp.combo_type.setCurrentText("Vertical Cut")
        dp.update_plot()
        _, ydata = dp.plot_data.getData()
        assert np.allclose(ydata, 13.0)
    finally:
        dp.close()


def test_current_plane_matches_display_data_without_the_dn_multiplier(channel_cube):
    """current_plane() feeds callers that apply data_multiplier themselves, so it must
    return unscaled values in display orientation."""
    v = channel_cube
    v.slider_slice.setValue(7)

    plane = v.current_plane()
    assert plane.ndim == 2
    assert np.allclose(plane, 7.0)
    assert plane.shape == v.display_data.shape[1:]

    v._itime_coadds = 10.0
    v.disp_as_dn = True
    v.refresh_display()
    assert np.allclose(v.current_plane(), 7.0), "current_plane must not include the multiplier"
    assert np.allclose(v.display_data[v.current_z()], 70.0), "display_data should include it"


@pytest.mark.parametrize("rot,flip", [(0, False), (90, False), (180, True), (270, True)])
def test_current_plane_orientation_matches_display(channel_cube, rot, flip):
    """current_plane() and display_data must agree pixel for pixel under rotation/flip,
    since tools index it with display coordinates."""
    v = channel_cube
    # break the per-channel symmetry so orientation errors cannot hide
    v.raw_data[0, 0, :] = 99.0
    v.rot_angle = rot
    v.flip = flip
    v.apply_axis_mapping()
    v.slider_slice.setValue(5)

    assert np.allclose(v.current_plane(), v.display_data[v.current_z()])


# --- R5 / R27: a redisplay keeps the plane the z mode describes ----------------------

def _rendered(viewer):
    """The array the ImageItem is painting (Linear scale, so the values themselves)."""
    return np.asarray(viewer.imv.getImageItem().image)


def _flip(v):
    v.flip = True                     # what Rotate Image > Flip Horizontal does
    v.refresh_display()


def _rotate_90(v):
    v.rot_angle = 90                  # what a Rotate Image 90° step does
    v.refresh_display()


def _total_dn(v):
    v._itime_coadds = 10.0            # what Display > As Total DN does
    v.disp_as_dn = True
    v.refresh_display()


def _swap_spatial_axes(v):
    # X and Y exchanged; Z stays on the wavelength axis, so its length does not change
    for combo, axis in ((v.combo_x, "AXIS 2"), (v.combo_y, "AXIS 3")):
        combo.blockSignals(True)
        combo.setCurrentText(axis)
        combo.blockSignals(False)
    v.on_axis_changed()


def _reload_same_cube(v):
    v.set_data(v.raw_data, v.header)  # what Redisplay and a poller auto-load do


REDISPLAYS = [
    pytest.param(_flip, id="flip"),
    pytest.param(_rotate_90, id="rotate-90"),
    pytest.param(_total_dn, id="total-dn"),
    pytest.param(_swap_spatial_axes, id="axis-change"),
    pytest.param(_reload_same_cube, id="reload"),
]


@pytest.mark.parametrize("redisplay", REDISPLAYS)
def test_redisplay_keeps_the_collapsed_plane(channel_cube, redisplay):
    """R5: refresh_display always put the full cube back on screen, so in Z Range mode the
    image became channel 0 while the label and current_plane() still described the collapse."""
    v = channel_cube
    v.combo_collapse.setCurrentText("Sum")
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.radio_range.setChecked(True)
    assert np.allclose(_rendered(v), 30.0)

    redisplay(v)

    mult = v.data_multiplier
    assert np.allclose(_rendered(v), v.current_plane() * mult), "screen and current_plane() disagree"
    assert np.allclose(_rendered(v), 30.0 * mult), "the collapse 4+5+6+7+8 is no longer shown"
    assert (v.txt_zmin.text(), v.txt_zmax.text()) == ("4", "8")
    assert "Collapsed: 4-8" in v.lbl_slice_info.text()


@pytest.mark.parametrize("redisplay", REDISPLAYS)
def test_redisplay_keeps_the_boxcar_plane(channel_cube, redisplay):
    """R5, Boxcar: a redisplay showed the slider's single channel instead of the median."""
    v = channel_cube
    nra, ndec, nz = v.raw_data.shape
    data = np.zeros((nra, ndec, nz), dtype=np.float32)
    for k in range(nz):
        data[:, :, k] = (5 * k) % 13      # non-monotonic: channels 8-12 hold 1, 6, 11, 3, 8
    v.set_data(data, v.header)
    v.txt_boxcar.setText("5")
    v.slider_slice.setValue(10)
    assert np.allclose(_rendered(v), 6.0), "boxcar median over channels 8-12"

    redisplay(v)

    mult = v.data_multiplier
    assert np.allclose(_rendered(v), v.current_plane() * mult), "screen and current_plane() disagree"
    assert np.allclose(_rendered(v), 6.0 * mult), "channel 10 alone (11) replaced the boxcar median"
    assert v.slider_slice.value() == 10


def test_reload_keeps_zmax_when_the_length_is_unchanged(channel_cube):
    """R5: every set_data wrote the last channel into Z Max, so a reload in Z Range mode
    widened the collapse from 4-8 to 4-19."""
    v = channel_cube
    v.combo_collapse.setCurrentText("Sum")
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.radio_range.setChecked(True)

    v.set_data(v.raw_data, v.header)

    assert v.txt_zmax.text() == "8"
    assert "Collapsed: 4-8" in v.lbl_slice_info.text()
    assert np.allclose(v.current_plane(), 30.0)

    # A cube of a different length still gets the full range
    v.set_data(v.raw_data[:, :, :12].copy(), v.header)
    assert v.txt_zmax.text() == "11"


def _boxcar_cube(v):
    """Replace the channel cube by one whose channels are not monotonic, so a boxcar median
    never equals the slider's own channel: channels 8-12 hold 1, 6, 11, 3, 8."""
    nra, ndec, nz = v.raw_data.shape
    data = np.zeros((nra, ndec, nz), dtype=np.float32)
    for k in range(nz):
        data[:, :, k] = (5 * k) % 13
    v.set_data(data, v.header)


def _boxcar_median(v):
    zmin, zmax = v.boxcar_range(v.slider_slice.value())
    return float(np.median(v.raw_data[0, 0, zmin:zmax + 1]))


@pytest.mark.parametrize("mode", ["range", "boxcar"])
def test_frame_keys_do_not_replace_a_collapse(channel_cube, qapp, mode):
    """R27: the ImageView keeps the full cube behind a collapsed plane, and its frame keys
    repaint a channel from it while the label still reads "Collapsed: 4-8".

    Boxcar mode had the same fault (found in the Phase 2 review): the ImageView's index
    stayed at 0 while the slider moved, so Home painted raw channel 0 under the label
    "Slice (Boxcar): 8-12", and Right jumped the slider from 10 to 1. There the frame keys
    move the slider, which draws the median for the new centre."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    v = channel_cube
    v.resize(800, 800)
    v.show()
    qapp.processEvents()
    if mode == "range":
        v.combo_collapse.setCurrentText("Sum")
        v.txt_zmin.setText("4")
        v.txt_zmax.setText("8")
        v.radio_range.setChecked(True)
        qapp.processEvents()
        assert np.allclose(_rendered(v), 30.0)
    else:
        _boxcar_cube(v)
        v.txt_boxcar.setText("5")
        v.btn_apply_slice.click()
        v.slider_slice.setValue(10)
        qapp.processEvents()
        assert np.allclose(_rendered(v), 6.0), "boxcar median over channels 8-12"
        assert v.imv.currentIndex == 10, "the ImageView's frame was left behind the slider"

    view = v.imv.ui.graphicsView
    view.setFocus()
    if mode == "range":
        # Home comes first: the ImageView is already at frame 0, so it repaints channel 0
        # without moving its timeline, and no sigTimeChanged follows
        for key in (Qt.Key_Home, Qt.Key_End, Qt.Key_Home, Qt.Key_Right, Qt.Key_Left, Qt.Key_Space):
            QTest.keyClick(view, key)
            qapp.processEvents()
            assert np.allclose(_rendered(v), 30.0), f"{key.name} replaced the collapse with a channel"
            assert "Collapsed: 4-8" in v.lbl_slice_info.text()
        assert v.slider_slice.value() == 0, "a frame key moved the disabled Z Slice slider"

        # Playback started in Z Slice mode keeps stepping the ImageView after the switch
        v.imv.setCurrentIndex(15)
        assert np.allclose(_rendered(v), 30.0), "a playback frame replaced the collapse"
    else:
        last = v.slider_slice.maximum()
        page = v.slider_slice.pageStep()
        steps = [
            (Qt.Key_Right, 11), (Qt.Key_Left, 10), (Qt.Key_Home, 0), (Qt.Key_Home, 0),
            (Qt.Key_Left, 0), (Qt.Key_End, last), (Qt.Key_Right, last),
            (Qt.Key_PageUp, last - page), (Qt.Key_PageDown, last), (Qt.Key_Home, 0),
            (Qt.Key_PageDown, page),
        ]
        for key, expected in steps:
            QTest.keyClick(view, key)
            qapp.processEvents()
            assert v.slider_slice.value() == expected, f"{key.name} moved the slider wrongly"
            assert np.allclose(_rendered(v), _boxcar_median(v)), \
                f"{key.name} replaced the boxcar median with a channel"
            assert np.allclose(_rendered(v), v.current_plane())
            zmin, zmax = v.boxcar_range()
            assert f"{zmin}-{zmax}" in v.lbl_slice_info.text()
    v.close()


@pytest.mark.parametrize("start,rate", [
    pytest.param("last", 10, id="forward-at-the-last-frame"),
    pytest.param("first", -10, id="backward-at-the-first-frame"),
])
def test_boxcar_playback_at_the_end_keeps_the_median(channel_cube, start, rate):
    """pyqtgraph's playback does not stop at the end of the cube: each tick there sets the
    frame it is already on, which repaints that raw channel and emits no sigTimeChanged, so in
    Boxcar mode the median was replaced by channel 19 (4.0 instead of 7.0)."""
    v = channel_cube
    _boxcar_cube(v)
    v.txt_boxcar.setText("5")
    v.btn_apply_slice.click()
    edge = v.slider_slice.maximum() if start == "last" else v.slider_slice.minimum()
    v.slider_slice.setValue(edge)
    expected = _boxcar_median(v)
    assert np.allclose(_rendered(v), expected)

    v.imv.play(rate)
    for _ in range(3):
        v.imv.lastPlayTime -= 1.0          # a tick that is due, so pyqtgraph steps a frame
        v.imv.playTimer.timeout.emit()
        assert np.allclose(_rendered(v), expected), "a playback tick painted a raw channel"

    assert v.slider_slice.value() == edge
    assert not v.imv.playTimer.isActive(), "playback kept running at the end of the cube"


def test_switching_to_a_collapse_stops_playback(channel_cube):
    """Playback started in Z Slice mode kept running after the switch to Z Range, and Space,
    which would pause it, is ignored in that mode. Each frame re-ran apply_z_range()."""
    v = channel_cube
    v.imv.play(10)
    assert v.imv.playTimer.isActive()

    v.radio_range.setChecked(True)

    assert v.imv.playRate == 0
    assert not v.imv.playTimer.isActive()


def test_a_key_released_after_switching_to_a_collapse_is_not_left_pressed(channel_cube, qapp):
    """An arrow pressed in Z Slice mode and released in Z Range mode: if the release is
    ignored, pyqtgraph keeps the key in keysPressed, and a later key in Z Slice mode
    resumes playback in that direction."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    v = channel_cube
    v.resize(800, 800)
    v.show()
    qapp.processEvents()
    view = v.imv.ui.graphicsView
    view.setFocus()
    QTest.keyPress(view, Qt.Key_Right)
    qapp.processEvents()
    assert Qt.Key_Right in v.imv.keysPressed

    v.combo_collapse.setCurrentText("Sum")
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.radio_range.setChecked(True)
    QTest.keyRelease(view, Qt.Key_Right)
    qapp.processEvents()

    assert v.imv.keysPressed == {}
    assert v.imv.playRate == 0
    assert np.allclose(_rendered(v), 30.0)
    v.close()


@pytest.mark.parametrize("mode", ["range", "boxcar"])
def test_a_collapse_keeps_the_timeline_of_the_new_cube(channel_cube, qapp, mode):
    """R5's fix draws the collapse without imv.setImage, so the ImageView has to be handed
    the new cube separately: after a 2-D image its timeline would stay hidden, and after a
    cube of another length it would keep the old frame count."""
    v = channel_cube
    v.resize(800, 800)
    v.show()
    if mode == "range":
        v.txt_zmin.setText("4")
        v.txt_zmax.setText("8")
        v.radio_range.setChecked(True)
    else:
        v.txt_boxcar.setText("5")
        v.slider_slice.setValue(10)
    nra, ndec, _ = v.raw_data.shape
    header = v.header       # the 2-D load below replaces v.header
    cube = np.zeros((nra, ndec, 30), dtype=np.float32)
    for k in range(30):
        cube[:, :, k] = (5 * k) % 13      # non-monotonic, so no single channel equals a collapse

    v.set_data(np.ones((nra, ndec), dtype=np.float32))
    v.set_data(cube, header)
    qapp.processEvents()

    assert v.imv.ui.roiPlot.isVisible(), "no timeline after the 2-D image"
    assert v.imv.nframes() == 30
    assert np.allclose(_rendered(v), v.current_plane())
    v.close()


def test_osiris_default_axis_selection_on_real_data(qapp, real_osiris_fits):
    """ImageViewer must default an OSIRIS cube to RA/Dec on screen, wavelength on Z.

    set_data() takes the default axes from `header_info.default_axes`: the celestial
    axes go on screen, longitude as X, so OSIRIS (wavelength, Dec, RA) gets X = AXIS 3.
    The synthetic fixtures encode the same layout, so this real-data check is the only
    guard against that choice regressing for actual instrument headers.

    Shapes are derived from the file's NAXIS cards; no cube-specific constants.
    """
    if real_osiris_fits is None:
        pytest.skip("Set PYQL3_TEST_CUBE to a real OSIRIS cube to run this test")

    reader = FitsReader(real_osiris_fits)
    try:
        header = reader.header
        if 'WAVE' not in str(header.get('CTYPE1', '')).upper():
            pytest.skip("PYQL3_TEST_CUBE is not an OSIRIS-ordered cube (CTYPE1 != WAVE)")

        nwave, ndec, nra = header['NAXIS1'], header['NAXIS2'], header['NAXIS3']

        viewer = ImageViewer()
        viewer.set_data(reader.data, header)

        # Wavelength is FITS axis 1, so it must land on Z, leaving RA/Dec on screen.
        assert viewer.combo_x.currentText() == "AXIS 3", "RA (FITS axis 3) should be X"
        assert viewer.combo_y.currentText() == "AXIS 2", "Dec (FITS axis 2) should be Y"
        assert viewer.current_z_axis == "AXIS 1", "Wavelength (FITS axis 1) should be Z"

        # Wavelength was recognised as the spectral axis (drives the top ROI axis).
        assert viewer.wcs_z_idx == 0

        # raw_data is the untouched FITS array; transposed_data is (Z, X, Y).
        assert viewer.raw_data.shape == (nra, ndec, nwave)
        assert viewer.transposed_data.shape == (nwave, nra, ndec)
        assert viewer.slider_slice.maximum() == nwave - 1, "Z slider must span all channels"

        # The CRITICAL rule: reordering for display must not disturb raw_data.
        assert np.array_equal(viewer.raw_data, reader.data)
    finally:
        reader.close()


def _hover_centre(viewer):
    """Drive `mouse_moved` over the middle of the frame and return the WCS readout."""
    x = viewer.display_data.shape[1] // 2
    y = viewer.display_data.shape[2] // 2
    scene_pos = viewer.imv.getView().mapViewToScene(pg.Point(x + 0.5, y + 0.5))
    viewer.mouse_moved([scene_pos])
    return viewer.lbl_wcs.text()


def test_a_wcs_that_returns_nan_reads_out_as_not_available(loaded_viewer):
    """A WCS that has lost its keywords returns NaN instead of raising.

    Handing that to `Angle.to_string()` put the literal `RA: nan  |  DEC: nan` in the
    readout, and on x86 made numpy report `invalid value encountered in do_format
    (vectorized)` -- once per mouse-move event, so dragging a tool's ROI across the
    image flooded stderr.
    """
    loaded_viewer.wcs.pixel_to_world_values = \
        lambda *args, **kwargs: (np.float64(2.2e-6), np.float64(np.nan), np.float64(np.nan))

    assert _hover_centre(loaded_viewer) == "WCS: N/A"


def test_a_two_axis_wcs_that_returns_nan_reads_out_as_not_available(qapp):
    """The 2D branch of the readout formats a SkyCoord directly, and needs the same guard.

    On a 2-D image: a cube shows FITS axis 3, which a two-axis WCS does not have, so the
    readout there says N/A before it reaches the SkyCoord (R52).
    """
    from astropy.coordinates import SkyCoord
    import astropy.units as u

    viewer = _encoded_viewer()
    viewer.wcs = SimpleNamespace(
        naxis=2,
        pixel_to_world=lambda *a, **k: SkyCoord(ra=np.nan * u.deg, dec=np.nan * u.deg),
    )
    viewer.lbl_wcs.setText("WCS: stale")

    _hover_item(viewer, 5.5, 5.5)

    assert viewer.lbl_wcs.text() == "WCS: N/A"


def test_a_finite_wcs_still_reads_out(loaded_viewer):
    """The guard must not swallow the ordinary case."""
    text = _hover_centre(loaded_viewer)
    assert "N/A" not in text and "nan" not in text, text


def test_total_dn_uses_truitime_when_itime_is_milliseconds(qapp, osiris_like_fits):
    """ITIME is 900000 (ms) in current OSIRIS cubes; the integration time in seconds is TRUITIME."""
    reader = FitsReader(osiris_like_fits)
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)
    viewer.disp_as_dn = True
    assert viewer.data_multiplier == pytest.approx(899.92)


def test_real_osiris_cube_total_dn_uses_truitime(qapp, real_osiris_fits):
    if real_osiris_fits is None:
        pytest.skip("PYQL3_TEST_CUBE not set")
    reader = FitsReader(real_osiris_fits)
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)
    viewer.disp_as_dn = True
    header = reader.header
    assert viewer.data_multiplier == pytest.approx(header['TRUITIME'] * header.get('COADDS', 1))


# --- plane_changed / view_rotation_changed, and the view-rotation pivot (R67) ----------

def _step_slider(v):
    v.slider_slice.setValue(5)


def _change_boxcar(v):
    v.txt_boxcar.setText("3")
    v.btn_apply_slice.click()


def _apply_z_range(v):
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.btn_apply_range.click()


def _set_data_2d(v):
    v.set_data(np.arange(12 * 16, dtype=np.float32).reshape(12, 16))


def _set_data_3d_shorter(v):
    v.set_data(v.raw_data[:, :, :10], v.header)


def _z_range_mode(v):
    v.radio_range.setChecked(True)


def _last_channel(v):
    # The slider is then clamped by a shorter cube, which runs on_slider_changed inside
    # set_data before the plane is redrawn
    v.slider_slice.setValue(19)


PLANE_CHANGES = [
    pytest.param(None, _step_slider, id="slider-step"),
    pytest.param(None, _change_boxcar, id="boxcar-change"),
    pytest.param(_z_range_mode, _apply_z_range, id="z-range-apply"),
    pytest.param(None, _flip, id="flip"),
    pytest.param(_z_range_mode, _flip, id="flip-in-z-range"),
    pytest.param(None, _total_dn, id="unit-change"),
    pytest.param(None, _set_data_2d, id="set_data-2d"),
    pytest.param(_set_data_2d, _flip, id="flip-2d"),
    pytest.param(_set_data_2d, _total_dn, id="unit-change-2d"),
    pytest.param(None, _reload_same_cube, id="set_data-3d"),
    pytest.param(_last_channel, _set_data_3d_shorter, id="set_data-3d-shorter"),
]


@pytest.mark.parametrize("setup,change", PLANE_CHANGES)
def test_plane_changed_fires_once_per_change(channel_cube, setup, change):
    """Tools that follow the plane on screen redraw once per change, not zero or two times."""
    v = channel_cube
    if setup is not None:
        setup(v)
    fired = []
    v.plane_changed.connect(lambda: fired.append(1))

    change(v)

    assert len(fired) == 1


@pytest.mark.parametrize("change,expected", [
    pytest.param(_set_data_2d, ["plane", "display", "rotation"], id="set_data-2d"),
    pytest.param(_reload_same_cube, ["plane", "display", "rotation"], id="set_data-3d"),
    pytest.param(_flip, ["plane", "display"], id="flip"),
    pytest.param(_rotate_90, ["plane", "display"], id="rotate-90"),
    pytest.param(_total_dn, ["plane", "display"], id="unit-change"),
    pytest.param(_swap_spatial_axes, ["plane", "display"], id="axis-change"),
    pytest.param(_step_slider, ["plane"], id="slider-step"),
])
def test_viewer_signals_arrive_in_one_order(channel_cube, change, expected):
    """The contract consumers rely on: display_changed always comes with plane_changed and
    after it, and on set_data view_rotation_changed comes last. The 2-D set_data path used
    to emit display_changed first."""
    v = channel_cube
    seen = []
    v.plane_changed.connect(lambda: seen.append("plane"))
    v.display_changed.connect(lambda: seen.append("display"))
    v.view_rotation_changed.connect(lambda _angle: seen.append("rotation"))

    change(v)

    assert seen == expected


def test_an_invalid_z_range_keeps_the_view_rotation(channel_cube):
    """_give_imageview_the_cube() calls imv.setImage, which resets the ImageItem's transform.
    When apply_z_range then stops at "Collapsed: Invalid Range", nothing put the rotation
    back: after a flip the view was unrotated while view_rotation still read 30."""
    v = channel_cube
    v.radio_range.setChecked(True)
    v.apply_view_rotation(30.0)
    item = v.imv.getImageItem()
    before = item.transform()
    assert not before.isIdentity()

    v.txt_zmin.setText("abc")
    _flip(v)

    assert "Invalid Range" in v.lbl_slice_info.text()
    assert v.view_rotation == 30.0
    after = item.transform()
    assert not after.isIdentity(), "the view rotation was dropped"
    assert after == before


def test_view_rotation_changed_fires_with_the_angle(loaded_viewer):
    angles = []
    loaded_viewer.view_rotation_changed.connect(angles.append)

    loaded_viewer.apply_view_rotation(37)
    loaded_viewer.apply_view_rotation(0.0)

    assert angles == [37.0, 0.0]


def test_new_data_resets_the_view_rotation_once(loaded_viewer):
    """set_data puts the rotation back to zero through apply_view_rotation, so the ImageItem's
    transform and anyone listening are told, rather than only `view_rotation` being reset."""
    loaded_viewer.apply_view_rotation(37.0)
    angles = []
    loaded_viewer.view_rotation_changed.connect(angles.append)

    loaded_viewer.set_data(loaded_viewer.raw_data, loaded_viewer.header)

    assert angles == [0.0]
    assert loaded_viewer.view_rotation == 0.0
    assert loaded_viewer.imv.getImageItem().transform().isIdentity()


def test_new_data_is_unrotated_before_anyone_is_told(loaded_viewer):
    """A listener on display_changed or plane_changed during set_data sees the new plane
    unrotated. The old angle used to stay on the ImageItem and in `view_rotation` until the
    last line of set_data."""
    v = loaded_viewer
    v.apply_view_rotation(37.0)
    seen = []

    def record(signal_name):
        seen.append((signal_name, v.view_rotation, v.imv.getImageItem().transform().isIdentity()))

    v.display_changed.connect(lambda: record("display_changed"))
    v.plane_changed.connect(lambda: record("plane_changed"))

    v.set_data(v.raw_data, v.header)

    assert {name for name, _, _ in seen} == {"display_changed", "plane_changed"}
    assert all(angle == 0.0 and identity for _, angle, identity in seen), seen


@pytest.mark.parametrize("data", [
    pytest.param(None, id="no-data"),
])
def test_a_load_with_nothing_to_show_drops_the_old_rotation(loaded_viewer, data):
    """With nothing displayable there is no plane to pivot on, but the rotation of the
    previous one must still come off the ImageItem."""
    loaded_viewer.apply_view_rotation(37.0)

    loaded_viewer.set_data(data)

    assert loaded_viewer.view_rotation == 0.0
    assert loaded_viewer.imv.getImageItem().transform().isIdentity()


@pytest.fixture
def osiris_viewer(qapp, osiris_like_fits):
    reader = FitsReader(osiris_like_fits)
    viewer = ImageViewer()
    viewer.set_data(reader.data, reader.header)
    assert viewer.display_data.shape[-2:] == (66, 51)
    return viewer


@pytest.mark.parametrize("angle", [0.0, 37.0, 90.0, 180.0])
def test_item_to_view_round_trips_under_a_view_rotation(osiris_viewer, angle):
    v = osiris_viewer
    v.apply_view_rotation(angle)
    points = [(0.0, 0.0), (66.0, 51.0), (10.5, 40.25), (60.0, 3.0)]

    for x, y in points:
        assert v.view_to_item(*v.item_to_view(x, y)) == pytest.approx((x, y), abs=1e-9)

    # A rotation keeps distances, and away from 0 degrees it does move a corner
    (ax, ay), (bx, by) = v.item_to_view(*points[0]), v.item_to_view(*points[1])
    assert np.hypot(bx - ax, by - ay) == pytest.approx(np.hypot(66.0, 51.0))
    if angle == 0.0:
        assert (ax, ay) == pytest.approx(points[0])
    else:
        assert (ax, ay) != pytest.approx(points[0])


@pytest.mark.parametrize("angle", [37.0, 180.0])
def test_view_rotation_pivots_on_the_plane_centre(osiris_viewer, angle):
    """R67: the pivot was ((shape[-1]-1)/2, (shape[-2]-1)/2), with x and y swapped and the
    half-pixel offset dropped. On a 51x66 plane a 180 degree rotation moved the centre from
    (25.5, 33.0) to (39.5, 17.0)."""
    v = osiris_viewer
    _rotate_90(v)
    assert v.display_data.shape[-2:] == (51, 66)
    before = v.item_to_view(25.5, 33.0)

    v.apply_view_rotation(angle)

    assert v.item_to_view(25.5, 33.0) == pytest.approx(before, abs=1e-9)


# --- R6: the hover readout maps the cursor through the ImageItem ---------------------

def _encoded_viewer(header=None):
    """A 40 x 30 image whose value is 1000*x + y, so a readout names its own pixel."""
    nx, ny = 40, 30
    img = (1000 * np.arange(nx)[None, :] + np.arange(ny)[:, None]).astype(np.float32)
    viewer = ImageViewer()
    viewer.resize(800, 800)
    viewer.set_data(img, header)
    return viewer


def _hover_item(viewer, ix, iy):
    """Put the cursor over ImageItem coordinate (ix, iy) and return the label texts."""
    scene_pos = viewer.imv.getImageItem().mapToScene(pg.QtCore.QPointF(ix, iy))
    viewer.mouse_moved((scene_pos,))
    return viewer.lbl_x.text(), viewer.lbl_y.text(), viewer.lbl_val.text()


@pytest.mark.parametrize("view_rotation", [0.0, 37.0, 90.0, 180.0])
def test_readout_follows_the_view_rotation(qapp, view_rotation):
    """R6: the view rotation is a QTransform on the ImageItem, so mapping the cursor through
    the ViewBox read a different pixel as soon as a view angle (or North Up) was set."""
    viewer = _encoded_viewer()
    viewer.apply_view_rotation(view_rotation)

    x, y, value = _hover_item(viewer, 5.5, 20.5)

    assert (x, y) == ("X: 5", "Y: 20")
    assert value.startswith("Value: 5020 ")


@pytest.mark.parametrize("view_rotation", [0.0, 37.0])
@pytest.mark.parametrize("rot_angle", [0, 90])
@pytest.mark.parametrize("flip", [False, True])
def test_readout_pixel_and_sky_position_agree_under_flip_and_rotation(
        qapp, flip, rot_angle, view_rotation):
    """R6: whatever the orientation, the readout names the display pixel under the cursor, its
    value is that display pixel's, and the WCS line is the sky position of the same pixel
    taken back to `transposed_data` indices with `display_to_orig`."""
    from astropy.io import fits
    header = fits.Header()
    for key, val in (("CTYPE1", "RA---TAN"), ("CTYPE2", "DEC--TAN"),
                     ("CRPIX1", 20.0), ("CRPIX2", 15.0),
                     ("CRVAL1", 150.0), ("CRVAL2", 2.0),
                     ("CDELT1", -0.001), ("CDELT2", 0.001)):
        header[key] = val
    viewer = _encoded_viewer(header)
    viewer.flip = flip
    viewer.rot_angle = rot_angle
    viewer.refresh_display()
    viewer.apply_view_rotation(view_rotation)

    dx, dy = 5, 7      # inside the displayed plane for both orientations
    x, y, value = _hover_item(viewer, dx + 0.5, dy + 0.5)

    assert (x, y) == (f"X: {dx}", f"Y: {dy}")
    assert value.startswith(f"Value: {viewer.display_data[dx, dy]:.5g} ")
    ox, oy = viewer.display_to_orig(dx, dy)
    world = viewer.wcs.pixel_to_world(ox, oy)
    assert viewer.lbl_wcs.text() == (
        f"WCS: {world.ra.to_string(unit='hour', sep='hms', precision=3)}  "
        f"{world.dec.to_string(unit='deg', sep='dms', precision=2)}")


def test_readout_left_of_the_image_is_out_of_bounds(qapp):
    """R6: `int()` rounds toward zero, so every x in (-1, 0) read as pixel 0 and passed the
    bounds check."""
    viewer = _encoded_viewer()

    _, _, value = _hover_item(viewer, -0.6, 5.5)

    assert value == "Value: Out of Bounds"


# --- R13: set_data validates before it changes anything ------------------------------

def _unit_header(unit):
    from astropy.io import fits
    h = fits.Header()
    h['NAXIS'] = 3
    h['CTYPE1'], h['CUNIT1'], h['CRVAL1'], h['CDELT1'], h['CRPIX1'] = 'WAVE', unit, 2.0, 0.001, 1
    h['CTYPE2'], h['CUNIT2'], h['CRVAL2'], h['CDELT2'], h['CRPIX2'] = 'DEC--TAN', 'deg', -29.0, 1e-5, 5
    h['CTYPE3'], h['CUNIT3'], h['CRVAL3'], h['CDELT3'], h['CRPIX3'] = 'RA---TAN', 'deg', 266.4, -1e-5, 5
    return h


def test_micron_cunit_loads_with_a_wavelength_axis(qapp):
    viewer = ImageViewer()
    cube = np.full((10, 10, 20), 7.0)

    viewer.set_data(cube, _unit_header('micron'))

    assert viewer.raw_data is cube
    assert viewer.wcs is not None
    assert viewer.wcs_z_idx is not None
    assert viewer.wcs.wcs.ctype[viewer.wcs_z_idx] == 'WAVE'


def test_unparseable_wcs_leaves_data_displayed_without_wcs(qapp):
    viewer = ImageViewer()
    viewer.set_data(np.ones((10, 10, 20)), _unit_header('um'))
    cube = np.full((10, 10, 20), 7.0)
    header = _unit_header('parsec')

    viewer.set_data(cube, header)

    assert viewer.wcs is None
    assert viewer.raw_data is cube
    assert viewer.header is header
    assert viewer.display_data.max() == 7.0
    assert viewer.lbl_wcs.text().startswith("WCS: unreadable (")


def test_the_unreadable_wcs_note_survives_a_hover_off_the_image(qapp):
    """Hovering a pixel, or a point beside the image, writes the readout again; for a file
    whose WCS could not be read it must still say why, not "WCS: N/A"."""
    viewer = ImageViewer()
    viewer.resize(800, 800)
    viewer.set_data(np.ones((10, 10, 20)), _unit_header('parsec'))
    note = viewer.lbl_wcs.text()
    assert note.startswith("WCS: unreadable (")

    _, _, value = _hover_item(viewer, 5.5, 5.5)            # on the image
    assert value.startswith("Value: 1 ")
    assert viewer.lbl_wcs.text() == note
    _, _, value = _hover_item(viewer, -0.5, 5.5)           # beside it
    assert value == "Value: Out of Bounds"
    assert viewer.lbl_wcs.text() == note

    viewer.set_data(np.ones((10, 10, 20)), None)
    _hover_item(viewer, 5.5, 5.5)
    assert viewer.lbl_wcs.text() == "WCS: N/A"


def test_an_unreadable_wcs_note_is_one_line(qapp):
    """wcslib's message has a first line naming its own source file
    (`ERROR 6 in wcs_units() at line 3322 of file cextern/wcslib/C/wcs.c:`), which made
    `lbl_wcs` three times its height. The label shows the last line, which says what is
    wrong; the tooltip holds the whole message."""
    viewer = ImageViewer()
    viewer.set_data(np.ones((10, 10, 20)), _unit_header('parsec'))

    assert viewer.lbl_wcs.text() == \
        "WCS: unreadable (In CUNIT1 : Invalid symbol in INITIAL context in 'parsec'.)"
    assert "ERROR 6 in wcs_units()" in viewer.lbl_wcs.toolTip()
    assert "'parsec'" in viewer.lbl_wcs.toolTip()

    viewer.set_data(np.ones((10, 10, 20)), _unit_header('um'))
    assert viewer.lbl_wcs.toolTip() == ""


@pytest.mark.parametrize("shape", [(1, 6, 5, 5), (6,)], ids=["4-D", "1-D"])
def test_a_refused_array_leaves_the_viewer_unchanged(loaded_viewer, shape):
    viewer = loaded_viewer
    before = (viewer.raw_data, viewer.header, viewer.wcs, viewer.display_data,
              viewer._itime_coadds)
    viewer.apply_view_rotation(90.0)

    with pytest.raises(ValueError):
        viewer.set_data(np.zeros(shape, dtype=np.float32), _unit_header('um'))

    after = (viewer.raw_data, viewer.header, viewer.wcs, viewer.display_data,
             viewer._itime_coadds)
    assert all(a is b for a, b in zip(before, after))
    assert viewer.view_rotation == 90.0
    assert not viewer.imv.getImageItem().transform().isIdentity()


# --- R24, R26, R52, R81, R82: axes found through the WCS ------------------------------

def _hover_wcs(viewer, x, y):
    """Hover ImageItem pixel (x, y) and return the WCS readout."""
    _hover_item(viewer, x + 0.5, y + 0.5)
    return viewer.lbl_wcs.text()


def _sky_text(world):
    """The RA and Dec of `world` as the 3-axis readout writes them."""
    return (f"RA: {world.ra.to_string(unit='hour', sep='hms', precision=3)}",
            f"DEC: {world.dec.to_string(unit='deg', sep='dms', precision=2)}")


def _sky_text_dec(dec_deg):
    from astropy.coordinates import Angle
    return f"DEC: {Angle(dec_deg, unit='deg').to_string(unit='deg', sep='dms', precision=2)}"


def _spectral_cube_viewer(spectral_ctype):
    """R24's cube: the spectral axis on FITS axis 1, Dec on 2, RA on 3 (OSIRIS order)."""
    from astropy.wcs import WCS
    w = WCS(naxis=3)
    w.wcs.ctype = [spectral_ctype, 'DEC--TAN', 'RA---TAN']
    w.wcs.crval = [2.2, 34.0, 242.0]
    w.wcs.cdelt = [0.0005, 1e-4, 1e-4]
    w.wcs.cunit = ['um', 'deg', 'deg']
    viewer = ImageViewer()
    viewer.resize(800, 800)
    viewer.set_data(np.random.default_rng(0).normal(size=(16, 12, 20)).astype(np.float32),
                    w.to_header())
    return viewer


def test_awav_is_a_spectral_axis(qapp):
    """R24: an air-wavelength axis is a wavelength axis, and the sky readout works on it."""
    viewer = _spectral_cube_viewer('AWAV')

    assert viewer.wcs_z_idx == 0
    assert viewer.lbl_slice_info.text() == "Slice: 0, Wavelength: 2.2005 µm"
    assert viewer.top_axis.isVisible(), "the timeline's wavelength axis"
    ox, oy = viewer.display_to_orig(3, 3)
    world = viewer.wcs.celestial.pixel_to_world(oy, ox)     # celestial order: Dec, RA axes
    ra, dec = _sky_text(world)
    assert _hover_wcs(viewer, 3, 3) == f"WCS: {ra}  |  {dec}"


def test_wcs_readout_never_shows_a_previous_position(qapp):
    """R24: with RA on Z, the readout kept the coordinates of the last pixel hovered before
    the axis change while X and Y moved on."""
    viewer = _spectral_cube_viewer('WAVE')
    before = _hover_wcs(viewer, 3, 3)
    assert "RA:" in before

    viewer.combo_x.setCurrentText("AXIS 1")      # wavelength on X, Dec on Y, RA on Z
    after = _hover_wcs(viewer, 15, 9)

    assert after != before
    world = viewer.wcs.pixel_to_world_values(15, 9, viewer.current_z())
    assert after == f"WCS: em.wl: {world[0]:.5g} m  |  {_sky_text_dec(world[1])}"


def test_duplicate_axis_choice_swaps_the_axes(qapp):
    """R26: picking for Y the axis X shows used to put the combo back but keep the stale
    choice in `current_y_axis`, so the display claimed FITS axes 1 and 1. The two now swap."""
    from astropy.wcs import WCS
    w = WCS(naxis=3)
    w.wcs.ctype = ['RA---TAN', 'DEC--TAN', 'WAVE']     # JWST NIRSpec order
    w.wcs.crval = [266.4, -29.0, 2.0e-6]
    w.wcs.cdelt = [-1e-5, 1e-5, 1e-9]
    w.wcs.crpix = [20, 15, 1]
    w.wcs.cunit = ['deg', 'deg', 'm']
    viewer = ImageViewer()
    viewer.set_data(np.zeros((25, 30, 40), np.float32), w.to_header())
    assert (viewer.combo_x.currentText(), viewer.combo_y.currentText()) == ("AXIS 1", "AXIS 2")
    planes = []
    viewer.plane_changed.connect(lambda: planes.append(1))

    viewer.combo_y.setCurrentText("AXIS 1")

    assert (viewer.combo_x.currentText(), viewer.combo_y.currentText()) == ("AXIS 2", "AXIS 1")
    assert (viewer.current_x_axis, viewer.current_y_axis) == ("AXIS 2", "AXIS 1")
    assert viewer.current_z_axis == "AXIS 3"
    assert viewer.display_axis_indices() == (1, 0)
    assert len(planes) == 1, "the swap must redraw the plane once"

    viewer.combo_x.setCurrentText("AXIS 1")       # and the other way round

    assert (viewer.combo_x.currentText(), viewer.combo_y.currentText()) == ("AXIS 1", "AXIS 2")
    assert viewer.display_axis_indices() == (0, 1)
    assert viewer.current_z_axis == "AXIS 3"
    assert len(planes) == 2


def _collapsed_cube_image_and_header():
    """R52: a collapsed cube saved with the cube's header, so its WCS has a third axis."""
    import warnings
    from astropy.io import fits
    from astropy.wcs import WCS
    w = WCS(naxis=3)
    w.wcs.ctype = ['RA---TAN', 'DEC--TAN', 'WAVE']
    w.wcs.crval = [266.4, -29.0, 2.0e-6]
    w.wcs.cdelt = [-1e-5, 1e-5, 1e-9]
    w.wcs.crpix = [20, 15, 1]
    w.wcs.cunit = ['deg', 'deg', 'm']
    img = np.zeros((30, 40), np.float32)
    with warnings.catch_warnings():
        warnings.simplefilter('ignore')
        header = fits.PrimaryHDU(img, header=w.to_header()).header
    assert header['NAXIS'] == 2 and header['CTYPE3'] == 'WAVE'
    return img, header, w


@pytest.mark.parametrize("cube_first", [False, True], ids=["fresh", "after a cube"])
def test_2d_image_with_a_third_wcs_axis_reads_sky(qapp, sample_3d_fits, cube_first):
    """R52: the readout chose its branch by `wcs.naxis`, so a 2-D image with a 3-axis WCS read
    "WCS: N/A", or after a cube a wavelength and a Dec from the wrong pixel axes."""
    img, header, w = _collapsed_cube_image_and_header()
    viewer = ImageViewer()
    viewer.resize(800, 800)
    if cube_first:
        reader = FitsReader(sample_3d_fits)
        viewer.set_data(reader.data, reader.header)
        reader.close()
        assert viewer.wcs_z_idx is not None

    viewer.set_data(img, header)

    assert viewer.wcs.naxis == 3
    assert viewer.wcs_z_idx is None
    ra, dec = _sky_text(w.celestial.pixel_to_world(10, 5))
    assert _hover_wcs(viewer, 10, 5) == f"WCS: {ra}  |  {dec}"


def test_a_nifs_cube_opens_with_wavelength_on_z(qapp, nifs_like_header):
    """R81: with no celestial axis types the cube used to get OSIRIS's layout, so the image
    was wavelength against y and the slider paged through x."""
    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 12, 10), dtype=np.float32), nifs_like_header)

    assert (viewer.current_x_axis, viewer.current_y_axis, viewer.current_z_axis) == \
        ("AXIS 1", "AXIS 2", "AXIS 3")
    assert viewer.current_plane().shape == (10, 12)


def test_an_iraf_spectral_axis_has_wavelengths(qapp, nifs_like_header):
    """R82: `WAT3_001 = '... axtype=wave'` names the spectral axis. With no unit stated the
    header's numbers are shown as they are and labelled Unknown; with one, they are µm."""
    viewer = ImageViewer()
    cube = np.zeros((40, 12, 10), dtype=np.float32)
    viewer.set_data(cube, nifs_like_header)

    assert viewer.wcs_z_idx == 2
    assert viewer.wavelength_unit() is None
    assert viewer.get_wavelength_for_slice(0) == pytest.approx(19968.66)
    assert "Wavelength (Unknown)" in viewer.lbl_slice_info.text()

    nifs_like_header['WAT3_001'] = 'wtype=linear axtype=wave units=angstroms'
    viewer.set_data(cube, nifs_like_header)

    assert viewer.get_wavelength_for_slice(0) == pytest.approx(1.99687, abs=1e-5)
    assert viewer.lbl_slice_info.text() == "Slice: 0, Wavelength: 1.9969 µm"


def test_an_iraf_axis_is_read_out_by_its_iraf_name(qapp, nifs_like_header):
    """NIFS's `LINEAR` axes have no physical type, so the readout said
    `WCS: None: 1.687   |  None: 1.55 `. The name comes from the IRAF `axtype` (xi, eta), and
    a unit the file does not state is left out rather than shown empty."""
    viewer = ImageViewer()
    viewer.resize(800, 800)
    viewer.set_data(np.zeros((40, 12, 10), dtype=np.float32), nifs_like_header)

    text = _hover_centre(viewer)

    assert "None" not in text
    assert text == text.strip()
    xi, eta = text.removeprefix("WCS: ").split("  |  ")
    assert xi == f"xi: {(5 + 1) * 0.05:.5g}"       # pixel 5, CRPIX 0, CD 0.05
    assert eta == f"eta: {(6 + 1) * 0.05:.5g}"

    # Without the WAT cards the name is the CTYPE
    for key in ('WAT1_001', 'WAT2_001'):
        del nifs_like_header[key]
    viewer.set_data(np.zeros((40, 12, 10), dtype=np.float32), nifs_like_header)
    assert _hover_centre(viewer) == f"WCS: LINEAR: {0.3:.5g}  |  LINEAR: {0.35:.5g}"


# --- R83: every unit label names the file's own unit ----------------------------------

@pytest.mark.parametrize("bunit,label", [("MJy/sr", "MJy/sr"), (None, "Unknown")])
def test_labels_use_the_declared_unit(qapp, bunit, label):
    """The colorbar, the hover readout and the cut plots said "DN/s" whatever the file declared
    (R83). They name the declared unit, and "Unknown" when there is none. Values in the
    thousands must not put an SI prefix on the unit name ("kUnknown")."""
    from astropy.io import fits
    from pyql3.gui.tools.cuts import CutPlotDialog
    header = fits.Header({'EXPTIME': 600.0})
    if bunit is not None:
        header['BUNIT'] = bunit
    viewer = ImageViewer()
    viewer.set_data(np.full((10, 12, 14), 5000.0, dtype=np.float32), header)
    viewer.show()
    viewer.toggle_colorbar(True)
    cut = CutPlotDialog('horizontal', None, viewer)
    try:
        assert viewer.value_unit_label() == label
        axis = viewer.imv.ui.histogram.axis
        assert axis.labelString().count(label) == 1
        assert f"k{label}" not in axis.labelString()
        assert cut.plot_widget.getAxis('left').labelText == f"Intensity ({label})"

        _hover_centre(viewer)
        # A bare "Unknown" after the number would read as part of it
        expected = "Value: 5000 (Unknown)" if bunit is None else f"Value: 5000 {label}"
        assert viewer.lbl_val.text() == expected
    finally:
        cut.close()
        viewer.close()


# --- R65 / R66 / R84: colorbar values, 1:1 zoom, unparsable Z fields -----------------

@pytest.mark.parametrize("scale", ["Linear", "Negative", "Logarithmic", "Sqrt", "AsinH", "HistEq"])
def test_colorbar_ticks_are_data_values_under_sqrt(qapp, scale):
    """R65: the colorbar showed the render values (0 to 99.0 under Sqrt for data 25 to 9825)
    beside a label that says "Pixel Value (...)". Its ticks are data values in every scale."""
    data = np.linspace(25.0, 9825.0, 64 * 32).reshape(64, 32).astype(np.float32)
    v = ImageViewer()
    v.set_data(data)
    v.txt_min.setText("25")
    v.txt_max.setText("9825")
    v.combo_scale.setCurrentText(scale)
    v.apply_contrast()
    low, high = v.imv.ui.histogram.getLevels()

    axis = v.imv.ui.histogram.axis
    shown = [float(s) for s in axis.tickStrings([low, high], 1.0, 1.0)]

    # Negative renders the negated data, so its colorbar runs from the largest value down
    expected = (9825.0, 25.0) if scale == "Negative" else (25.0, 9825.0)
    assert shown == pytest.approx(expected, rel=2e-3, abs=0.6)
    assert axis.labelText.startswith("Pixel Value")


@pytest.mark.parametrize("scale", ["Linear", "Sqrt"])
@pytest.mark.parametrize("low, high", [(1e-7, 5e-6), (2e6, 9e8)], ids=["small", "large"])
def test_colorbar_ticks_are_not_scaled_by_an_si_prefix(qapp, scale, low, high):
    """pyqtgraph appends "(x1e-06)" to the label and divides the ticks by it. The ticks here
    are data values already, so a prefix would make them read 1e6 times too small."""
    data = np.linspace(low, high, 64 * 32).reshape(64, 32)
    v = ImageViewer()
    v.set_data(data)
    v.txt_min.setText(repr(low))
    v.txt_max.setText(repr(high))
    v.combo_scale.setCurrentText(scale)
    v.apply_contrast()
    hist = v.imv.ui.histogram
    hist.axis.setRange(*hist.getLevels())      # the axis follows the colorbar's own range
    qapp.processEvents()
    axis = hist.axis

    assert "(x" not in axis.labelString()
    ticks = axis.tickValues(*axis.range, 400.0)
    values = [t for _spacing, group in ticks for t in group]
    scaled = axis.tickStrings(values, axis.autoSIPrefixScale, 1.0)
    assert axis.autoSIPrefixScale == 1.0
    shown = [float(t) for t in scaled if t]
    assert shown, "no tick was labelled"
    assert min(shown) >= low * 0.99 and max(shown) <= high * 1.01


def test_colorbar_ticks_outside_the_levels_are_blank(qapp):
    """The axis runs past the levels. A negative square root would read as a value above
    the minimum, and HistEq would repeat its end value there."""
    data = np.linspace(25.0, 9825.0, 64 * 32).reshape(64, 32).astype(np.float32)
    v = ImageViewer()
    v.set_data(data)
    axis = v.imv.ui.histogram.axis
    for scale in ("Sqrt", "HistEq"):
        v.combo_scale.setCurrentText(scale)
        low, high = v.imv.ui.histogram.getLevels()
        span = high - low
        assert axis.tickStrings([low - 0.1 * span, low, high, high + 0.1 * span], 1.0, 1.0)[0::3] \
            == ["", ""], scale
        assert all(axis.tickStrings([low, high], 1.0, 1.0))


def test_histeq_colorbar_labels_do_not_change_between_redraws(qapp):
    data = np.random.default_rng(1).random((300, 300)).astype(np.float32)   # over 50,000 pixels
    v = ImageViewer()
    v.set_data(data)
    v.combo_scale.setCurrentText("HistEq")
    axis = v.imv.ui.histogram.axis
    first = axis.tickStrings([0.25, 0.5, 0.75], 1.0, 1.0)
    v.update_image_display(use_manual_levels=True)

    assert axis.tickStrings([0.25, 0.5, 0.75], 1.0, 1.0) == first


def test_one_to_one_zoom_shows_one_data_pixel_per_screen_pixel(qapp):
    """R66: 1:1 set the range to the whole image, which is Fit. One data pixel per screen
    pixel means the view spans the ViewBox's width and height in data units."""
    data = np.random.default_rng(0).random((200, 300)).astype(np.float32)   # 300 wide, 200 high
    v = ImageViewer()
    v.resize(900, 900)
    v.show()
    v.set_data(data)
    qapp.processEvents()
    view = v.imv.getView()

    v.main_zoom_11()
    qapp.processEvents()

    rect = view.viewRect()
    assert rect.width() == pytest.approx(view.width(), abs=1.0)
    assert rect.height() == pytest.approx(view.height(), abs=1.0)
    assert rect.center().x() == pytest.approx(150.0, abs=1.0), "1:1 moved the view's centre"
    assert rect.center().y() == pytest.approx(100.0, abs=1.0)

    v.close()


def _reload_new_array(v):
    v.set_data(v.raw_data.copy(), v.header)


@pytest.mark.parametrize("redisplay", [pytest.param(_reload_new_array, id="reload"),
                                       pytest.param(_flip, id="flip")])
def test_an_unparsable_z_field_does_not_leave_a_stale_plane(qapp, redisplay):
    """R84: with Z Min unparsable, a redisplay in Z Range mode stopped after giving the
    ImageView the whole cube, so channel 0 stayed on screen at the old levels while
    `display_data` was the cube. The full range is collapsed, with a message."""
    nra, ndec, nz = 8, 6, 12
    cube = np.zeros((nz, ndec, nra), dtype=np.float32)
    for k in range(nz):
        cube[k] = k + 100.0
    v = ImageViewer()
    v.set_data(cube)
    v.combo_collapse.setCurrentText("Sum")
    v.txt_zmin.setText("4")
    v.txt_zmax.setText("8")
    v.radio_range.setChecked(True)
    assert np.allclose(_rendered(v), 530.0)

    v.txt_zmin.setText("abc")
    for _ in range(10):
        if redisplay is _flip:
            v.flip = not v.flip
        redisplay(v)

    full = float(sum(100 + k for k in range(nz)))
    assert np.allclose(_rendered(v), full), "the screen is not the full-range collapse"
    assert v.display_data.ndim == 2
    assert np.allclose(v.display_data, _rendered(v)), "display_data is not what is painted"
    assert np.allclose(v.current_plane(), _rendered(v)), "tools would read another plane"
    assert "Invalid Range" in v.lbl_slice_info.text()
    assert "0-11" in v.lbl_slice_info.text()
    assert v.txt_zmin.text() == "abc", "the user's text was rewritten"


def test_the_z_fields_refuse_letters_when_typed(qapp):
    """R84: a validator on Z Min, Z Max and Boxcar, so the common way in is closed."""
    v = ImageViewer()
    for field in (v.txt_zmin, v.txt_zmax, v.txt_boxcar):
        assert field.validator() is not None
        field.clear()
        QTest.keyClicks(field, "a1b2")
        assert field.text() == "12"

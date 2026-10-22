"""Tests that exercise menu actions the way Qt fires them.

QAction.triggered is declared triggered(bool checked=False), and PySide6 selects
that overload for any slot that accepts one argument. Openers such as
open_depth_plot(initial_center=None) therefore receive False, not None, when
invoked from the menu bar -- constructing the dialogs directly in a test does
not cover this path.
"""
import pytest
from PySide6.QtGui import QAction
from pyql3.gui.main_window import MainWindow
from pyql3.gui.tools.base_tool import as_center


@pytest.fixture
def main_window(qapp, sample_3d_fits):
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    yield win
    win.close()


def test_qaction_triggered_delivers_bool_to_one_arg_slot(qapp):
    """Pin down the Qt behaviour these openers have to tolerate."""
    received = []

    def slot(initial_center=None):
        received.append(initial_center)

    holder = MainWindow()
    action = QAction("probe", holder)
    action.triggered.connect(slot)
    action.trigger()
    holder.close()

    assert received == [False], (
        "Expected QAction.triggered to hand a one-argument slot its checked flag; "
        f"got {received}. If Qt/PySide6 behaviour changed, as_center() is now belt-and-braces."
    )


@pytest.mark.parametrize("value,expected", [
    (None, None),
    (False, None),
    (True, None),
    ((3, 4), (3, 4)),
    ([3, 4], (3, 4)),
    ((1, 2, 3), None),
    (7, None),
    ("xy", ("x", "y")),
])
def test_as_center_coercion(value, expected):
    assert as_center(value) == expected


def test_scaling_menu_keeps_the_collapse_display(main_window, qapp):
    """R57: Display > Scaling redisplayed through update_image_display's defaults, which
    passed the 2-D collapsed plane to imv.setImage (hiding the timeline) and replaced the
    manual Min/Max with automatic levels. The Scale combo box kept both."""
    main_window.resize(900, 900)
    main_window.show()
    v = main_window.image_viewer
    v.combo_collapse.setCurrentText("Sum")
    v.txt_zmin.setText("20")
    v.txt_zmax.setText("30")
    v.radio_range.setChecked(True)
    v.txt_min.setText("900")
    v.txt_max.setText("1300")
    v.apply_contrast()
    qapp.processEvents()
    assert v.imv.ui.roiPlot.isVisible()

    main_window.set_scaling("Sqrt")
    qapp.processEvents()

    assert v.combo_scale.currentText() == "Sqrt"
    assert v.imv.ui.roiPlot.isVisible(), "the timeline with the collapse range was hidden"
    assert (v.txt_min.text(), v.txt_max.text()) == ("900", "1300")


def test_depth_plot_opens_from_menu_action(main_window):
    """Regression: Plot -> Depth Plot raised TypeError unpacking the bool flag."""
    action = QAction("Depth Plot", main_window)
    action.triggered.connect(main_window.open_depth_plot)
    action.trigger()

    dialog = getattr(main_window, '_depth_plot_dialog', None)
    assert dialog is not None, "Depth Plot dialog was never created from the menu action"
    assert dialog.isVisible(), "Depth Plot dialog was created but not shown"
    x_data, _ = dialog.plot_data.getData()
    assert x_data is not None and len(x_data) > 0, "Depth Plot opened with no spectrum"
    dialog.close()


def test_gaussian_fit_opens_from_menu_action(main_window):
    """Regression: Analysis -> Gaussian Fit raised the same TypeError."""
    action = QAction("Gaussian Fit", main_window)
    action.triggered.connect(main_window.open_gaussian_fit)
    action.trigger()

    dialog = getattr(main_window, '_gauss_dialog', None)
    assert dialog is not None, "Gaussian Fit dialog was never created from the menu action"
    assert dialog.isVisible(), "Gaussian Fit dialog was created but not shown"
    dialog.close()


def test_depth_plot_menu_centres_roi_on_the_cube(main_window):
    """With no centre given, the ROI must land on the cube, not at a bool-derived spot."""
    main_window.open_depth_plot()
    dialog = main_window._depth_plot_dialog

    shape = main_window.image_viewer.display_data.shape
    max_x, max_y = (shape[1], shape[2]) if len(shape) == 3 else (shape[0], shape[1])
    pos, size = dialog.roi.pos(), dialog.roi.size()
    cx, cy = pos.x() + size.x() / 2.0, pos.y() + size.y() / 2.0

    assert 0 <= cx <= max_x and 0 <= cy <= max_y, f"ROI centre {(cx, cy)} is outside the cube"
    dialog.close()


def test_right_click_signal_still_passes_a_centre(main_window):
    """The context-menu path emits a real (x, y) tuple, which must survive coercion."""
    main_window.image_viewer.last_right_click_pixel_pos = (12, 9)
    main_window.image_viewer.request_depth_plot.emit((12, 9))

    dialog = getattr(main_window, '_depth_plot_dialog', None)
    assert dialog is not None, "Depth Plot dialog was not created from the context-menu signal"
    pos, size = dialog.roi.pos(), dialog.roi.size()
    center = (pos.x() + size.x() / 2.0, pos.y() + size.y() / 2.0)
    # The clicked index is a pixel; the circle is drawn on that pixel's centre, half a
    # pixel into the ImageItem's [i, i+1) cell, and measures there too (BUGS.md R2).
    assert center == pytest.approx((12.5, 9.5)), f"ROI centre is {center}, expected (12.5, 9.5)"
    assert dialog.aperture_geometry()[:2] == pytest.approx((12.0, 9.0))
    dialog.close()


# Every tool opener reachable from the Plot / Analysis / Display menus. All open
# non-modal dialogs via show(). edit_header and open_polling_config are excluded
# on purpose: they call dialog.exec(), whose modal event loop blocks a headless
# run forever (and cannot be patched away after create_menus() has connected the
# bound methods).
TOOL_OPENERS = [
    "open_depth_plot",
    "open_horizontal_cut",
    "open_vertical_cut",
    "open_diagonal_cut",
    "open_surface_plot",
    "open_contour_plot",
    "open_plot_catalog",
    "open_statistics",
    "open_photometry",
    "open_gaussian_fit",
    "open_strehl_tool",
    "open_arithmetic_tool",
    "open_rotate",
    "redisplay_image",
]

DIALOG_ATTRS = ['_depth_plot_dialog', '_hcut_dialog', '_vcut_dialog', '_dcut_dialog',
                '_surf_dialog', '_cont_dialog', '_plot_catalog_dialog', '_stats_dialog',
                '_phot_dialog', '_gauss_dialog', '_strehl_dialog', '_rotate_dialog',
                '_arith_dialog']


@pytest.mark.parametrize("opener", TOOL_OPENERS)
def test_tool_opener_survives_qaction_triggered(main_window, opener):
    """Every tool opener must tolerate being wired straight to QAction.triggered.

    Guards the whole class of bug: an opener whose signature silently absorbs
    triggered()'s `checked` flag and then treats it as data. This connects the
    real bound method to a real QAction, exactly as create_menus() does.

    (The app's own menu objects are deliberately not walked here: MainWindow
    keeps no Python reference to its QMenus, so calling menu_action.menu() from
    a test creates a transient Python owner and PySide6 deletes the C++ object.)
    """
    method = getattr(main_window, opener)
    action = QAction(opener, main_window)      # parented, so it stays alive
    action.triggered.connect(method)

    try:
        action.trigger()
    except Exception as exc:                   # pragma: no cover - diagnostic
        pytest.fail(f"{opener} raised when fired from a QAction: {type(exc).__name__}: {exc}")
    finally:
        for attr in DIALOG_ATTRS:
            dialog = getattr(main_window, attr, None)
            if dialog is not None:
                dialog.close()
        action.setParent(None)


# --- R15: one teardown for Close and Escape --------------------------------------------------

def _catalog_tool(viewer):
    """A Plot Catalog with markers and labels on the image, so its teardown has work to do."""
    from astropy.table import Table
    from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

    dialog = PlotCatalogDialog(None, viewer)
    dialog.set_catalog_table(Table({
        "name": ["a", "b", "c"],
        "x": [5.0, 10.0, 15.0],
        "y": [5.0, 10.0, 15.0],
    }), "test")
    dialog.chk_show_name.setChecked(True)
    dialog.update_visible_text_labels()
    return dialog


def _tool_builders():
    """Every `BaseToolDialog` subclass, as `{test id: (class, build(viewer))}`."""
    from pyql3.gui.tools.advanced_plots import ContourDialog, SurfacePlotDialog
    from pyql3.gui.tools.cuts import CutPlotDialog
    from pyql3.gui.tools.depth_plot import DepthPlotDialog
    from pyql3.gui.tools.fitting import GaussianFitDialog
    from pyql3.gui.tools.photometry import PhotometryDialog
    from pyql3.gui.tools.plot_catalog import PlotCatalogDialog
    from pyql3.gui.tools.region_list import RegionListDialog
    from pyql3.gui.tools.rotate import RotateDialog
    from pyql3.gui.tools.statistics import StatisticsDialog
    from pyql3.gui.tools.strehl import StrehlDialog

    return {
        "depth_plot": (DepthPlotDialog, lambda v: DepthPlotDialog(None, v)),
        "hcut": (CutPlotDialog, lambda v: CutPlotDialog('horizontal', None, v)),
        "vcut": (CutPlotDialog, lambda v: CutPlotDialog('vertical', None, v)),
        "dcut": (CutPlotDialog, lambda v: CutPlotDialog('diagonal', None, v)),
        "surface": (SurfacePlotDialog, lambda v: SurfacePlotDialog(None, v)),
        "contour": (ContourDialog, lambda v: ContourDialog(None, v)),
        "plot_catalog": (PlotCatalogDialog, _catalog_tool),
        "statistics": (StatisticsDialog, lambda v: StatisticsDialog(None, v)),
        "photometry": (PhotometryDialog, lambda v: PhotometryDialog(None, v)),
        "gaussian_fit": (GaussianFitDialog, lambda v: GaussianFitDialog(None, v)),
        "strehl": (StrehlDialog, lambda v: StrehlDialog(None, v)),
        "rotate": (RotateDialog, lambda v: RotateDialog(None, v)),
        "region_list": (RegionListDialog, lambda v: RegionListDialog(None, v)),
    }


TOOL_IDS = list(_tool_builders())
#: Tools that draw nothing on the image: the Surface plot draws in its own window.
DRAW_NOTHING = {"surface", "rotate", "region_list"}


def _show_tool(name, viewer, qapp):
    """Show the viewer and the tool, and return (dialog, scene items that existed before it)."""
    viewer.show()
    scene = viewer.imv.getView().scene()
    before = set(scene.items())
    _, build = _tool_builders()[name]
    dialog = build(viewer)
    dialog.show()
    qapp.processEvents()
    if name not in DRAW_NOTHING:
        assert _items_left_by(viewer, before), "the tool drew nothing, so this checks nothing"
    return dialog, before


def _items_left_by(viewer, before):
    return [item for item in viewer.imv.getView().scene().items() if item not in before]


def test_teardown_tests_cover_every_tool():
    """The parametrization below has to name every tool dialog, or a new one goes unchecked."""
    import importlib
    import pkgutil

    import pyql3.gui.tools as tools
    from pyql3.gui.tools.base_tool import BaseToolDialog

    for module in pkgutil.iter_modules(tools.__path__):
        importlib.import_module(f"pyql3.gui.tools.{module.name}")

    def subclasses(cls):
        for sub in cls.__subclasses__():
            yield sub
            yield from subclasses(sub)

    tools_in_package = {cls for cls in subclasses(BaseToolDialog)
                        if cls.__module__.startswith("pyql3.gui.tools.")}
    assert tools_in_package == {cls for cls, _ in _tool_builders().values()}


@pytest.mark.parametrize("name", TOOL_IDS)
def test_escape_tears_a_tool_down_like_close(qapp, loaded_viewer, name):
    """R15: Escape runs QDialog.reject(), which hides the dialog without a closeEvent, so the
    tool's ROI, rings, markers or contours stayed on the image and a checked Draw Region kept
    the view from panning."""
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dialog, before = _show_tool(name, loaded_viewer, qapp)
    if hasattr(dialog, 'btn_draw'):
        dialog.btn_draw.click()
        assert loaded_viewer.exclusive_drag_owner() is dialog

    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    qapp.processEvents()

    assert not dialog.isVisible()
    assert _items_left_by(loaded_viewer, before) == []
    assert loaded_viewer.exclusive_drag_owner() is None
    assert loaded_viewer.imv.getView().state['mouseEnabled'] == [True, True]


@pytest.mark.parametrize("name", TOOL_IDS)
def test_close_still_hides_every_tool(qapp, loaded_viewer, name):
    """Guards the obvious wrong fix for R15: a `reject()` that calls `self.close()` re-enters a
    close already in progress, and the dialog then stays on screen after Close and Escape."""
    dialog, before = _show_tool(name, loaded_viewer, qapp)

    dialog.close()
    qapp.processEvents()

    assert not dialog.isVisible()
    assert _items_left_by(loaded_viewer, before) == []


@pytest.mark.parametrize("name", TOOL_IDS)
def test_tear_down_twice_is_harmless(qapp, loaded_viewer, name):
    """A Close runs the teardown twice (closeEvent, then QDialog.closeEvent's own reject()), so
    the second call must neither raise nor warn, for example about a signal already disconnected."""
    import warnings

    dialog, before = _show_tool(name, loaded_viewer, qapp)
    with warnings.catch_warnings():
        warnings.simplefilter("error")
        dialog.tear_down()
        dialog.tear_down()
        dialog.close()
    qapp.processEvents()

    assert not dialog.isVisible()
    assert _items_left_by(loaded_viewer, before) == []


class _Press:
    """The parts of a scene mouse press that pyqtgraph's MouseClickEvent reads."""

    def __init__(self, scene_pos):
        from PySide6.QtCore import QPointF, Qt
        self._pos = QPointF(*scene_pos)
        self._button = Qt.MouseButton.LeftButton
        self._modifiers = Qt.KeyboardModifier.NoModifier

    def scenePos(self):
        return self._pos

    def screenPos(self):
        return self._pos

    def button(self):
        return self._button

    def buttons(self):
        return self._button

    def modifiers(self):
        return self._modifiers


def test_closing_a_tool_mid_drag_leaves_the_scene_no_deleted_roi(qapp, loaded_viewer):
    """M31's residual exposure: the scene keeps `dragItem` and the last hover event, which can
    point at a tool's ROI or one of its handles. Once the ROI is deleted, the next click
    reached a deleted C++ object."""
    from PySide6.QtCore import Qt
    from pyqtgraph.GraphicsScene.mouseEvents import HoverEvent, MouseClickEvent

    dialog, _ = _show_tool("statistics", loaded_viewer, qapp)
    scene = loaded_viewer.imv.getView().scene()
    roi = dialog.roi
    handle = roi.getHandles()[0]
    hover = HoverEvent(None, True)
    hover.currentItem = handle
    hover.acceptClicks(Qt.MouseButton.LeftButton)
    scene.lastHoverEvent = hover
    scene.dragItem = roi

    dialog.close()
    qapp.processEvents()

    assert scene.dragItem is None
    assert scene.lastHoverEvent is None
    scene.sendClickEvent(MouseClickEvent(_Press((10.0, 10.0))))


@pytest.mark.parametrize("name", ["statistics", "photometry", "gaussian_fit", "strehl",
                                  "depth_plot"])
def test_a_tool_keeps_the_roi_it_opened_with(qapp, loaded_viewer, name):
    """R85: Photometry and Strehl set `self.roi` and then passed it to `add_roi_to_viewer`,
    which retired the ROI it was adding; one event-loop pass later `_drop_retired_rois`
    deleted it, while the tool still measured through it."""
    import shiboken6

    dialog, _ = _show_tool(name, loaded_viewer, qapp)
    qapp.processEvents()

    assert shiboken6.isValid(dialog.roi)
    assert dialog.roi.scene() is loaded_viewer.imv.getView().scene()
    dialog.close()


@pytest.mark.parametrize("mode", ["Annulus", "Region"])
def test_depth_plot_retires_its_background_rois(qapp, loaded_viewer, mode):
    """The Depth Plot's background box and annulus rings were dropped (`self.bg_roi = None`)
    rather than retired, so the collector freed them in whatever order their reference cycles
    were walked -- M31's crash pattern -- and `tear_down` now runs `gc.collect()` straight
    after. Each one must go through `_retired_rois`, which deletes it before collecting."""
    import gc

    import shiboken6
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from pyql3.gui.tools.depth_plot import DepthPlotDialog

    dialog, before = _show_tool("depth_plot", loaded_viewer, qapp)
    dialog.chk_enable_bg.setChecked(True)
    dialog.combo_bg_mode.setCurrentText(mode)
    seen, background = [], []

    def note(roi, into):
        if roi is not None and all(roi is not other for other in seen):
            seen.append(roi)
            into.append(roi)

    def note_rois():
        note(dialog.roi, [])
        for roi in (dialog.bg_roi, dialog.ring_inner, dialog.ring_outer):
            note(roi, background)

    note_rois()
    for shape in ("Rectangle", "Circle", "Rectangle", "Circle"):
        dialog.combo_shape.setCurrentText(shape)
        qapp.processEvents()
        note_rois()
    # Annulus mode builds rings, gives way to a box over the rectangle, and builds rings again.
    assert len(background) >= 3, "too few background ROIs were built for this to check much"

    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    qapp.processEvents()
    assert _items_left_by(loaded_viewer, before) == []

    replacement = DepthPlotDialog(None, loaded_viewer)
    replacement.show()
    gc.collect()
    qapp.processEvents()

    assert [roi for roi in seen if shiboken6.isValid(roi)] == []
    replacement.close()


def test_photometry_retires_its_sky_rings(qapp, loaded_viewer):
    """`tear_down` took the sky rings off the scene but only kept them as attributes, so once
    `MainWindow._open_tool` deleted the dialog they went to the collector in whatever order
    their reference cycles were walked -- M31's crash pattern. They must go through
    `_retired_rois`, which deletes them before collecting."""
    import shiboken6
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dialog, before = _show_tool("photometry", loaded_viewer, qapp)
    rings = [dialog.roi_inner, dialog.roi_outer]
    assert all(shiboken6.isValid(ring) for ring in rings)

    QTest.keyClick(dialog, Qt.Key.Key_Escape)
    qapp.processEvents()

    assert _items_left_by(loaded_viewer, before) == []
    assert [ring for ring in rings if shiboken6.isValid(ring)] == []
    assert dialog.roi_inner is None and dialog.roi_outer is None


# --- R83: Total DN only for a declared count rate -------------------------------------

@pytest.fixture
def unitless_fits(tmp_path):
    """A cube laid out as a Gemini NIFS one: no `BUNIT` anywhere, and `EXPTIME`/`COADDS` in
    the primary only. Its values look like summed counts, but the file does not say so."""
    from astropy.io import fits
    import numpy as np
    path = tmp_path / "unitless.fits"
    fits.HDUList([fits.PrimaryHDU(header=fits.Header({'EXPTIME': 600.0, 'COADDS': 1})),
                  fits.ImageHDU(np.ones((20, 9, 7), dtype=np.float32), name='SCI')]
                 ).writeto(path)
    return str(path)


def test_total_dn_is_offered_only_for_a_count_rate(qapp, s3d_like_fits, unitless_fits,
                                                    osiris_like_fits):
    """JWST MJy/sr and a cube with no `BUNIT` used to offer "As Total DN", multiply by 1.0 and
    relabel the unchanged numbers Total DN (R83). The action is disabled with the reason as its
    tooltip and status tip, and the native unit's action is named after the file's unit."""
    win = MainWindow()
    try:
        assert win.action_dn_s.text() == "As Stored"
        assert not win.action_tot_dn.isEnabled()
        win.colorbar_action.trigger()                   # Display > Show Colorbar, no file open
        assert win.image_viewer.imv.ui.histogram.axis.labelText == "Pixel Value"

        for path, native, reason in [
                (s3d_like_fits, "As Stored (MJy/sr)", "data are in MJy/sr"),
                (unitless_fits, "As Stored (unit not stated)", "no unit is stated for these data")]:
            assert win.load_fits(path)
            assert not win.action_tot_dn.isEnabled(), path
            assert win.action_tot_dn.toolTip() == reason
            assert win.action_tot_dn.statusTip() == reason
            assert win.action_dn_s.text() == native
            assert win.action_dn_s.isChecked()

        assert win.load_fits(osiris_like_fits)
        assert win.action_tot_dn.isEnabled()
        assert win.action_tot_dn.statusTip() == ""
        assert win.action_dn_s.text() == "As Stored (DN/s)"
        win.action_tot_dn.trigger()
        assert win.image_viewer.data_multiplier == pytest.approx(899.92)
        assert win.image_viewer.value_unit_label() == "Total DN"
        assert win.image_viewer.imv.ui.histogram.axis.labelText == "Pixel Value (Total DN)"
    finally:
        win.close()


def test_loading_a_non_rate_cube_leaves_total_dn(qapp, osiris_like_fits, s3d_like_fits):
    """Loading an MJy/sr cube while Total DN is shown returns to the file's own unit, says so in
    the status bar, and redraws once: `set_data` clears the unit before it announces the plane,
    so no second `refresh_display` follows the load."""
    win = MainWindow()
    try:
        assert win.load_fits(osiris_like_fits)
        win.action_tot_dn.trigger()
        assert win.image_viewer.disp_as_dn

        planes = []
        win.image_viewer.plane_changed.connect(
            lambda: planes.append((win.image_viewer.disp_as_dn,
                                   win.image_viewer.value_unit_label())))
        assert win.load_fits(s3d_like_fits)

        assert planes == [(False, "MJy/sr")]
        assert win.image_viewer.data_multiplier == 1.0
        assert win.action_dn_s.isChecked() and not win.action_tot_dn.isChecked()
        assert win.statusBar().currentMessage() == (
            "Total DN is not available for this file (data are in MJy/sr); "
            "showing the values as stored.")

        # Back on a count-rate cube the user chooses Total DN again; it is not restored
        assert win.load_fits(osiris_like_fits)
        assert win.action_tot_dn.isEnabled() and not win.image_viewer.disp_as_dn

        # Nothing was switched, so nothing is said
        win.statusBar().clearMessage()
        assert win.load_fits(s3d_like_fits)
        assert win.statusBar().currentMessage() == ""
    finally:
        win.close()

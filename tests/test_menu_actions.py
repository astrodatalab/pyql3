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
    assert center == pytest.approx((12.0, 9.0)), f"ROI centre is {center}, expected (12, 9)"
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

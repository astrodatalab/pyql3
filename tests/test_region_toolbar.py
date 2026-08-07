"""The optional vertical region toolbar.

It is off by default — the image is what the window is for, and a permanent bar takes width from
it — so what matters is that the toggle works both ways, that the buttons do the same things as the
menu rather than a parallel implementation, and that the pressed button tells the truth about what a
drag will draw.

The icons are painted at run time rather than shipped, so there is nothing here about bundled
assets; the tests check that each button actually got an icon, since a silently blank toolbar would
look broken.
"""
import pytest

from pyql3.core.regions_model import Circle
from pyql3.gui.main_window import MainWindow
from pyql3.gui.region_toolbar import EXTRA_ICONS, SHAPES, region_icon


@pytest.fixture
def window(qapp, sample_3d_fits, isolated_settings):
    """A window whose toolbar preference starts unset.

    `conftest.isolated_settings` keeps the whole suite out of the real `~/.pyql3/config.json`; this
    only has to clear the one key, since another test may have set it.
    """
    isolated_settings.set("region_toolbar", False)
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    yield win
    win.close()


# ------------------------------------------------------------------- the toggle

def test_the_toolbar_is_off_until_it_is_asked_for(window):
    """A fresh install shows no toolbar and does not even build one."""
    assert window.region_toolbar is None, "built before anyone wanted it"
    assert window.region_toolbar_action.isChecked() is False


def test_a_window_with_no_stored_preference_starts_without_it(qapp, isolated_settings):
    """The startup default, stated as its own test: nothing remembered means nothing shown."""
    from PySide6.QtWidgets import QToolBar

    isolated_settings.config.pop("region_toolbar", None)
    win = MainWindow()
    try:
        assert isolated_settings.get("region_toolbar") is None, "nothing should be stored yet"
        assert win.region_toolbar is None
        assert win.findChildren(QToolBar) == [], "a toolbar appeared uninvited"
    finally:
        win.close()


def test_the_menu_entry_shows_and_hides_it(window):
    window.region_toolbar_action.setChecked(True)

    toolbar = window.region_toolbar
    assert toolbar is not None
    assert not toolbar.isHidden()

    window.region_toolbar_action.setChecked(False)
    assert toolbar.isHidden()
    assert window.region_toolbar is toolbar, "hiding should not throw the toolbar away"


def test_it_sits_vertically_beside_the_image(window):
    from PySide6.QtCore import Qt

    window.region_toolbar_action.setChecked(True)
    toolbar = window.region_toolbar

    assert window.toolBarArea(toolbar) == Qt.ToolBarArea.LeftToolBarArea
    assert toolbar.orientation() == Qt.Orientation.Vertical
    # Only the vertical edges are offered: along the top it would push the image down.
    assert not (toolbar.allowedAreas() & Qt.ToolBarArea.TopToolBarArea)


def test_the_choice_is_remembered(window, qapp):
    """A standing preference about the window's shape, not a per-file choice."""
    window.region_toolbar_action.setChecked(True)
    assert window.config.get("region_toolbar") is True

    second = MainWindow()
    try:
        assert second.region_toolbar_action.isChecked() is True
        assert second.region_toolbar is not None, "the remembered toolbar was not restored"
    finally:
        second.close()


def test_a_remembered_off_state_builds_nothing(window, qapp):
    window.config.set("region_toolbar", False)

    second = MainWindow()
    try:
        assert second.region_toolbar is None
    finally:
        second.close()


# -------------------------------------------------------------------- buttons

def test_every_button_has_an_icon(window):
    window.region_toolbar_action.setChecked(True)

    labelled = [action for action in window.region_toolbar.actions() if action.text()]
    assert len(labelled) == len(SHAPES) + len(EXTRA_ICONS)
    for action in labelled:
        assert not action.icon().isNull(), f"{action.text()} has no icon"


@pytest.mark.parametrize("kind", [kind for kind, _ in SHAPES])
def test_a_shape_button_arms_that_shape(window, kind, monkeypatch):
    from PySide6.QtWidgets import QInputDialog

    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("knot", True)))
    window.region_toolbar_action.setChecked(True)

    window.region_toolbar.shape_actions[kind].trigger()

    assert window.region_layer.draw_kind == kind
    assert window.region_toolbar.shape_actions[kind].isChecked()
    window.region_layer.cancel_draw()


def test_only_one_shape_button_is_pressed_at_a_time(window):
    window.region_toolbar_action.setChecked(True)
    buttons = window.region_toolbar.shape_actions

    buttons["circle"].trigger()
    buttons["box"].trigger()

    assert buttons["box"].isChecked()
    assert not buttons["circle"].isChecked()
    window.region_layer.cancel_draw()


def test_the_pressed_button_releases_when_drawing_ends(window):
    window.region_toolbar_action.setChecked(True)
    window.region_toolbar.shape_actions["circle"].trigger()

    window.region_layer.cancel_draw()

    assert not any(action.isChecked() for action in window.region_toolbar.shape_actions.values())


def test_the_button_releases_when_a_tool_takes_the_drag(window):
    """A tool's Draw Box revokes the layer's drag, so the toolbar must not keep claiming it."""
    from pyql3.gui.tools.statistics import StatisticsDialog

    window.region_toolbar_action.setChecked(True)
    window.region_toolbar.shape_actions["box"].trigger()
    assert window.region_toolbar.shape_actions["box"].isChecked()

    dialog = StatisticsDialog(window, window.image_viewer)
    try:
        dialog.enable_draw_mode()
        assert not window.region_toolbar.shape_actions["box"].isChecked(), \
            "the toolbar still claims a drag it has lost"
    finally:
        dialog.close()


def test_a_refused_draw_leaves_no_button_pressed(qapp, monkeypatch, isolated_settings):
    """With nothing loaded the window declines, so the button must not look armed."""
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    win = MainWindow()
    try:
        win.region_toolbar_action.setChecked(True)
        win.region_toolbar.shape_actions["circle"].trigger()

        assert not win.region_layer.drawing
        assert not win.region_toolbar.shape_actions["circle"].isChecked()
    finally:
        win.close()


def test_the_list_button_opens_the_region_list(window):
    window.region_toolbar_action.setChecked(True)

    window.region_toolbar.list_action.trigger()

    assert window._region_list_dialog.isVisible()


def test_the_clear_button_goes_through_the_same_confirmation(window, monkeypatch):
    """The buttons call the window, so they inherit its confirmation rather than skipping it."""
    from PySide6.QtWidgets import QMessageBox

    window.region_layer.set_regions([Circle(x=1.0, y=1.0, radius=1.0)])
    window.region_toolbar_action.setChecked(True)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

    window.region_toolbar.clear_action.trigger()
    assert len(window.region_layer) == 1, "cleared without asking"

    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))
    window.region_toolbar.clear_action.trigger()
    assert len(window.region_layer) == 0


# ---------------------------------------------------------------------- icons

@pytest.mark.parametrize("kind", [kind for kind, _ in SHAPES] + list(EXTRA_ICONS))
def test_each_icon_is_painted_and_not_blank(qapp, kind):
    from PySide6.QtGui import QColor

    icon = region_icon(kind, QColor("white"), size=32)
    assert not icon.isNull()

    image = icon.pixmap(32, 32).toImage()
    inked = sum(1 for x in range(32) for y in range(32) if image.pixelColor(x, y).alpha() > 0)
    assert inked > 20, f"the {kind} icon is effectively empty ({inked} pixels drawn)"


def test_icons_take_their_colour_from_the_palette(qapp):
    """So they stay legible in a light or a dark theme, rather than being a fixed colour."""
    from PySide6.QtGui import QColor

    image = region_icon("box", QColor("red"), size=32).pixmap(32, 32).toImage()
    reds = [image.pixelColor(x, y) for x in range(32) for y in range(32)
            if image.pixelColor(x, y).alpha() > 128]
    assert reds, "nothing was drawn"
    assert all(colour.red() > colour.blue() for colour in reds), "the icon ignored the colour asked for"

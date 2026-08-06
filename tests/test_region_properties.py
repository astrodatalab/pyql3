"""The per-region properties dialog, and the region's own context menu on the image.

Both exist because of the same complaint: a region could be drawn but not changed, and
right-clicking one raised in the terminal. What is asserted here is that every field the model
carries can actually be edited, that Cancel really puts things back, and that the context menu is
offered instead of pyqtgraph's — whose menu walks up to the ImageItem and raises.

`QMenu.exec` is modal and blocks until dismissed, which is why `MainWindow` builds its region menu
in one method and shows it in another: the tests here inspect and trigger a built menu, and never
let one be shown. A test that popped one up would hang the suite — the same hazard AGENTS.md
records for `install_cli_tool`.
"""
import pytest
from PySide6.QtGui import QColor
from PySide6.QtWidgets import QColorDialog

from pyql3.core.regions_model import Arrow, Box, Circle, Text
from pyql3.gui.dialogs.region_properties import (
    copy_region_coordinates,
    region_coordinate_text,
)
from pyql3.gui.main_window import MainWindow


@pytest.fixture
def window(qapp, sample_3d_fits):
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    yield win
    win.close()


@pytest.fixture
def layer(window):
    return window.region_layer


def editor_for(window, region):
    """Open the properties dialog the way a double-click does."""
    window.region_layer.add(region)
    return window.open_region_properties(region)


# ----------------------------------------------------------------- the fields

def test_every_style_field_reaches_the_region(window):
    """The list from the report: colour, line width, text size, text colour, text angle."""
    region = Text(x=5.0, y=5.0, text="knot", angle=0.0)
    dialog = editor_for(window, region)
    try:
        dialog.txt_label.setText("Br-gamma")
        dialog._colour = "#ff8800"
        dialog.spin_width.setValue(4)
        dialog.spin_font.setValue(22)
        dialog.spin_angle.setValue(35.0)
        dialog.chk_dash.setChecked(True)
        dialog.txt_tag.setText("knots")
        dialog.apply()

        assert region.text == "Br-gamma"
        assert region.color == "#ff8800"
        assert region.line_width == 4
        assert region.font_size == 22
        assert region.angle == pytest.approx(35.0)
        assert region.dash is True
        assert region.tag == "knots"
    finally:
        dialog.close()


def test_the_drawn_label_follows_a_font_and_colour_change(window):
    region = Circle(x=6.0, y=6.0, radius=3.0, text="src")
    dialog = editor_for(window, region)
    try:
        dialog.spin_font.setValue(28)
        dialog._colour = "#00ccff"
        dialog.apply()

        label = window.region_layer.label_for(region)
        assert label is not None
        assert label.textItem.font().pointSize() == 28
    finally:
        dialog.close()


def test_geometry_edits_reach_the_region_and_the_item(window):
    region = Box(x=5.0, y=5.0, width=4.0, height=3.0, angle=0.0)
    dialog = editor_for(window, region)
    try:
        dialog.spin_x.setValue(9.0)
        dialog.spin_y.setValue(7.0)
        dialog.spin_size.setValue(8.0)
        dialog.spin_size2.setValue(6.0)
        dialog.apply()

        assert (region.x, region.y) == (9.0, 7.0)
        assert (region.width, region.height) == (8.0, 6.0)
        item = window.region_layer.item_for(region)
        assert tuple(float(v) for v in item.size()) == (8.0, 6.0)
    finally:
        dialog.close()


@pytest.mark.parametrize("region,field,value,attribute", [
    (Circle(x=5.0, y=5.0, radius=2.0), "spin_size", 7.0, "radius"),
    (Arrow(x=5.0, y=5.0, length=4.0, angle=0.0), "spin_size", 9.0, "length"),
])
def test_each_shape_edits_its_own_size_field(window, region, field, value, attribute):
    dialog = editor_for(window, region)
    try:
        getattr(dialog, field).setValue(value)
        dialog.apply()
        assert getattr(region, attribute) == pytest.approx(value)
    finally:
        dialog.close()


def test_a_circle_has_no_angle_or_second_size(window):
    dialog = editor_for(window, Circle(x=5.0, y=5.0, radius=2.0))
    try:
        assert dialog.spin_size2 is None
        assert dialog.spin_angle is None, "a circle has no orientation to set"
    finally:
        dialog.close()


def test_visibility_can_be_turned_off(window):
    region = Circle(x=5.0, y=5.0, radius=2.0)
    dialog = editor_for(window, region)
    try:
        dialog.chk_visible.setChecked(False)
        dialog.apply()

        assert region.visible is False
        assert not window.region_layer.item_for(region).isVisible()
    finally:
        dialog.close()


def test_a_channel_range_can_be_set_and_cleared(window, loaded_viewer):
    region = Circle(x=5.0, y=5.0, radius=2.0)
    dialog = editor_for(window, region)
    try:
        dialog.chk_channels.setChecked(True)
        dialog.spin_zmin.setValue(30)
        dialog.spin_zmax.setValue(10)      # deliberately reversed
        dialog.apply()
        assert region.z_range == (10, 30), "a reversed range should be put in order"

        dialog.chk_channels.setChecked(False)
        dialog.apply()
        assert region.z_range is None
    finally:
        dialog.close()


def test_a_text_region_cannot_be_left_without_text(window):
    """It would draw nothing at all, leaving no way to find it on the image again."""
    region = Text(x=5.0, y=5.0, text="knot")
    dialog = editor_for(window, region)
    try:
        dialog.txt_label.setText("   ")
        dialog.apply()

        assert region.text == "knot"
        assert dialog.txt_label.text() == "knot", "the field was not put back"
    finally:
        dialog.close()


def test_a_shape_label_may_be_cleared(window):
    """Unlike a text region, a circle without a label is still a circle."""
    region = Circle(x=5.0, y=5.0, radius=2.0, text="src")
    dialog = editor_for(window, region)
    try:
        dialog.txt_label.setText("")
        dialog.apply()
        assert region.text == ""
        assert window.region_layer.label_for(region) is None
    finally:
        dialog.close()


# ---------------------------------------------------------------- ok / cancel

def test_cancel_restores_everything_including_an_applied_change(window):
    region = Circle(x=5.0, y=5.0, radius=2.0, color="green", line_width=2)
    dialog = editor_for(window, region)
    try:
        dialog.spin_x.setValue(11.0)
        dialog._colour = "red"
        dialog.apply()
        assert region.x == 11.0 and region.color == "red"

        dialog.reject()

        assert region.x == 5.0, "Cancel did not undo an applied change"
        assert region.color == "green"
        assert region.line_width == 2
    finally:
        dialog.close()


def test_ok_applies_and_closes(window):
    region = Circle(x=5.0, y=5.0, radius=2.0)
    dialog = editor_for(window, region)
    dialog.spin_x.setValue(3.0)
    dialog.accept()

    assert region.x == 3.0
    assert not dialog.isVisible()


def test_the_dialog_shows_the_sky_position_when_there_is_a_wcs(window):
    dialog = editor_for(window, Circle(x=5.0, y=5.0, radius=2.0))
    try:
        assert dialog.lbl_sky.isVisible()
        assert "RA" in dialog.lbl_sky.text() and "Dec" in dialog.lbl_sky.text()
    finally:
        dialog.close()


def test_choosing_a_colour_updates_the_button(window, monkeypatch):
    dialog = editor_for(window, Circle(x=5.0, y=5.0, radius=2.0))
    try:
        monkeypatch.setattr(QColorDialog, "getColor",
                            staticmethod(lambda *a, **k: QColor("#123456")))
        dialog.choose_colour()

        assert dialog._colour == "#123456"
        assert "#123456" in dialog.btn_colour.text()
    finally:
        dialog.close()


def test_a_cancelled_colour_dialog_changes_nothing(window, monkeypatch):
    dialog = editor_for(window, Circle(x=5.0, y=5.0, radius=2.0, color="green"))
    try:
        monkeypatch.setattr(QColorDialog, "getColor", staticmethod(lambda *a, **k: QColor()))
        dialog.choose_colour()
        assert dialog._colour == "green"
    finally:
        dialog.close()


# -------------------------------------------------------------- opening it

def test_double_clicking_a_region_opens_its_properties(window, click_event):
    """End to end: the item's double-click, through the layer's signal, to the dialog."""
    from PySide6.QtCore import Qt

    region = window.region_layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    window.region_layer.item_for(region).mouseClickEvent(
        click_event(button=Qt.MouseButton.LeftButton, double=True))

    dialog = window._region_property_dialogs.get(id(region))
    assert dialog is not None and dialog.isVisible()
    dialog.close()


def test_opening_the_same_region_twice_reuses_its_dialog(window):
    """Two identical circles are `==` as dataclasses, so the dialogs are keyed by identity."""
    first = Circle(x=5.0, y=5.0, radius=2.0)
    second = Circle(x=5.0, y=5.0, radius=2.0)
    window.region_layer.set_regions([first, second])

    a = window.open_region_properties(first)
    again = window.open_region_properties(first)
    b = window.open_region_properties(second)
    try:
        assert again is a, "a second double-click stacked another dialog"
        assert b is not a, "two equal-but-distinct regions shared one dialog"
    finally:
        a.close()
        b.close()


def test_closing_a_dialog_forgets_it(window):
    region = Circle(x=5.0, y=5.0, radius=2.0)
    dialog = editor_for(window, region)
    dialog.close()

    assert id(region) not in window._region_property_dialogs


# ------------------------------------------------------- the item context menu

def test_the_viewer_context_menu_offers_the_region_actions(window):
    """Built rather than shown: `QMenu.exec` is modal and would block the suite forever."""
    region = window.region_layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    menu = window.build_region_menu(region)

    labels = [action.text() for action in menu.actions()]
    assert "Properties..." in labels
    assert "Copy Coordinates" in labels
    assert "Delete" in labels


def test_the_context_menu_delete_removes_the_region(window):
    region = window.region_layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    menu = window.build_region_menu(region)

    next(a for a in menu.actions() if a.text() == "Delete").trigger()

    assert region not in window.region_layer.regions


def test_the_context_menu_properties_opens_the_editor(window):
    region = window.region_layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    menu = window.build_region_menu(region)

    next(a for a in menu.actions() if a.text() == "Properties...").trigger()

    dialog = window._region_property_dialogs.get(id(region))
    assert dialog is not None
    dialog.close()


def test_the_context_menu_copies_coordinates(window, qapp):
    region = window.region_layer.add(Circle(x=4.0, y=3.0, radius=2.0))
    menu = window.build_region_menu(region)

    next(a for a in menu.actions() if a.text() == "Copy Coordinates").trigger()

    assert "x=4.000" in qapp.clipboard().text()


def test_asking_for_a_menu_with_no_position_builds_it_without_showing(window):
    """The signal payload is an object, so a missing position must not reach a modal exec."""
    region = window.region_layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    menu = window.show_region_menu(region, None)
    assert menu is not None and menu.actions()


# ------------------------------------------------------- coordinate text

def test_coordinate_text_includes_the_sky_position(window):
    region = Circle(x=10.0, y=9.0, radius=3.0, text="src A")
    text = region_coordinate_text(region, window.image_viewer)

    assert "circle x=10.000 y=9.000" in text
    assert "RA=" in text and "Dec=" in text
    assert "(src A)" in text


def test_coordinate_text_without_a_wcs_is_just_pixels(qapp):
    from pyql3.gui.viewers.image_viewer import ImageViewer

    text = region_coordinate_text(Circle(x=1.0, y=2.0, radius=3.0), ImageViewer())
    assert "RA=" not in text
    assert "x=1.000" in text


def test_copying_puts_the_text_on_the_clipboard(window, qapp):
    region = Circle(x=1.0, y=2.0, radius=3.0)
    copied = copy_region_coordinates(region, window.image_viewer)
    assert qapp.clipboard().text() == copied

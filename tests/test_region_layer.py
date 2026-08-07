"""Drawn regions on the viewer (`TODO_regions.md` Phase 3).

The model is in orig coordinates and the items are derived from it, so the tests that matter are
about that direction holding:

- a region drawn at a pixel stays on that pixel through every flip and rotation, which is the
  bug class `pyql3.core.coords` exists for,
- dragging an item writes the *orig* geometry back, not the display geometry,
- and items are genuinely removed rather than re-parented, which is `BUGS.md` B7.

Plus the exclusive-drag arbiter, which fixes a bug that predates regions: two tools in draw mode
used to corrupt `mouseDragEvent` between them and leave the view unable to pan.
"""
import math

import pytest
from PySide6.QtCore import QPointF, Qt

from pyql3.core.regions_model import Arrow, Box, Circle, Text

COMBINATIONS = [(flip, rot) for flip in (False, True) for rot in (0, 90, 180, 270)]
COMBINATION_IDS = [f"flip={f}-rot={r}" for f, r in COMBINATIONS]


@pytest.fixture
def layer(loaded_viewer):
    """The layer the viewer builds for itself, on a loaded 3-D cube."""
    return loaded_viewer.region_layer


def centre_of_roi(roi):
    """An ROI's centre in ImageItem coordinates, whatever its rotation."""
    width, height = (float(v) for v in roi.size())
    point = roi.mapToParent(QPointF(width / 2.0, height / 2.0))
    return (point.x(), point.y())


# ------------------------------------------------------------------ the model

def test_a_viewer_starts_with_an_empty_layer(layer):
    assert len(layer) == 0
    assert layer.regions == []


def test_adding_a_region_draws_it_and_announces_it(layer):
    seen = []
    layer.regions_changed.connect(lambda: seen.append(True))

    region = layer.add(Circle(x=10.0, y=9.0, radius=3.0))

    assert layer.regions == [region]
    assert layer.item_for(region) is not None
    assert seen, "regions_changed was not emitted"


def test_removing_a_region_takes_its_items_out_of_the_scene(layer):
    """`setParentItem(None)` leaves an item painted in the same scene (B7), so check the scene."""
    region = layer.add(Circle(x=10.0, y=9.0, radius=3.0))
    item = layer.item_for(region)
    assert item.scene() is not None

    assert layer.remove(region) is True

    assert layer.regions == []
    assert item.scene() is None, "item still in the scene after removal"


def test_removing_an_unknown_region_is_harmless(layer):
    assert layer.remove(Circle(x=1.0, y=1.0, radius=1.0)) is False


def test_regions_are_tracked_by_identity_not_equality(layer):
    """Two identical circles are `==` as dataclasses; removing one must not remove the other."""
    first = layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    second = layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    assert first == second and first is not second

    layer.remove(first)

    assert len(layer) == 1
    assert layer.regions[0] is second


def test_set_regions_replaces_everything(layer):
    layer.add(Circle(x=1.0, y=1.0, radius=1.0))
    replacement = [Box(x=4.0, y=4.0, width=3.0, height=2.0), Text(x=6.0, y=6.0, text="hi")]

    layer.set_regions(replacement)

    assert layer.regions == replacement
    assert all(layer.item_for(region) is not None for region in replacement)


def test_replacing_regions_never_releases_items_mid_rebuild(layer, qapp):
    """The graveyard must only ever grow while items are being built (`BUGS.md` M18).

    A `QGraphicsItem` sits in a reference cycle, so dropping the last Python reference leaves it
    for the cyclic collector, which then runs at whatever allocation comes next — including one
    inside `pg.ROI.addRotateHandle`, building the replacement. That is a segfault, not an
    exception, so it cannot be caught: the only defence is never to create the garbage while
    construction is underway, which is what this asserts. Flushing at the *start* of
    `_destroy_items` looks harmless and reintroduces exactly that crash.
    """
    layer.set_regions([Circle(x=float(i), y=2.0, radius=1.0) for i in range(4)])
    doomed = sum(len(entry.items) for entry in layer._entries)
    assert doomed >= 4, "the fixture should have built real items to destroy"

    layer.set_regions([Box(x=float(i), y=5.0, width=2.0, height=2.0) for i in range(4)])

    # Every item of all four entries, not just the last one destroyed: a flush inside the loop
    # leaves only the final entry's items held, which is what the bug looked like.
    assert len(layer._retired) >= doomed, (
        f"held {len(layer._retired)} of {doomed} destroyed items — "
        "a rebuild released some instead of retiring them")

    # ... and the graveyard is emptied once the event loop gets a turn, so it is not a leak.
    qapp.processEvents()
    assert layer._retired == []


def test_clear_removes_every_item_from_the_scene(layer):
    regions = [Circle(x=2.0, y=2.0, radius=1.0), Arrow(x=3.0, y=3.0, length=4.0, angle=0.0)]
    layer.set_regions(regions)
    items = [layer.item_for(region) for region in regions]

    layer.clear()

    assert len(layer) == 0
    assert all(item.scene() is None for item in items)


def test_adding_a_region_does_not_touch_the_others(layer, monkeypatch):
    """Adding used to refresh *every* region's visibility, so a bulk load was quadratic.

    Measured before the fix: 10,000 regions took 48 s to add, almost all of it here. Asserted by
    counting the work rather than by timing, which would be flaky.
    """
    for index in range(20):
        layer.add(Circle(x=float(index), y=1.0, radius=1.0), notify=False)

    touched = []
    original = layer._apply_visibility
    monkeypatch.setattr(layer, '_apply_visibility',
                        lambda entry, channel, rect=None: (touched.append(entry),
                                                           original(entry, channel, rect)))

    layer.add(Circle(x=5.0, y=5.0, radius=2.0))

    assert len(touched) == 1, f"adding one region touched {len(touched)} of them"


def test_the_lookup_index_is_emptied_by_removals(layer):
    """A leaked id key would hold an entry — and its graphics items — alive forever."""
    regions = [layer.add(Circle(x=float(i), y=1.0, radius=1.0)) for i in range(10)]
    for region in regions:
        layer.remove(region)

    assert len(layer) == 0
    assert layer._by_id == {}, "removed regions left entries behind in the index"


def test_the_lookup_index_survives_clear_and_reload(layer):
    layer.set_regions([Circle(x=1.0, y=1.0, radius=1.0) for _ in range(5)])
    layer.clear()
    assert layer._by_id == {}

    fresh = layer.add(Circle(x=2.0, y=2.0, radius=1.0))
    assert layer.item_for(fresh) is not None


# ------------------------------------------------------- placement and transforms

@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_a_circle_stays_on_its_pixel_through_every_transform(loaded_viewer, layer, flip, rot):
    """The whole point of storing orig coordinates: the region follows the data, not the screen."""
    region = layer.add(Circle(x=12.0, y=7.0, radius=3.0))

    loaded_viewer.flip = flip
    loaded_viewer.rot_angle = rot
    loaded_viewer.refresh_display()

    roi = layer.item_for(region)
    where = loaded_viewer.orig_to_display(region.x, region.y)
    assert centre_of_roi(roi) == pytest.approx((where[0] + 0.5, where[1] + 0.5), abs=1e-6)
    # ...and the model is untouched by any of it.
    assert (region.x, region.y, region.radius) == (12.0, 7.0, 3.0)


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_a_box_keeps_its_shape_and_maps_its_angle(loaded_viewer, layer, flip, rot):
    from pyql3.core import coords

    region = layer.add(Box(x=10.0, y=8.0, width=6.0, height=4.0, angle=20.0))

    loaded_viewer.flip = flip
    loaded_viewer.rot_angle = rot
    loaded_viewer.refresh_display()

    roi = layer.item_for(region)
    where = loaded_viewer.orig_to_display(region.x, region.y)
    assert centre_of_roi(roi) == pytest.approx((where[0] + 0.5, where[1] + 0.5), abs=1e-6)
    # A 90° step turns the shape rather than swapping its sides.
    assert tuple(float(v) for v in roi.size()) == (6.0, 4.0)
    expected = coords.orig_angle_to_display(20.0, flip=flip, rot_angle=rot)
    assert roi.angle() % 360.0 == pytest.approx(expected % 360.0, abs=1e-6)


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_an_arrow_keeps_its_length_and_points_the_same_way_on_the_data(loaded_viewer, layer,
                                                                      flip, rot):
    region = layer.add(Arrow(x=4.0, y=4.0, length=6.0, angle=30.0))

    loaded_viewer.flip = flip
    loaded_viewer.rot_angle = rot
    loaded_viewer.refresh_display()

    roi = layer.item_for(region)
    tail, tip = (roi.mapToParent(handle.pos()) for handle in roi.getHandles())
    length = math.hypot(tip.x() - tail.x(), tip.y() - tail.y())
    assert length == pytest.approx(6.0, abs=1e-6)

    # The tip must land where the head pixel actually is on screen.
    expected_tip = loaded_viewer.orig_to_display(*region.end)
    assert (tip.x(), tip.y()) == pytest.approx((expected_tip[0] + 0.5, expected_tip[1] + 0.5),
                                               abs=1e-6)


def test_a_text_region_places_its_label_and_its_handle_together(loaded_viewer, layer):
    region = layer.add(Text(x=8.0, y=6.0, text="knot"))

    label = layer.item_for(region)          # the handle
    entry_label = [item for item in _items_of(layer, region) if hasattr(item, 'setText')][0]
    where = loaded_viewer.orig_to_display(8.0, 6.0)

    assert (label.pos().x(), label.pos().y()) == pytest.approx((where[0] + 0.5, where[1] + 0.5))
    assert (entry_label.pos().x(), entry_label.pos().y()) == pytest.approx(
        (where[0] + 0.5, where[1] + 0.5))


def _items_of(layer, region):
    for entry in layer._entries:
        if entry.region is region:
            return entry.items
    return []


def test_regions_are_re_placed_when_the_axis_mapping_changes(loaded_viewer, layer):
    """Swapping which FITS axis is X moves everything; the model must survive it."""
    region = layer.add(Circle(x=6.0, y=5.0, radius=2.0))
    before = centre_of_roi(layer.item_for(region))

    loaded_viewer.combo_x.setCurrentText("AXIS 1")
    loaded_viewer.apply_axis_mapping()

    assert (region.x, region.y) == (6.0, 5.0), "the model was rewritten by a display change"
    where = loaded_viewer.orig_to_display(6.0, 5.0)
    assert centre_of_roi(layer.item_for(region)) == pytest.approx(
        (where[0] + 0.5, where[1] + 0.5), abs=1e-6)
    assert before is not None


def test_a_layer_without_data_does_not_crash(qapp):
    from pyql3.gui.viewers.image_viewer import ImageViewer

    viewer = ImageViewer()
    layer = viewer.region_layer
    layer.add(Circle(x=1.0, y=2.0, radius=3.0))     # nothing loaded, so nowhere to put it
    layer.refresh()
    layer.update_channel_visibility()

    assert len(layer) == 1


# ------------------------------------------------------------- channel ranges

def test_a_region_outside_its_channel_range_is_hidden(loaded_viewer, layer):
    inside = layer.add(Circle(x=5.0, y=5.0, radius=2.0, z_range=(10, 20)))
    always = layer.add(Circle(x=8.0, y=8.0, radius=2.0))

    loaded_viewer.slider_slice.setValue(15)
    assert layer.item_for(inside).isVisible()
    assert layer.item_for(always).isVisible()

    loaded_viewer.slider_slice.setValue(30)
    assert not layer.item_for(inside).isVisible(), "shown outside its channel range"
    assert layer.item_for(always).isVisible(), "a region with no range must always show"


def test_an_invisible_region_stays_hidden(loaded_viewer, layer):
    region = layer.add(Circle(x=5.0, y=5.0, radius=2.0, visible=False))
    assert not layer.item_for(region).isVisible()


# --------------------------------------------------------------- user editing

def test_dragging_a_circle_writes_orig_coordinates_back(loaded_viewer, layer):
    region = layer.add(Circle(x=10.0, y=9.0, radius=3.0))
    roi = layer.item_for(region)

    roi.setPos([0.5, 0.5])          # ImageItem coordinates: centre of pixel (0, 0) plus radius

    expected = loaded_viewer.display_to_orig(0.0 + 3.0, 0.0 + 3.0)
    assert (region.x, region.y) == pytest.approx(expected, abs=1e-6)


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_a_drag_survives_a_round_trip_through_any_transform(loaded_viewer, layer, flip, rot):
    """Drag under one orientation, then rotate: the region must not move on the data."""
    loaded_viewer.flip = flip
    loaded_viewer.rot_angle = rot
    loaded_viewer.refresh_display()

    region = layer.add(Circle(x=6.0, y=5.0, radius=2.0))
    roi = layer.item_for(region)
    roi.setPos([roi.pos()[0] + 2.0, roi.pos()[1]])       # nudge along the screen's x
    moved = (region.x, region.y)

    loaded_viewer.refresh_display()

    assert (region.x, region.y) == pytest.approx(moved, abs=1e-6), \
        "re-placing the items rewrote the model"
    where = loaded_viewer.orig_to_display(*moved)
    assert centre_of_roi(layer.item_for(region)) == pytest.approx(
        (where[0] + 0.5, where[1] + 0.5), abs=1e-6)


def test_rotating_a_box_writes_an_orig_angle_back(loaded_viewer, layer):
    from pyql3.core import coords

    loaded_viewer.flip = True
    loaded_viewer.rot_angle = 90
    loaded_viewer.refresh_display()

    region = layer.add(Box(x=8.0, y=6.0, width=6.0, height=4.0, angle=0.0))
    roi = layer.item_for(region)
    roi.setAngle(35.0, center=[0.5, 0.5])

    expected = coords.display_angle_to_orig(35.0, flip=True, rot_angle=90)
    assert region.angle == pytest.approx(expected % 360.0, abs=1e-6)


def test_the_text_itself_is_the_handle(loaded_viewer, layer):
    """No marker beside the label: a crosshair on the same anchor covered the text it moved."""
    region = layer.add(Text(x=8.0, y=6.0, text="knot"))

    assert layer.item_for(region) is layer.label_for(region), "the label should be the handle"
    assert len(_items_of(layer, region)) == 1, "something is drawn as well as the text"


def test_dragging_the_text_moves_the_region(loaded_viewer, layer):
    region = layer.add(Text(x=8.0, y=6.0, text="knot"))
    label = layer.item_for(region)

    label.setPos(QPointF(2.5, 3.5))
    label.sigPositionChanged.emit(label)

    assert (region.x, region.y) == pytest.approx(loaded_viewer.display_to_orig(2.0, 3.0))
    assert (label.pos().x(), label.pos().y()) == pytest.approx((2.5, 3.5))


def test_the_text_accepts_the_mouse_buttons_it_needs(loaded_viewer, layer):
    """Clicking the text has to reach it: a `GraphicsObject` must say which buttons it wants."""
    region = layer.add(Text(x=8.0, y=6.0, text="knot"))
    label = layer.item_for(region)

    accepted = label.acceptedMouseButtons()
    assert accepted & Qt.MouseButton.LeftButton, "cannot be clicked or dragged"
    assert accepted & Qt.MouseButton.RightButton, "cannot be right-clicked for its menu"
    assert label.acceptHoverEvents(), "no hover feedback to show it can be grabbed"


def test_the_text_covers_its_own_glyphs_for_hit_testing(loaded_viewer, layer):
    """Clicking anywhere on the text should count, so its bounding rect must span the words."""
    short = layer.add(Text(x=4.0, y=4.0, text="a"))
    long = layer.add(Text(x=9.0, y=9.0, text="a much longer label"))

    narrow = layer.item_for(short).boundingRect().width()
    wide = layer.item_for(long).boundingRect().width()
    assert wide > narrow * 3, f"the hit area does not follow the text ({narrow} vs {wide})"


def test_hovering_the_text_outlines_it(loaded_viewer, layer):
    region = layer.add(Text(x=8.0, y=6.0, text="knot"))
    label = layer.item_for(region)

    class _Hover:
        def __init__(self, exit):
            self._exit = exit

        def isExit(self):
            return self._exit

    label.hoverEvent(_Hover(exit=False))
    assert label.border.style() != Qt.PenStyle.NoPen, "no outline on hover"

    label.hoverEvent(_Hover(exit=True))
    assert label.border.style() == Qt.PenStyle.NoPen, "the outline stayed after leaving"


def test_a_shape_caption_is_not_interactive(loaded_viewer, layer):
    """A box is grabbed by its outline; a caption that swallowed clicks would be in the way."""
    region = layer.add(Circle(x=6.0, y=6.0, radius=3.0, text="src A"))

    caption = layer.label_for(region)
    assert caption is not layer.item_for(region)
    assert not caption.acceptedMouseButtons(), "a caption should not take clicks"


def test_an_edit_announces_itself(layer):
    region = layer.add(Circle(x=10.0, y=9.0, radius=3.0))
    seen = []
    layer.regions_changed.connect(lambda: seen.append(True))

    layer.item_for(region).setPos([1.0, 1.0])

    assert seen, "regions_changed was not emitted for an edit"


def test_restyling_updates_the_item_in_place(layer):
    """Rebuilding an item to change its pen was both wasteful and a reproducible segfault.

    Dropping the last Python reference to the old items handed their deletion to the garbage
    collector, which then ran *inside* the construction of the replacements and crashed in
    `pg.ROI.addScaleHandle`.
    """
    region = layer.add(Circle(x=10.0, y=9.0, radius=3.0, color="green"))
    original = layer.item_for(region)

    region.color = "red"
    region.line_width = 5
    layer.restyle(region)

    assert layer.item_for(region) is original, "the item was replaced rather than updated"
    assert original.scene() is not None
    assert original.pen.color().name() == "#ff0000"
    assert original.pen.width() == 5


def test_restyling_rebuilds_only_when_the_items_change(layer):
    """Gaining or losing a label changes *which* items exist, so that case does rebuild."""
    region = layer.add(Circle(x=10.0, y=9.0, radius=3.0))
    assert layer.label_for(region) is None
    first = layer.item_for(region)

    region.text = "src A"
    layer.restyle(region)
    assert layer.label_for(region) is not None
    assert layer.item_for(region) is not first, "a new label needs the items rebuilt"

    second = layer.item_for(region)
    region.text = ""
    layer.restyle(region)
    assert layer.label_for(region) is None
    assert layer.item_for(region) is not second


# ------------------------------------------------------------------- drawing

def test_begin_draw_claims_the_drag_and_announces_the_mode(loaded_viewer, layer):
    modes = []
    layer.draw_mode_changed.connect(modes.append)

    layer.begin_draw("circle")

    assert layer.drawing
    assert loaded_viewer.exclusive_drag_owner() is layer
    assert modes == [True]


def test_cancelling_a_draw_gives_the_drag_back(loaded_viewer, layer):
    layer.begin_draw("box")
    layer.cancel_draw()

    assert not layer.drawing
    assert loaded_viewer.exclusive_drag_owner() is None
    assert loaded_viewer.imv.getView().mouseEnabled() == [True, True]


def test_an_unknown_shape_cannot_be_drawn(layer):
    with pytest.raises(ValueError, match="cannot draw"):
        layer.begin_draw("ellipse")


def test_place_at_creates_a_region_in_orig_coordinates(loaded_viewer, layer):
    layer.begin_draw("text", color="cyan")
    drawn = []
    layer.region_drawn.connect(drawn.append)

    region = layer.place_at(4.5, 3.5)

    assert isinstance(region, Text)
    assert (region.x, region.y) == pytest.approx(loaded_viewer.display_to_orig(4.0, 3.0))
    assert region.color == "cyan"
    assert region.text, "a text region needs some text to be visible at all"
    assert drawn == [region]


@pytest.mark.parametrize("kind,expected", [
    ("circle", Circle), ("box", Box), ("arrow", Arrow), ("text", Text)])
def test_every_drawable_shape_can_be_placed(layer, kind, expected):
    layer.begin_draw(kind)
    region = layer.place_at(5.5, 5.5)
    assert isinstance(region, expected)


def test_drawing_and_a_tool_draw_mode_cannot_both_own_the_drag(loaded_viewer, layer,
                                                               sample_3d_fits):
    """The bug this arbiter exists for: two owners used to corrupt the handler between them."""
    from pyql3.gui.tools.statistics import StatisticsDialog

    import pyqtgraph as pg

    dialog = StatisticsDialog(None, loaded_viewer)
    try:
        view = loaded_viewer.imv.getView()

        dialog.enable_draw_mode()
        assert loaded_viewer.exclusive_drag_owner() is dialog

        layer.begin_draw("circle")
        assert loaded_viewer.exclusive_drag_owner() is layer
        assert not dialog.btn_draw.isChecked(), "the tool's button stayed on after being revoked"

        layer.cancel_draw()

        # Bound methods compare unequal by identity on every attribute access, so what is
        # asserted is the underlying function: dragging is ViewBox's own panning again, rather
        # than one of the two owners' handlers left behind.
        assert getattr(view.mouseDragEvent, '__func__', None) is pg.ViewBox.mouseDragEvent, \
            "the view was left with a draw handler instead of its own panning"
        assert view.mouseEnabled() == [True, True]
    finally:
        dialog.close()


def test_a_non_owner_cannot_end_someone_elses_drag(loaded_viewer, layer):
    layer.begin_draw("circle")
    loaded_viewer.end_exclusive_drag(object())

    assert loaded_viewer.exclusive_drag_owner() is layer, "an outsider released the drag"
    layer.cancel_draw()


def test_a_drag_draws_a_circle_from_its_centre(loaded_viewer, layer):
    layer.begin_draw("circle")
    _drag(loaded_viewer, layer, (5.5, 5.5), (9.5, 5.5))

    region, = layer.regions
    assert isinstance(region, Circle)
    assert region.radius == pytest.approx(4.0, abs=1e-6)
    assert (region.x, region.y) == pytest.approx(loaded_viewer.display_to_orig(5.0, 5.0),
                                                 abs=1e-6)
    assert not layer.drawing, "drawing mode should end with the drag"


def test_a_drag_draws_a_box_between_its_corners(loaded_viewer, layer):
    layer.begin_draw("box")
    _drag(loaded_viewer, layer, (2.5, 2.5), (8.5, 6.5))

    region, = layer.regions
    assert isinstance(region, Box)
    assert (region.width, region.height) == pytest.approx((6.0, 4.0), abs=1e-6)
    assert (region.x, region.y) == pytest.approx(loaded_viewer.display_to_orig(5.0, 4.0),
                                                 abs=1e-6)


def test_a_drag_draws_an_arrow_from_tail_to_head(loaded_viewer, layer):
    layer.begin_draw("arrow")
    _drag(loaded_viewer, layer, (2.5, 2.5), (2.5, 8.5))

    region, = layer.regions
    assert isinstance(region, Arrow)
    assert region.length == pytest.approx(6.0, abs=1e-6)
    assert region.end == pytest.approx(loaded_viewer.display_to_orig(2.0, 8.0), abs=1e-6)


def test_a_drag_that_goes_nowhere_places_a_default_shape(loaded_viewer, layer):
    """A click rather than a drag, which would otherwise make a region of zero size."""
    layer.begin_draw("circle")
    _drag(loaded_viewer, layer, (5.5, 5.5), (5.6, 5.5))

    region, = layer.regions
    assert region.radius > 0


# ------------------------------------------------- right-click and double-click

def test_right_clicking_a_region_asks_for_a_menu_instead_of_raising(layer, click_event):
    """Regression: this raised in pyqtgraph on every right-click.

    `pg.ROI.raiseContextMenu` calls `scene().addParentContextMenus()`, which walks up to the
    ImageItem these items are parented to. `ImageItem.getContextMenus()` returns `[None]` for a
    non-removable image, and pyqtgraph raises `Cannot add object None ... to QMenu`. A region is
    the first ROI here built with `removable=True`, which is the only way to reach that code.
    """
    region = layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    requests = []
    layer.region_menu_requested.connect(lambda r, position: requests.append((r, position)))

    layer.item_for(region).mouseClickEvent(click_event())

    assert len(requests) == 1, "no menu was requested"
    assert requests[0][0] is region
    assert requests[0][1] is not None, "the menu needs a screen position"


def test_the_pyqtgraph_menu_path_would_still_raise(loaded_viewer, layer, click_event):
    """Pins *why* the override exists, so nobody restores the inherited behaviour."""
    region = layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    roi = layer.item_for(region)

    with pytest.raises(Exception, match="Cannot add object"):
        loaded_viewer.imv.scene.addParentContextMenus(roi, roi.getMenu(), click_event())


@pytest.mark.parametrize("region", [
    Circle(x=5.0, y=5.0, radius=2.0),
    Box(x=5.0, y=5.0, width=4.0, height=3.0),
    Arrow(x=3.0, y=3.0, length=5.0, angle=0.0),
    Text(x=6.0, y=6.0, text="knot"),
])
def test_every_shape_can_be_right_clicked(layer, region, click_event):
    layer.add(region)
    requests = []
    layer.region_menu_requested.connect(lambda r, _p: requests.append(r))

    layer.item_for(region).mouseClickEvent(click_event())

    assert requests == [region], f"{region.TYPE} did not offer a menu"


def test_double_clicking_a_region_asks_for_its_properties(layer, click_event):
    region = layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    activated = []
    layer.region_activated.connect(activated.append)

    layer.item_for(region).mouseClickEvent(
        click_event(button=Qt.MouseButton.LeftButton, double=True))

    assert activated == [region]


def test_a_single_left_click_does_not_open_properties(layer, click_event):
    region = layer.add(Circle(x=5.0, y=5.0, radius=2.0))
    activated = []
    layer.region_activated.connect(activated.append)

    layer.item_for(region).mouseClickEvent(click_event(button=Qt.MouseButton.LeftButton))

    assert activated == []


# ------------------------------------------------------------------- labels

def test_a_shape_with_a_label_draws_it(loaded_viewer, layer):
    """ds9 draws a shape's text beside it, so the label settings mean something for every shape."""
    region = layer.add(Circle(x=8.0, y=6.0, radius=3.0, text="src A"))

    label = layer.label_for(region)
    assert label is not None, "a labelled circle drew no text"
    assert label.toPlainText() == "src A"

    # Clear of the shape rather than on top of it.
    where = layer._to_item(8.0, 6.0)
    assert label.pos().y() == pytest.approx(where[1] + 3.0)


def test_a_shape_without_a_label_draws_no_text(layer):
    region = layer.add(Circle(x=8.0, y=6.0, radius=3.0))
    assert layer.label_for(region) is None


def test_a_text_regions_label_sits_on_its_anchor(loaded_viewer, layer):
    region = layer.add(Text(x=8.0, y=6.0, text="knot"))

    label = layer.label_for(region)
    where = layer._to_item(8.0, 6.0)
    assert (label.pos().x(), label.pos().y()) == pytest.approx(where)


def test_a_label_follows_a_rotation(loaded_viewer, layer):
    region = layer.add(Circle(x=8.0, y=6.0, radius=3.0, text="src"))

    loaded_viewer.rot_angle = 90
    loaded_viewer.refresh_display()

    where = loaded_viewer.orig_to_display(8.0, 6.0)
    label = layer.label_for(region)
    assert label.pos().x() == pytest.approx(where[0] + 0.5, abs=1e-6)


def _drag(viewer, layer, start, end):
    """Drive the layer's drag handler the way pyqtgraph would."""
    image_item = viewer.imv.getImageItem()
    start_scene = image_item.mapToScene(QPointF(*start))
    end_scene = image_item.mapToScene(QPointF(*end))

    layer._drag_event(_FakeDragEvent(start_scene, start_scene, is_start=True))
    layer._drag_event(_FakeDragEvent(end_scene, start_scene))
    layer._drag_event(_FakeDragEvent(end_scene, start_scene, is_finish=True))


class _FakeDragEvent:
    """The parts of a pyqtgraph drag event the layer's handler uses."""

    def __init__(self, scene_pos, button_down_pos, is_start=False, is_finish=False):
        self._scene_pos = scene_pos
        self._button_down = button_down_pos
        self._start = is_start
        self._finish = is_finish
        self.accepted = False

    def button(self):
        return Qt.MouseButton.LeftButton

    def scenePos(self):
        return self._scene_pos

    def buttonDownScenePos(self):
        return self._button_down

    def isStart(self):
        return self._start

    def isFinish(self):
        return self._finish

    def accept(self):
        self.accepted = True

    def ignore(self):
        self.accepted = False


# ------------------------------------------------- the aggregate overlay (Phase 5)

def many_circles(count, layer=None):
    """`count` circles spread over the plane."""
    return [Circle(x=float(i % 15), y=float((i // 15) % 15), radius=1.0) for i in range(count)]


def test_a_small_set_gets_one_item_per_region(layer):
    layer.set_regions(many_circles(10))

    assert layer.bulk is False
    assert all(layer.item_for(region) is not None for region in layer)


def test_a_large_set_switches_to_the_aggregate_overlay(layer):
    """Measured: 10,000 interactive regions cost 0.6 GB and 27 s. A few items cost neither."""
    layer.interactive_limit = 20
    layer.set_regions(many_circles(50))

    assert layer.bulk is True
    assert len(layer) == 50, "regions must all still be there"
    assert all(layer.item_for(region) is None for region in layer), \
        "per-region items should have been dropped"
    assert layer._bulk_items, "nothing was drawn in their place"
    # One ScatterPlotItem for the whole uniform set, not fifty.
    assert len(layer._bulk_items) == 1


def test_crossing_the_limit_by_adding_one_region_switches_mode(layer):
    layer.interactive_limit = 5
    layer.set_regions(many_circles(5))
    assert layer.bulk is False

    layer.add(Circle(x=1.0, y=1.0, radius=1.0))

    assert layer.bulk is True
    assert layer._bulk_items


def test_dropping_below_the_limit_restores_interaction(layer):
    layer.interactive_limit = 5
    regions = many_circles(8)
    layer.set_regions(regions)
    assert layer.bulk is True

    layer.remove_many(regions[:4])

    assert layer.bulk is False
    assert all(layer.item_for(region) is not None for region in layer)
    assert not layer._bulk_items, "the aggregate overlay was left behind"


def test_the_mode_switch_is_announced(layer):
    """Losing the ability to drag a region without being told would read as a bug."""
    changes = []
    layer.render_mode_changed.connect(lambda bulk, count: changes.append((bulk, count)))
    layer.interactive_limit = 5

    layer.set_regions(many_circles(20))
    assert changes == [(True, 20)]

    layer.clear()
    assert changes[-1][0] is False


def test_styles_are_grouped_so_pens_stay_uniform(layer):
    layer.interactive_limit = 5
    layer.set_regions([Circle(x=float(i), y=1.0, radius=1.0,
                              color="red" if i % 2 else "green") for i in range(20)])

    assert layer.bulk is True
    assert len(layer._bulk_items) == 2, "one item per distinct style, not one per region"


def test_every_shape_appears_in_the_overlay(layer):
    layer.interactive_limit = 2
    layer.set_regions([
        Circle(x=2.0, y=2.0, radius=1.0),
        Box(x=5.0, y=5.0, width=3.0, height=2.0, angle=20.0),
        Arrow(x=7.0, y=7.0, length=4.0, angle=45.0),
        Text(x=9.0, y=9.0, text="knot"),
    ])

    assert layer.bulk is True
    # A circle scatter, a cross for the text position, and one path item for box + arrow.
    kinds = sorted(type(item).__name__ for item in layer._bulk_items)
    assert kinds == ["PlotDataItem", "ScatterPlotItem", "ScatterPlotItem"], kinds


def test_the_overlay_follows_a_rotation(loaded_viewer, layer):
    layer.interactive_limit = 2
    layer.set_regions(many_circles(10))
    first = [item for item in layer._bulk_items][0]
    before = first.getData()[0][0]

    loaded_viewer.rot_angle = 90
    loaded_viewer.refresh_display()

    after = layer._bulk_items[0].getData()[0][0]
    expected = loaded_viewer.orig_to_display(layer.regions[0].x, layer.regions[0].y)[0] + 0.5
    assert after == pytest.approx(expected), "the overlay did not follow the rotation"
    assert before != after or expected == before


def test_the_overlay_respects_visibility_and_channel_ranges(loaded_viewer, layer):
    layer.interactive_limit = 2
    layer.set_regions([
        Circle(x=1.0, y=1.0, radius=1.0),
        Circle(x=2.0, y=2.0, radius=1.0, visible=False),
        Circle(x=3.0, y=3.0, radius=1.0, z_range=(20, 25)),
    ])

    assert loaded_viewer.slider_slice.maximum() >= 25, "fixture too shallow for this test"
    loaded_viewer.slider_slice.setValue(5)
    drawn = len(layer._bulk_items[0].getData()[0])
    assert drawn == 1, "an invisible region or one outside its channel range was drawn"

    loaded_viewer.slider_slice.setValue(22)
    assert len(layer._bulk_items[0].getData()[0]) == 2


def test_a_region_can_still_be_edited_in_the_overlay(layer):
    """No dragging, but the model — and so the Region List and the properties dialog — still works."""
    layer.interactive_limit = 2
    regions = many_circles(10)
    layer.set_regions(regions)

    regions[0].x = 11.0
    layer.restyle(regions[0])

    xs = layer._bulk_items[0].getData()[0]
    assert max(xs) == pytest.approx(11.5), "the edit did not reach the overlay"


def test_bulk_geometry_helpers():
    from pyql3.gui.viewers.region_layer import _arrow_outline, _box_outline

    outline = _box_outline((10.0, 10.0), 4.0, 2.0, 0.0)
    assert outline[0] == outline[-1], "the box outline must close"
    assert outline[0] == pytest.approx((8.0, 9.0))
    assert len(outline) == 5

    rotated = _box_outline((10.0, 10.0), 4.0, 2.0, 90.0)
    assert rotated[0] == pytest.approx((11.0, 8.0))

    arrow = _arrow_outline((0.0, 0.0), 10.0, 0.0)
    assert arrow[0] == (0.0, 0.0)
    assert arrow[1] == pytest.approx((10.0, 0.0))
    assert len(arrow) == 5, "tail, tip and two barbs drawn in one stroke"

    # The aggregate overlay draws its own arrowheads as part of the polyline, so it is a second
    # implementation of the same picture and needs its own check: the barbs must fall *behind* the
    # tip and straddle the line, or the head points the wrong way here too.
    tip, barb_left, barb_right = arrow[1], arrow[2], arrow[4]
    assert barb_left[0] < tip[0] and barb_right[0] < tip[0], "barbs are ahead of the tip"
    assert barb_left[1] > tip[1] > barb_right[1], "barbs do not straddle the line"

    for angle in (30.0, 90.0, 200.0, 315.0):
        outline = _arrow_outline((0.0, 0.0), 10.0, angle)
        tip = outline[1]
        assert math.degrees(math.atan2(tip[1], tip[0])) % 360 == pytest.approx(angle, abs=1e-6)
        for barb in (outline[2], outline[4]):
            # A barb points back from the tip, so it is nearer the tail than the tip is.
            assert math.hypot(*barb) < math.hypot(*tip)


# ---------------------------------------------- labels during pan and zoom

def pan(viewer, offset=3.0):
    """Move the view, which is what emits `sigRangeChanged` — the real trigger."""
    rect = viewer.imv.getView().viewRect()
    viewer.imv.getView().setRange(
        xRange=(rect.left() + offset, rect.right() + offset),
        yRange=(rect.top(), rect.bottom()), padding=0)


def look_at(viewer, x_range=(0, 12), y_range=(0, 12)):
    viewer.imv.getView().setRange(xRange=x_range, yRange=y_range, padding=0)


def test_panning_hides_the_labels_immediately(loaded_viewer, layer):
    """Text is 27% of a pan frame with 400 labelled regions; every frame repaints all of it."""
    regions = [Circle(x=float(i % 10), y=float(i // 10), radius=1.0, text=f"src {i}")
               for i in range(12)]
    layer.set_regions(regions)
    look_at(loaded_viewer)
    layer._labels_settled()
    assert any(layer.label_for(region).isVisible() for region in regions), "nothing to hide"

    pan(loaded_viewer)

    assert all(not layer.label_for(region).isVisible() for region in regions)
    assert layer._label_timer.isActive(), "nothing would bring the labels back"


def test_the_shapes_stay_visible_while_panning(loaded_viewer, layer):
    """Only the text is dropped: losing the regions themselves mid-drag would be worse."""
    region = layer.add(Circle(x=5.0, y=5.0, radius=2.0, text="src"))
    look_at(loaded_viewer)

    pan(loaded_viewer)

    assert layer.item_for(region).isVisible()
    assert not layer.label_for(region).isVisible()


def test_labels_come_back_when_the_view_settles(loaded_viewer, layer):
    region = layer.add(Circle(x=5.0, y=5.0, radius=2.0, text="src"))
    look_at(loaded_viewer)
    pan(loaded_viewer, offset=0.5)
    assert not layer.label_for(region).isVisible()

    layer._labels_settled()          # what the debounce timer calls

    assert layer.label_for(region).isVisible()


def test_a_label_outside_the_view_stays_hidden(loaded_viewer, layer):
    """The same pass culls to the viewport, which is what caps the cost when zoomed in."""
    inside = layer.add(Circle(x=5.0, y=5.0, radius=1.0, text="near"))
    outside = layer.add(Circle(x=500.0, y=500.0, radius=1.0, text="far"))

    look_at(loaded_viewer, (0, 10), (0, 10))
    layer._labels_settled()

    assert layer.label_for(inside).isVisible()
    assert not layer.label_for(outside).isVisible(), "a label far off screen was painted"


def test_settling_does_not_reveal_a_hidden_region(loaded_viewer, layer):
    """The cull must not override the region's own visibility or its channel range."""
    hidden = layer.add(Circle(x=5.0, y=5.0, radius=1.0, text="hidden", visible=False))
    out_of_range = layer.add(Circle(x=6.0, y=6.0, radius=1.0, text="later", z_range=(20, 25)))

    look_at(loaded_viewer)
    loaded_viewer.slider_slice.setValue(5)
    layer._labels_settled()

    assert not layer.label_for(hidden).isVisible()
    assert not layer.label_for(out_of_range).isVisible()


def test_panning_is_harmless_with_no_labels(loaded_viewer, layer):
    layer.set_regions([Circle(x=1.0, y=1.0, radius=1.0)])
    look_at(loaded_viewer)

    pan(loaded_viewer)

    assert not layer._label_timer.isActive(), "no labels, so nothing to debounce"


def test_panning_hides_the_overlays_labels_too(loaded_viewer, layer):
    layer.interactive_limit = 2
    layer.set_regions([Circle(x=float(i), y=1.0, radius=1.0, text=f"s{i}") for i in range(6)])
    look_at(loaded_viewer)
    layer._labels_settled()
    assert layer._bulk_labels, "the overlay drew no labels to hide"

    pan(loaded_viewer)

    assert all(not label.isVisible() for label in layer._bulk_labels)
    assert layer._label_timer.isActive()

    layer._labels_settled()
    assert layer._bulk_labels and all(label.isVisible() for label in layer._bulk_labels)


def test_the_overlay_draws_the_labels_it_has(loaded_viewer, layer):
    """A catalogue of named stars is mostly its names; the overlay used to draw none of them."""
    layer.interactive_limit = 2
    layer.set_regions([Circle(x=float(i % 8), y=float(i // 8), radius=0.8, text=f"star {i}")
                       for i in range(20)])
    look_at(loaded_viewer, (-2, 12), (-2, 12))
    layer._labels_settled()

    assert layer.bulk is True
    assert len(layer._bulk_labels) == 20, "the aggregated set drew no labels"
    assert {label.toPlainText() for label in layer._bulk_labels} >= {"star 0", "star 19"}


def test_the_overlay_labels_only_what_is_in_view(loaded_viewer, layer):
    layer.interactive_limit = 2
    layer.set_regions([Circle(x=2.0, y=2.0, radius=1.0, text="near"),
                       Circle(x=400.0, y=400.0, radius=1.0, text="far"),
                       Circle(x=3.0, y=3.0, radius=1.0, text="also near")])

    look_at(loaded_viewer, (0, 10), (0, 10))
    layer._labels_settled()

    drawn = {label.toPlainText() for label in layer._bulk_labels}
    assert drawn == {"near", "also near"}, drawn


def test_labels_can_be_turned_off(loaded_viewer, layer):
    """Whether a crowd of labels helps is the user's call, as the catalogue tool's Show Names is."""
    layer.interactive_limit = 2
    layer.set_regions([Circle(x=float(i % 8), y=float(i // 8), radius=0.5, text=f"s{i}")
                       for i in range(12)])
    look_at(loaded_viewer, (-2, 12), (-2, 12))
    layer._labels_settled()
    assert layer._bulk_labels, "nothing drawn to turn off"

    layer.set_labels_visible(False)
    assert layer._bulk_labels == []
    assert layer._bulk_items, "the shapes should stay when the labels go"

    layer.set_labels_visible(True)
    layer._labels_settled()
    assert layer._bulk_labels


def test_labels_can_be_turned_off_for_individual_regions_too(loaded_viewer, layer):
    """One switch for both render paths."""
    layer.set_regions([Circle(x=float(i), y=2.0, radius=0.5, text=f"s{i}") for i in range(5)])
    look_at(loaded_viewer)
    layer._labels_settled()
    assert any(layer.label_for(region).isVisible() for region in layer)

    layer.set_labels_visible(False)
    assert all(not layer.label_for(region).isVisible() for region in layer)
    assert all(layer.item_for(region).isVisible() for region in layer), "shapes went too"


def test_an_enormous_number_of_labels_is_refused_as_a_hang_guard(loaded_viewer, layer,
                                                                 monkeypatch):
    """Not a readability rule — a ceiling so a huge set cannot lock the window up."""
    from pyql3.gui.viewers import region_layer as module

    monkeypatch.setattr(module, "LABEL_SAFETY_LIMIT", 5)
    told = []
    layer.labels_suppressed.connect(told.append)
    layer.interactive_limit = 2

    layer.set_regions([Circle(x=float(i % 8), y=float(i // 8), radius=0.5, text=f"s{i}")
                       for i in range(12)])
    look_at(loaded_viewer, (-2, 12), (-2, 12))
    layer._labels_settled()

    assert layer._bulk_labels == []
    assert told and told[-1] == 12, f"the count was not reported: {told}"

    look_at(loaded_viewer, (-0.5, 1.5), (-0.5, 0.5))
    layer._labels_settled()
    assert layer._bulk_labels, "labels did not come back once few enough were in view"
    assert told[-1] == 0


def test_a_ds9_catalogue_of_named_stars_draws_its_names(loaded_viewer, layer):
    """The reported case: 2,000 labelled circles from a .reg file drew no text at all."""
    from pyql3.core.ds9_regions import from_ds9

    lines = ["# Region file format: DS9 version 4.1", "image"]
    lines += [f"circle({i % 40 + 1},{i // 40 + 1},1) # text={{star {i}}}" for i in range(2000)]
    regions, report = from_ds9("\n".join(lines) + "\n", axis_indices=(0, 1))
    assert len(regions) == 2000 and not report.skipped

    layer.set_regions(regions.regions)
    assert layer.bulk is True, "2,000 regions should be aggregated"

    # All of them in view: the names are drawn, as the catalogue tool draws its own.
    look_at(loaded_viewer, (-5, 60), (-5, 60))
    layer._labels_settled()
    assert len(layer._bulk_labels) == 2000, \
        f"only {len(layer._bulk_labels)} of 2,000 names drawn"
    assert all(label.toPlainText().startswith("star ") for label in layer._bulk_labels)

    # Zoomed into a corner, only the names in view are built.
    look_at(loaded_viewer, (0, 6), (0, 4))
    layer._labels_settled()
    assert 0 < len(layer._bulk_labels) < 2000
# ------------------------------------------------------------- arrow heads

def head_direction(head):
    """Where an arrow head points, as an angle in the view's own (y-up) convention.

    Measured from the painted path rather than from the angle option, so this cannot agree with a
    wrong formula. The tip sits at the path's origin and the base corners straddle the axis, so
    their mean is behind the tip; the head points from there to the tip. The path is in the item's
    coordinates, which for a `pxMode=True` item are screen coordinates with y downward, so the
    result is negated to compare with a data-space angle.
    """
    path = head.path
    points = [(path.elementAt(i).x, path.elementAt(i).y) for i in range(path.elementCount())]
    behind = [point for point in points if math.hypot(*point) > 1e-9]
    mean_x = sum(point[0] for point in behind) / len(behind)
    mean_y = sum(point[1] for point in behind) / len(behind)
    # From the base mean toward the tip is `(-mean_x, -mean_y)` in item coordinates; flipping the
    # y sign expresses it the way the view does, with y upward.
    return math.degrees(math.atan2(mean_y, -mean_x)) % 360.0


def arrow_head_of(layer, region):
    for entry in layer._entries:
        if entry.region is region:
            return entry.head
    return None


@pytest.mark.parametrize("angle", [0, 30, 45, 90, 135, 180, 225, 270, 315])
def test_the_arrow_head_points_along_its_own_line(loaded_viewer, layer, angle):
    """Reported from use: the head did not point the same way as the line.

    It was rotated with `direction + 180`, which is right for a `pxMode=False` item like the PA
    compass but not for this one: `pxMode=True` ignores the view transform, so the head is painted
    with y downward while the line's angle has y upward. The two agree only at 0° and 180°, so a
    horizontal arrow looked correct and every other one did not.
    """
    region = layer.add(Arrow(x=8.0, y=8.0, length=5.0, angle=float(angle)))

    head = arrow_head_of(layer, region)
    assert head is not None
    assert_same_direction(head_direction(head), angle,
                          f"an arrow drawn at {angle}° has a head pointing "
                          f"{head_direction(head):.1f}°")


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_the_arrow_head_follows_the_line_through_every_transform(loaded_viewer, layer, flip, rot):
    from pyql3.core import coords

    region = layer.add(Arrow(x=8.0, y=8.0, length=5.0, angle=40.0))
    loaded_viewer.flip = flip
    loaded_viewer.rot_angle = rot
    loaded_viewer.refresh_display()

    expected = coords.orig_angle_to_display(40.0, flip=flip, rot_angle=rot)
    assert_same_direction(head_direction(arrow_head_of(layer, region)), expected)


def test_the_head_sits_at_the_tip_of_the_line(loaded_viewer, layer):
    region = layer.add(Arrow(x=4.0, y=4.0, length=6.0, angle=90.0))

    head = arrow_head_of(layer, region)
    tip = loaded_viewer.orig_to_display(*region.end)
    assert (head.pos().x(), head.pos().y()) == pytest.approx((tip[0] + 0.5, tip[1] + 0.5),
                                                            abs=1e-6)


def test_the_head_follows_a_dragged_endpoint(loaded_viewer, layer):
    region = layer.add(Arrow(x=4.0, y=4.0, length=6.0, angle=0.0))
    roi = layer.item_for(region)

    # Drag the head handle somewhere else entirely.
    roi.movePoint(roi.getHandles()[1], QPointF(4.5, 12.5), finish=True)

    assert_same_direction(head_direction(arrow_head_of(layer, region)),
                          coords_display_angle(loaded_viewer, region))


def coords_display_angle(viewer, region):
    from pyql3.core import coords

    return coords.orig_angle_to_display(region.angle, flip=viewer.flip,
                                        rot_angle=viewer.rot_angle)


def assert_same_direction(got, expected, message=""):
    """Compare angles as directions, so 0° and 360° are the same."""
    difference = (float(got) - float(expected)) % 360.0
    assert min(difference, 360.0 - difference) < 1.0, \
        message or f"{got}° and {expected}° are different directions"


def test_only_a_text_regions_angle_turns_its_label(loaded_viewer, layer):
    """A box's angle rotates the box and an arrow's is its heading; neither is a text angle.

    Spotted by rendering the window: an arrow's caption was drawn on its side, at the arrow's own
    55°. ds9 treats `textangle` as a property of a text region alone.
    """
    box = layer.add(Box(x=6.0, y=6.0, width=4.0, height=3.0, angle=20.0, text="slit"))
    arrow = layer.add(Arrow(x=3.0, y=3.0, length=5.0, angle=55.0, text="outflow"))
    label = layer.add(Text(x=9.0, y=9.0, text="knot", angle=30.0))

    assert layer.label_for(box).angle == 0.0, "a rotated box tipped its caption over"
    assert layer.label_for(arrow).angle == 0.0, "an arrow's heading rotated its caption"
    assert layer.label_for(label).angle == pytest.approx(30.0), "a text region should turn"


@pytest.mark.parametrize("flip,rot", COMBINATIONS, ids=COMBINATION_IDS)
def test_a_shape_caption_stays_upright_through_every_transform(loaded_viewer, layer, flip, rot):
    region = layer.add(Box(x=6.0, y=6.0, width=4.0, height=3.0, angle=20.0, text="slit"))

    loaded_viewer.flip = flip
    loaded_viewer.rot_angle = rot
    loaded_viewer.refresh_display()

    assert layer.label_for(region).angle == 0.0


# ------------------------------------------------- placing text with a click

def scene_click(viewer, item_x, item_y, click_event):
    """Deliver a left click at an ImageItem position, the way the scene would."""
    scene_pos = viewer.imv.getImageItem().mapToScene(QPointF(item_x, item_y))
    event = click_event(button=Qt.MouseButton.LeftButton,
                        scene_pos=(scene_pos.x(), scene_pos.y()))
    viewer.imv.scene.sigMouseClicked.emit(event)
    return event


def test_a_click_places_a_text_region(loaded_viewer, layer, click_event):
    """A click without movement never reaches `mouseDragEvent`, so text needed a drag to appear."""
    asked = []
    layer.begin_draw("text", ask_text=lambda x, y: asked.append((x, y)) or "knot")

    scene_click(loaded_viewer, 6.5, 4.5, click_event)

    region, = layer.regions
    assert isinstance(region, Text)
    assert region.text == "knot"
    assert (region.x, region.y) == pytest.approx(loaded_viewer.display_to_orig(6.0, 4.0))
    assert not layer.drawing, "the tool should disarm once the label is placed"


def test_the_label_is_asked_for_after_the_click_and_told_where(loaded_viewer, layer, click_event):
    asked = []
    layer.begin_draw("text", ask_text=lambda x, y: asked.append((x, y)) or "knot")
    assert asked == [], "asked before the click landed"

    scene_click(loaded_viewer, 6.5, 4.5, click_event)

    assert len(asked) == 1
    assert asked[0] == pytest.approx(loaded_viewer.display_to_orig(6.0, 4.0)), \
        "the prompt was not told where the label goes"


def test_declining_the_label_places_nothing(loaded_viewer, layer, click_event):
    layer.begin_draw("text", ask_text=lambda x, y: "")

    scene_click(loaded_viewer, 6.5, 4.5, click_event)

    assert len(layer) == 0
    assert not layer.drawing


def test_dragging_in_text_mode_draws_no_rubber_band(loaded_viewer, layer):
    """A label is horizontal by nature, so a drag would only suggest an orientation it cannot have."""
    layer.begin_draw("text", ask_text=lambda x, y: "knot")

    _drag(loaded_viewer, layer, (2.5, 2.5), (9.5, 7.5))

    assert layer._draw_preview is None, "a rubber band was drawn for a point"
    assert len(layer) == 0, "a drag placed the label instead of leaving it to the click"
    assert layer.drawing, "the text tool should still be armed, waiting for a click"
    layer.cancel_draw()


def test_a_click_is_ignored_when_no_text_tool_is_armed(loaded_viewer, layer, click_event):
    scene_click(loaded_viewer, 6.5, 4.5, click_event)
    assert len(layer) == 0

    layer.begin_draw("circle")
    scene_click(loaded_viewer, 6.5, 4.5, click_event)
    assert len(layer) == 0, "a click placed something while the circle tool was armed"
    layer.cancel_draw()


def test_a_region_is_announced_once_when_drawn(loaded_viewer, layer, click_event):
    """`place_at` and the drag handler both used to emit, so a click-sized drag announced twice."""
    drawn = []
    layer.region_drawn.connect(drawn.append)

    layer.begin_draw("text", ask_text=lambda x, y: "knot")
    scene_click(loaded_viewer, 6.5, 4.5, click_event)
    assert len(drawn) == 1, f"a placed label was announced {len(drawn)} times"

    drawn.clear()
    layer.begin_draw("circle")
    _drag(loaded_viewer, layer, (5.5, 5.5), (5.6, 5.5))     # a click-sized drag
    assert len(drawn) == 1, f"a click-sized drag announced the region {len(drawn)} times"


@pytest.mark.parametrize("region", [
    Circle(x=5.0, y=5.0, radius=2.0),
    Box(x=5.0, y=5.0, width=4.0, height=3.0),
    Arrow(x=3.0, y=3.0, length=5.0, angle=0.0),
    Text(x=6.0, y=6.0, text="knot"),
])
@pytest.mark.parametrize("button", ["left", "middle"])
def test_an_ordinary_click_on_any_region_does_not_raise(layer, click_event, region, button):
    """`pg.TextItem` has no `mouseClickEvent`, so calling `super()` blindly raised on every click.

    pyqtgraph catches it inside its own dispatch and prints the traceback, so the symptom was a
    terminal full of `AttributeError: 'super' object has no attribute 'mouseClickEvent'` rather
    than a crash.
    """
    layer.add(region)
    buttons = {"left": Qt.MouseButton.LeftButton, "middle": Qt.MouseButton.MiddleButton}

    layer.item_for(region).mouseClickEvent(click_event(button=buttons[button]))

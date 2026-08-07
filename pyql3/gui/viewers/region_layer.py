"""The drawn regions on one `ImageViewer`: graphics items, editing, and drawing.

The model (`pyql3.core.regions_model`) is the source of truth and is stored in **orig**
coordinates. The items on screen are derived from it, never the reverse — except immediately
after the user drags one, when the new geometry is read back into the model. Keeping that
direction straight is what makes a flip or a 90° rotation safe: `refresh()` throws the item
geometry away and re-derives it, so nothing accumulates rounding or drifts out of the array.

Three things here were learned from earlier bugs in this codebase rather than invented:

- Items are parented to the **ImageItem**, so the view rotation (`apply_view_rotation`, a
  `QTransform` on that item) is inherited and needs no arithmetic at all.
- They are removed with `ViewBox.removeItem()`. `setParentItem(None)` is *not* removal — it
  makes an item top-level in the same scene, still painted (`BUGS.md` B7).
- Every coordinate goes through `pyql3.core.coords`, including angles. A flip mirrors an angle
  and each 90° step adds 90°, in that order (`BUGS.md` B13/B14/B20).

Drawing claims the view's drag handler through `ImageViewer.begin_exclusive_drag`, so it cannot
fight with a tool dialog's *Draw Box* mode.
"""

import gc
import math

import numpy as np
import pyqtgraph as pg
from PySide6.QtCore import QObject, QPoint, QPointF, Qt, QTimer, Signal

from pyql3.core import coords
from pyql3.core.regions_model import Arrow, Box, Circle, Region, Text, resolve_color

#: Shapes `begin_draw` understands, by the name used in the file format.
DRAWABLE = ("circle", "box", "arrow", "text")

#: Smallest region a drag can create, in pixels. A stray click would otherwise leave a
#: zero-sized region that cannot be seen or grabbed again.
MIN_SIZE = 1.0

#: Size of a region created at a point rather than dragged out — from the right-click menu, or
#: from a click with a shape tool armed. Big enough to see and to grab a handle on.
DEFAULT_RADIUS = 5.0
DEFAULT_BOX = (10.0, 10.0)
DEFAULT_ARROW_LENGTH = 10.0

#: Above this many regions the layer stops building one graphics item per region and draws the
#: whole set as a few aggregate items instead.
#:
#: Measured (`TODO_regions.md`, Phase 5): an interactive region costs 40-60 kB of RSS and ~1.6 ms
#: to build, so 10,000 of them is 0.6 GB and half a minute of loading — and profiling puts more
#: than half of that in `SignalInstance.connect` and pyqtgraph's parent bookkeeping, neither of
#: which can be tuned away while there is one item per region. The same 200,000 regions drawn as
#: one `ScatterPlotItem` cost 0.42 kB each and 0.1 s in total.
#:
#: The cost of the switch is that individual regions can no longer be dragged or right-clicked;
#: they are still listed, edited and saved through the model.
INTERACTIVE_LIMIT = 500

#: Length of an arrowhead barb in the aggregate overlay, as a fraction of the arrow's length.
BULK_BARB = 0.2

#: How long after panning stops before labels come back, in milliseconds.
#:
#: Text is the most expensive thing on the overlay to paint: measured at 34.9 ms per pan frame for
#: 400 labelled regions against 25.4 ms with the labels hidden — 27% of the frame. `plot_catalog`
#: found the same and hides its catalogue labels the same way, with the same delay.
LABEL_REDRAW_DELAY_MS = 200

#: The visible rect is grown by this fraction before culling labels, so text just off the edge —
#: which still paints into the view — is not dropped.
LABEL_CULL_MARGIN = 0.1

#: A ceiling on labels built at once, to stop an enormous set from locking the window up.
#:
#: This is a hang guard, not a judgement about readability: whether a crowd of labels is useful is
#: the user's call, made with **Region ➔ Show Region Labels**, exactly as the catalogue tool offers
#: a *Show Names* checkbox. Labels are culled to the visible rect and hidden while panning, so the
#: cost of a large set falls on the redraw after the view settles — measured at ~0.18 ms per label,
#: so this ceiling is about a second in the worst case.
LABEL_SAFETY_LIMIT = 5000


class _Entry:
    """One model region and the items drawing it.

    `handle` is the item that carries the interaction signals — for most shapes that is the ROI
    itself, for a text region a small draggable target beside the label. `label` is the `TextItem`
    drawing `region.text`, which every shape gets, as ds9 does.
    """

    __slots__ = ("region", "items", "handle", "label", "head")

    def __init__(self, region, items, handle, label=None, head=None):
        self.region = region
        self.items = items
        self.handle = handle
        self.label = label
        #: An arrow's `ArrowItem`. Named rather than found by position in `items`: it used to be
        #: `items[-1]`, which quietly became the label once shapes started drawing one.
        self.head = head


class RegionItemInteraction:
    """Right-click and double-click handling for a region's item.

    Overriding `raiseContextMenu` is not decoration. pyqtgraph's version calls
    `scene().addParentContextMenus()`, which walks up the parent chain — and these items are
    parented to the ImageItem so they inherit the view rotation. `ImageItem.getContextMenus()`
    returns `[None]` when the image is not removable, which pyqtgraph then tries to add to a menu
    and raises `Cannot add object None (type=NoneType) to QMenu`. Every right-click on a region
    hit that, because a region is the first ROI in this application built with `removable=True`,
    which is the only way to reach `raiseContextMenu` at all.

    The item itself puts no UI on screen: it asks the layer, which emits a signal for whoever owns
    the dialogs.
    """

    _region_layer = None
    _region = None

    def bind_region(self, layer, region):
        self._region_layer = layer
        self._region = region

    def mouseClickEvent(self, event):
        bound = self._region is not None and self._region_layer is not None
        moving = getattr(self, 'isMoving', False)

        if bound and not moving and event.button() == Qt.MouseButton.RightButton:
            self.raiseContextMenu(event)
            event.accept()
            return

        if bound and event.button() == Qt.MouseButton.LeftButton and event.double():
            self._region_layer.activate_region(self._region)
            event.accept()
            return

        # Not every base class has one. `pg.ROI` and `pg.TargetItem` define `mouseClickEvent`,
        # but `pg.TextItem` — which a text region's own label subclasses — does not, so calling
        # `super()` unconditionally raised `AttributeError` on any single left click, and
        # pyqtgraph printed the traceback from inside its click dispatch.
        inherited = getattr(super(), 'mouseClickEvent', None)
        if inherited is None:
            event.ignore()
            return
        inherited(event)

    def raiseContextMenu(self, event):
        if self._region is None or self._region_layer is None:
            return
        position = event.screenPos()
        self._region_layer.request_region_menu(
            self._region, QPoint(int(position.x()), int(position.y())))


class RegionCircleROI(RegionItemInteraction, pg.CircleROI):
    pass


class RegionRectROI(RegionItemInteraction, pg.RectROI):
    pass


class RegionLineROI(RegionItemInteraction, pg.LineSegmentROI):
    pass


class RegionTextItem(RegionItemInteraction, pg.TextItem):
    """A label that is its own handle: click, drag or right-click the text itself.

    A text region used to carry a separate crosshair to grab, and since both sat on the same anchor
    the crosshair covered the very text it was there to move. A label is already a visible thing of
    exactly the right size, so it makes a better handle than any marker added beside it.

    `pg.TextItem` is a `GraphicsObject`, so it can take mouse events; it just has to say which
    buttons it wants. The drag follows `pg.TargetItem`'s pattern, and `sigPositionChanged` is named
    to match so the layer's existing wiring picks it up.
    """

    sigPositionChanged = Signal(object)

    def __init__(self, *args, hover_pen=None, **kwargs):
        super().__init__(*args, **kwargs)
        self._hover_pen = hover_pen
        self._moving = False
        self._offset = QPointF(0, 0)

        self.setAcceptedMouseButtons(Qt.MouseButton.LeftButton | Qt.MouseButton.RightButton)
        self.setAcceptHoverEvents(True)
        # Without a marker there is nothing to say the text can be moved, so the cursor says it.
        self.setCursor(Qt.CursorShape.SizeAllCursor)

    def mouseDragEvent(self, event):
        if event.button() != Qt.MouseButton.LeftButton:
            event.ignore()
            return
        event.accept()

        if event.isStart():
            self._offset = self.pos() - self.mapToParent(event.buttonDownPos())
            self._moving = True

        if not self._moving:
            return

        self.setPos(self._offset + self.mapToParent(event.pos()))
        self.sigPositionChanged.emit(self)

        if event.isFinish():
            self._moving = False

    def hoverEvent(self, event):
        """Outline the text on hover, so it is discoverable as something to grab."""
        wanted = None if event.isExit() else self._hover_pen
        if self.border != wanted:
            self.border = pg.mkPen(wanted) if wanted is not None else pg.mkPen(None)
            self.update()


class RegionLayer(QObject):
    """Owns the regions drawn on one viewer.

    Model regions are dataclasses compared by value, so two identical circles are `==`. Lookups
    here are therefore always by identity (`is`), never by equality.
    """

    #: The set of regions or their geometry changed. Carries no payload: a listener redraws.
    regions_changed = Signal()
    #: A region was just created by drawing. Payload is the new region.
    region_drawn = Signal(object)
    #: Drawing mode ended, whether by finishing, cancelling, or being interrupted.
    draw_mode_changed = Signal(bool)
    #: A region was double-clicked: open its properties. Payload is the region.
    region_activated = Signal(object)
    #: A region was right-clicked. Payload is `(region, global QPoint)`. The layer draws no menu
    #: itself; whoever owns the dialogs answers this.
    region_menu_requested = Signal(object, object)
    #: Switched between per-region items and the aggregate overlay. Payload is `(bulk, count)`,
    #: so the window can say why regions stopped being draggable.
    render_mode_changed = Signal(bool, int)
    #: Labels stopped or started being drawn because of how many are in view. Payload is the number
    #: in view, or 0 when they are showing again, so the window can say why they vanished.
    labels_suppressed = Signal(int)

    def __init__(self, image_viewer, parent=None):
        super().__init__(parent)
        self.viewer = image_viewer
        self._entries = []
        #: id(region) -> entry, so a lookup is not a scan. See `_entry_for`.
        self._by_id = {}
        self._draw_kind = None
        self._draw_attributes = {}
        #: Supplied by `begin_draw` for a text region; called after the click for its label.
        self._ask_text = None
        self._draw_anchor = None
        self._draw_preview = None
        #: Set while items are being re-placed from the model, so their change signals are not
        #: mistaken for the user editing them.
        self._placing = False
        #: How many regions may have their own graphics items. See INTERACTIVE_LIMIT.
        self.interactive_limit = INTERACTIVE_LIMIT
        #: True while the whole set is drawn as a few aggregate items rather than one each.
        self._bulk = False
        self._bulk_items = []
        #: Labels drawn over the aggregate overlay; rebuilt for whatever is in view.
        self._bulk_labels = []
        #: Items taken out of the scene but not yet released. See `_destroy_items`.
        self._retired = []
        #: Whether labels are drawn at all. The user's choice, not the layer's.
        self._labels_visible = True
        #: True while the safety ceiling is suppressing labels.
        self._labels_hidden_for_density = False

        #: Set while the view is being panned or zoomed, so labels stay hidden until it settles.
        self._panning = False
        self._label_timer = QTimer(self)
        self._label_timer.setSingleShot(True)
        self._label_timer.timeout.connect(self._labels_settled)

        if image_viewer is not None:
            image_viewer.display_changed.connect(self.refresh)
            slider = getattr(image_viewer, 'slider_slice', None)
            if slider is not None:
                # Channel visibility is cheap to update and should follow the slider live.
                slider.valueChanged.connect(self.update_channel_visibility)
            view = image_viewer.imv.getView()
            if view is not None:
                view.sigRangeChanged.connect(self._on_view_range_changed)
            image_viewer.imv.scene.sigMouseClicked.connect(self._on_scene_clicked)

    # ------------------------------------------------------------------ the model

    @property
    def regions(self):
        """The regions, in drawing order. A copy: mutate through this class, not the list."""
        return [entry.region for entry in self._entries]

    def __len__(self):
        return len(self._entries)

    def __iter__(self):
        return iter(self.regions)

    def add(self, region, notify=True):
        """Add a region and draw it. Returns the region."""
        entry = self._register(region)
        if self._bulk or len(self._entries) > self.interactive_limit:
            # Either already aggregated, or this is the region that tips it over.
            self.render()
        else:
            self._build_items(entry)
        if notify:
            self.regions_changed.emit()
        return region

    def _register(self, region):
        entry = _Entry(region, [], None)
        self._entries.append(entry)
        self._by_id[id(region)] = entry
        return entry

    def remove(self, region, notify=True):
        """Remove `region` (by identity) and its items. Unknown regions are ignored."""
        if not self._unregister(region):
            return False
        if self._bulk:
            self.render()
        if notify:
            self.regions_changed.emit()
        return True

    def remove_many(self, regions, notify=True):
        """Remove several regions with one redraw.

        Deleting a selection one at a time would rebuild the aggregate overlay for each, which is
        the difference between one redraw and thousands.
        """
        removed = [region for region in list(regions) if self._unregister(region)]
        if not removed:
            return 0
        if self._bulk:
            self.render()
        if notify:
            self.regions_changed.emit()
        return len(removed)

    def _unregister(self, region):
        entry = self._by_id.pop(id(region), None)
        if entry is None:
            return False
        self._destroy_items(entry)
        self._entries.remove(entry)
        return True

    def clear(self, notify=True):
        for entry in self._entries:
            self._destroy_items(entry)
        self._entries = []
        self._by_id.clear()
        self._clear_bulk_items()
        self._set_bulk(False)
        if notify:
            self.regions_changed.emit()

    def set_regions(self, regions):
        """Replace everything with `regions`, e.g. after loading a file.

        Registers them all first and draws once. Looping `add` would rebuild the aggregate overlay
        per region, which is what made loading a catalogue-sized file take minutes.
        """
        self.clear(notify=False)
        for region in regions:
            self._register(region)
        self.render()
        self.regions_changed.emit()

    def _entry_for(self, region):
        """The entry drawing `region`, or None.

        Keyed by `id(region)` rather than by the region itself: model regions are dataclasses
        compared by value, so two identical circles are `==` (and unhashable). An entry holds a
        strong reference to its region, so the id cannot be recycled while it is registered. A dict
        rather than a scan because deleting a selection was otherwise quadratic.
        """
        return self._by_id.get(id(region))

    def item_for(self, region):
        """The interactive item drawing `region`, or None. For tests and the region list."""
        entry = self._entry_for(region)
        return None if entry is None else entry.handle

    def label_for(self, region):
        """The `TextItem` drawing `region.text`, or None if it has no label."""
        entry = self._entry_for(region)
        return None if entry is None else entry.label

    def activate_region(self, region):
        """Called by an item on a double-click. Asks for its properties to be edited."""
        self.region_activated.emit(region)

    def request_region_menu(self, region, global_position):
        """Called by an item on a right-click, with a screen position for the menu."""
        self.region_menu_requested.emit(region, global_position)

    # ---------------------------------------------------------------- placement

    @property
    def bulk(self):
        """True while the set is drawn as aggregate items and individual regions are not editable."""
        return self._bulk

    def render(self):
        """Draw everything, choosing between per-region items and the aggregate overlay.

        The mode follows the region count, so crossing `interactive_limit` in either direction
        rebuilds. Both paths derive their geometry from the model, which is what makes a flip or a
        rotation safe in either mode.
        """
        if self.viewer is None:
            return

        wanted = len(self._entries) > self.interactive_limit
        # The mode is set *before* drawing: `refresh()` branches on it, so leaving it stale here
        # made dropping back below the limit rebuild the aggregate overlay it had just cleared.
        changed = wanted != self._bulk
        self._bulk = wanted

        if wanted:
            for entry in self._entries:
                self._destroy_items(entry)
            self._draw_bulk()
        else:
            self._clear_bulk_items()
            for entry in self._entries:
                if not entry.items:
                    self._build_items(entry)
            self.refresh()

        if changed:
            # Emitted after the drawing, so a listener sees the finished state.
            self.render_mode_changed.emit(wanted, len(self._entries))

    def _set_bulk(self, bulk):
        if bulk != self._bulk:
            self._bulk = bulk
            self.render_mode_changed.emit(bulk, len(self._entries))

    def refresh(self):
        """Re-derive every item's geometry from the model.

        Called whenever the displayed plane changes shape or orientation. Rebuilding from the
        model rather than transforming the items means a rotation cannot accumulate error, and a
        region can never end up somewhere the array does not go.
        """
        if self.viewer is None:
            return
        if self._bulk:
            self._draw_bulk()
            return

        self._placing = True
        try:
            for entry in self._entries:
                self._place(entry)
        finally:
            self._placing = False
        self.update_channel_visibility()

    def update_channel_visibility(self):
        """Hide regions whose `z_range` excludes the channel on screen."""
        if self.viewer is None:
            return
        if self._bulk:
            # Filtering happens while the aggregate arrays are built.
            self._draw_bulk()
            return
        current = self._current_channel()
        rect = self._view_rect()
        show_labels = self._labels_fit(self._labels_in_view(current, rect))
        for entry in self._entries:
            self._apply_visibility(entry, current, rect, show_labels=show_labels)

    def _labels_in_view(self, channel, rect):
        """How many labels would be drawn right now, before the density rule is applied."""
        return sum(1 for entry in self._entries
                   if entry.label is not None
                   and entry.region.visible
                   and self._in_channel_range(entry.region, channel)
                   and self._label_in_view(entry.label, rect, ignore_panning=True))

    @property
    def labels_visible(self):
        """Whether region labels are drawn at all."""
        return self._labels_visible

    def set_labels_visible(self, visible):
        """Turn labels on or off, as the catalogue tool's *Show Names* checkbox does."""
        visible = bool(visible)
        if visible == self._labels_visible:
            return
        self._labels_visible = visible
        if self._bulk:
            self._draw_bulk()
        else:
            self.update_channel_visibility()

    def _labels_fit(self, count):
        """True if `count` labels can be drawn, and tell the window when that answer changes.

        Only the safety ceiling refuses here. How many labels are worth looking at is the user's
        decision, made with the *Show Region Labels* toggle.
        """
        if not self._labels_visible:
            return False
        too_many = count > LABEL_SAFETY_LIMIT
        if too_many != self._labels_hidden_for_density:
            self._labels_hidden_for_density = too_many
            self.labels_suppressed.emit(count if too_many else 0)
        return not too_many

    # ------------------------------------------------------------ aggregate overlay

    def _clear_bulk_items(self):
        # Retires, never releases — see `_destroy_items` for why the flush cannot happen here.
        for item in self._bulk_items + self._bulk_labels:
            self._remove_from_scene(item)
            self._retired.append(item)
        self._bulk_items = []
        self._bulk_labels = []
        if self._retired:
            QTimer.singleShot(0, self._drop_retired)

    def _draw_bulk(self):
        """Draw the whole set as a few items: one pair per distinct style.

        Grouping by style keeps the pens uniform, which is what lets pyqtgraph batch. A set with
        thousands of *distinct* colours would end up back at one item each, and there is no way
        around that short of dropping the colours.

        Labels *are* drawn, for whatever is in view: a catalogue of named stars is mostly its
        names, so leaving them out made the overlay far less useful than it looked. They cannot be
        batched — one `TextItem` each — so the density rule in `_labels_fit` decides whether to draw
        them at all, and only those inside the visible rect are built. Text regions also get a cross
        so their positions show even when the labels do not.
        """
        self._clear_bulk_items()
        if self.viewer is None or not self._entries:
            return

        channel = self._current_channel()
        groups = {}
        labelled = []
        for entry in self._entries:
            region = entry.region
            if not region.visible or not self._in_channel_range(region, channel):
                continue
            placed = self._to_item(region.x, region.y)
            if placed is None:
                continue

            if region.text:
                labelled.append((region, placed))

            group = groups.setdefault((region.color, region.line_width, region.dash),
                                      {"circles": [], "marks": [], "paths": []})
            angle = self._display_angle(getattr(region, 'angle', 0.0))

            if isinstance(region, Circle):
                group["circles"].append((placed[0], placed[1], region.radius * 2.0))
            elif isinstance(region, Box):
                group["paths"].append(_box_outline(placed, region.width, region.height, angle))
            elif isinstance(region, Arrow):
                group["paths"].append(_arrow_outline(placed, region.length, angle))
            elif isinstance(region, Text):
                group["marks"].append(placed)

        for (colour, width, dash), group in groups.items():
            pen = pg.mkPen(resolve_color(colour), width=width,
                           style=Qt.PenStyle.DashLine if dash else Qt.PenStyle.SolidLine)
            for item in _bulk_items_for(group, colour, pen):
                item.setZValue(20)
                self._add_to_scene(item)
                self._bulk_items.append(item)

        self._draw_bulk_labels(labelled)

    def _draw_bulk_labels(self, labelled):
        """Draw the labels of an aggregated set, for those in view and if there are few enough."""
        if self._panning:
            return

        rect = self._view_rect()
        in_view = [(region, place) for region, place in labelled
                   if rect is None or rect.contains(QPointF(*place))]
        if not self._labels_fit(len(in_view)):
            return

        for region, place in in_view:
            label = _label_item(region, anchor=(0.5, 1.0))
            label.setPos(place[0], place[1] + _label_offset(region))
            label.setZValue(21)
            self._add_to_scene(label)
            self._bulk_labels.append(label)

    def _current_channel(self):
        if getattr(self.viewer, 'transposed_data', None) is None:
            return 0
        return self.viewer.current_z()

    def _apply_visibility(self, entry, channel, view_rect=None, show_labels=True):
        """Show or hide one entry's items. Per-entry so adding a region is not O(N).

        Adding used to end by refreshing *every* region's visibility, which made loading a
        catalogue-sized region file quadratic: 10,000 regions took 48 seconds, nearly all of it
        here.

        A label is held to a stricter test than the shape it belongs to: hidden outright while the
        view is moving, and otherwise only shown if it falls inside the visible rect. Text is the
        most expensive thing here to paint (see `LABEL_REDRAW_DELAY_MS`).
        """
        visible = entry.region.visible and self._in_channel_range(entry.region, channel)
        for item in entry.items:
            if item is entry.label:
                item.setVisible(visible and show_labels
                                and self._label_in_view(item, view_rect))
            else:
                item.setVisible(visible)

    def _label_in_view(self, label, view_rect=None, ignore_panning=False):
        """True if `label` is worth painting: not mid-pan, and inside the visible rect."""
        if self._panning and not ignore_panning:
            return False
        rect = view_rect if view_rect is not None else self._view_rect()
        if rect is None:
            return True
        return rect.contains(self._label_view_position(label))

    def _view_rect(self):
        try:
            rect = self.viewer.imv.getView().viewRect()
        except Exception:
            return None
        margin_x = rect.width() * LABEL_CULL_MARGIN
        margin_y = rect.height() * LABEL_CULL_MARGIN
        return rect.adjusted(-margin_x, -margin_y, margin_x, margin_y)

    def _label_view_position(self, label):
        """A label's anchor in view coordinates.

        Labels are parented to the ImageItem, so their own position is in *its* coordinates —
        which differ from the view's whenever a view rotation is applied, that being a transform on
        the ImageItem.
        """
        image_item = self.viewer.imv.getImageItem()
        if image_item is None:
            return label.pos()
        try:
            return image_item.mapToView(label.pos())
        except Exception:
            return label.pos()

    def _on_view_range_changed(self):
        """Hide labels the moment the view starts moving; a debounce brings back the visible ones.

        Panning with labels drawn costs 27% more per frame than without them, and every frame of a
        drag repaints all of them. `plot_catalog` does the same for catalogue labels.
        """
        if self.viewer is None:
            return

        has_labels = bool(self._bulk_labels) or any(
            entry.label is not None for entry in self._entries)
        if not has_labels:
            return

        self._panning = True
        for label in self._bulk_labels:
            label.setVisible(False)
        for entry in self._entries:
            if entry.label is not None:
                entry.label.setVisible(False)
        self._label_timer.start(LABEL_REDRAW_DELAY_MS)

    def _labels_settled(self):
        """The view has stopped moving: bring back the labels that are actually on screen."""
        self._panning = False
        self.update_channel_visibility()

    @staticmethod
    def _in_channel_range(region, channel):
        if region.z_range is None:
            return True
        return region.z_range[0] <= channel <= region.z_range[1]

    def _to_item(self, x, y):
        """Orig coordinates to ImageItem coordinates, or None if there is no data.

        The half pixel is the ImageItem convention: pixel `i` is drawn across `[i, i+1)`, so its
        centre — which is what an orig coordinate names — is at `i + 0.5`.
        """
        shown = self.viewer.orig_to_display(x, y)
        if shown is None:
            return None
        return coords.index_to_item(shown[0]), coords.index_to_item(shown[1])

    def _from_item(self, x, y):
        """ImageItem coordinates back to orig coordinates, or None."""
        return self.viewer.display_to_orig(coords.item_to_index(x), coords.item_to_index(y))

    def _display_angle(self, angle):
        return coords.orig_angle_to_display(
            angle, flip=self.viewer.flip, rot_angle=self.viewer.rot_angle)

    def _orig_angle(self, angle):
        return coords.display_angle_to_orig(
            angle, flip=self.viewer.flip, rot_angle=self.viewer.rot_angle)

    def _place(self, entry):
        """Set one entry's item geometry from its region. Caller holds `_placing`."""
        region = entry.region
        centre = self._to_item(region.x, region.y)
        if centre is None or not entry.items:
            return
        cx, cy = centre

        if isinstance(region, Circle):
            roi = entry.handle
            roi.setSize([region.radius * 2.0, region.radius * 2.0], update=False)
            roi.setPos([cx - region.radius, cy - region.radius], update=False)
            roi.stateChanged(finish=False)

        elif isinstance(region, Box):
            roi = entry.handle
            roi.setAngle(0, update=False)
            roi.setSize([region.width, region.height], update=False)
            roi.setPos([cx - region.width / 2.0, cy - region.height / 2.0], update=False)
            # Normalised centre, not local pixels: `centerLocal=[0.5, 0.5]` would rotate about a
            # point half a pixel from the corner and walk the box across the image.
            roi.setAngle(self._display_angle(region.angle), center=[0.5, 0.5], update=False)
            roi.stateChanged(finish=False)

        elif isinstance(region, Arrow):
            roi = entry.handle
            angle = math.radians(self._display_angle(region.angle))
            tip = (cx + region.length * math.cos(angle), cy + region.length * math.sin(angle))
            roi.setPos([0, 0], update=False)
            handles = roi.getHandles()
            roi.movePoint(handles[0], QPointF(cx, cy), finish=False)
            roi.movePoint(handles[1], QPointF(*tip), finish=False)
            self._place_arrow_head(entry, (cx, cy), tip)

        # A text region's label *is* its handle, so `_place_label` positions it below.
        self._place_label(entry, (cx, cy))

    def _place_label(self, entry, centre):
        """Put a shape's label beside it, or a text region's label on its anchor.

        A text region's label *is* the region, so it sits on the point; any other shape's label
        sits just clear of it, which is where ds9 puts one.
        """
        label = entry.label
        if label is None:
            return
        region = entry.region
        cx, cy = centre

        if isinstance(region, Text):
            label.setPos(cx, cy)
        else:
            offset = _label_offset(region)
            label.setPos(cx, cy + offset)

        # Only a text region's angle turns its text. A box's angle rotates the *box* and an
        # arrow's is its direction, so applying either to the label drew a shape's caption on its
        # side — as ds9 does not, `textangle` there being a property of a text region alone.
        if isinstance(region, Text):
            turn = self._display_angle(region.angle) - self._display_angle(0.0)
        else:
            turn = 0.0
        label.setAngle(turn)

    def _place_arrow_head(self, entry, tail, tip):
        """Point the arrow head along the line and put it at the far end."""
        head = entry.head
        if head is None:
            return
        direction = math.degrees(math.atan2(tip[1] - tail[1], tip[0] - tail[0]))
        head.setPos(*tip)
        head.setStyle(angle=_arrow_head_angle(direction))

    # ------------------------------------------------------------------- items

    def _pen(self, region, hover=False):
        style = Qt.PenStyle.DashLine if region.dash else Qt.PenStyle.SolidLine
        width = region.line_width + (2 if hover else 0)
        # Resolved, not passed through: ds9's `green` is neon, Qt's is dark (see DS9_COLORS).
        return pg.mkPen(resolve_color(region.color), width=width, style=style)

    def _add_to_scene(self, item):
        """Parent to the ImageItem so the view rotation is inherited for free."""
        image_item = self.viewer.imv.getImageItem()
        if image_item is not None:
            item.setParentItem(image_item)
        else:
            self.viewer.imv.getView().addItem(item)

    def _remove_from_scene(self, item):
        """`ViewBox.removeItem` is removal; `setParentItem(None)` only re-parents (B7)."""
        try:
            self.viewer.imv.getView().removeItem(item)
            return
        except Exception:
            pass
        try:
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)
            else:
                item.setParentItem(None)
        except Exception:
            pass

    def _build_items(self, entry):
        region = entry.region
        pen, hover_pen = self._pen(region), self._pen(region, hover=True)
        entry.label = entry.head = None

        if isinstance(region, Circle):
            roi = RegionCircleROI([0, 0], radius=max(region.radius, MIN_SIZE / 2),
                                  pen=pen, hoverPen=hover_pen, removable=True)
            entry.items, entry.handle = [roi], roi

        elif isinstance(region, Box):
            roi = RegionRectROI([0, 0],
                                [max(region.width, MIN_SIZE), max(region.height, MIN_SIZE)],
                                pen=pen, hoverPen=hover_pen, removable=True)
            roi.addRotateHandle([1, 0], [0.5, 0.5])
            entry.items, entry.handle = [roi], roi

        elif isinstance(region, Arrow):
            roi = RegionLineROI([[0, 0], [max(region.length, MIN_SIZE), 0]],
                                pen=pen, hoverPen=hover_pen, removable=True)
            head = pg.ArrowItem(angle=180, headLen=12, brush=resolve_color(region.color),
                                pen=pen,
                                pxMode=True)
            entry.items, entry.handle, entry.head = [roi, head], roi, head

        elif isinstance(region, Text):
            # The label is the handle: one item, nothing drawn on top of the text.
            label = _label_item(region, anchor=(0.5, 0.5), hover_pen=hover_pen)
            entry.items, entry.handle, entry.label = [label], label, label

        else:
            entry.items, entry.handle = [], None
            return

        if entry.label is None and region.text:
            # ds9 draws a shape's `text={...}` beside it, so the label fields mean something for
            # every shape rather than only for a text region.
            entry.label = _label_item(region, anchor=(0.5, 1.0))
            entry.items.append(entry.label)

        for item in entry.items:
            item.setZValue(20)
            self._add_to_scene(item)

        if entry.handle is not None:
            entry.handle.bind_region(self, region)

        self._connect(entry)
        self._placing = True
        try:
            self._place(entry)
        finally:
            self._placing = False
        self._apply_visibility(entry, self._current_channel(), self._view_rect())

    def _connect(self, entry):
        handle = entry.handle
        if handle is None:
            return
        if hasattr(handle, 'sigRegionChanged'):
            handle.sigRegionChanged.connect(lambda *_, e=entry: self._item_edited(e))
        if hasattr(handle, 'sigPositionChanged'):
            handle.sigPositionChanged.connect(lambda *_, e=entry: self._item_edited(e))
        if hasattr(handle, 'sigRemoveRequested'):
            # pyqtgraph's own right-click "Remove" entry, which `removable=True` provides.
            handle.sigRemoveRequested.connect(lambda *_, e=entry: self.remove(e.region))

    def _destroy_items(self, entry):
        """Take an entry's items out of the scene, and defer letting go of them.

        Dropping the last Python reference to a QGraphicsItem hands its deletion to the garbage
        collector, which then runs at an arbitrary later allocation — including *inside* the
        construction of the replacement items. That is a reproducible segfault: destroying and
        rebuilding one region crashed in `pg.ROI.addScaleHandle` while the collector was freeing the
        items just discarded. Holding them until a later, quieter moment removes the race.

        Nothing is released *here*, only added. Flushing the previous batch first looks harmless
        and reintroduces the crash exactly: `clear()` destroys every entry in a loop, so entry 2's
        flush frees entry 1's items, and the collector then runs inside `addRotateHandle` building
        the replacements. Two segfaults in three runs (`BUGS.md` M18). The timer owns the release.
        """
        for item in entry.items:
            self._remove_from_scene(item)
            self._retired.append(item)
        entry.items, entry.handle, entry.label, entry.head = [], None, None, None
        if self._retired:
            QTimer.singleShot(0, self._drop_retired)

    def _drop_retired(self):
        """Let go of items removed earlier. Safe here: nothing is being constructed.

        The collection is forced rather than left to the next allocation that happens to trip the
        threshold: a QGraphicsItem sits in a reference cycle with its children and its scene, so
        dropping the list frees nothing by refcount alone. Running it at this known-quiet point is
        the whole purpose of the deferral.
        """
        if not self._retired:
            return
        self._retired = []
        gc.collect()

    # ----------------------------------------------------------- user editing

    def _item_edited(self, entry):
        """Read a dragged item's geometry back into its region."""
        if self._placing or self.viewer is None:
            return
        region, handle = entry.region, entry.handle
        if handle is None:
            return

        if isinstance(region, Circle):
            size = handle.size()
            radius = float(size[0]) / 2.0
            centre = self._from_item(handle.pos()[0] + radius, handle.pos()[1] + radius)
            if centre is None:
                return
            region.x, region.y = centre
            region.radius = max(radius, MIN_SIZE / 2)

        elif isinstance(region, Box):
            width, height = (float(v) for v in handle.size())
            local_centre = handle.mapToParent(QPointF(width / 2.0, height / 2.0))
            centre = self._from_item(local_centre.x(), local_centre.y())
            if centre is None:
                return
            region.x, region.y = centre
            region.width, region.height = max(width, MIN_SIZE), max(height, MIN_SIZE)
            region.angle = self._orig_angle(handle.angle())

        elif isinstance(region, Arrow):
            points = [handle.mapToParent(h.pos()) for h in handle.getHandles()]
            tail = self._from_item(points[0].x(), points[0].y())
            tip = self._from_item(points[1].x(), points[1].y())
            if tail is None or tip is None:
                return
            dx, dy = tip[0] - tail[0], tip[1] - tail[1]
            region.x, region.y = tail
            region.length = max(math.hypot(dx, dy), MIN_SIZE)
            region.angle = math.degrees(math.atan2(dy, dx)) % 360.0
            self._placing = True
            try:
                self._place_arrow_head(entry, (points[0].x(), points[0].y()),
                                      (points[1].x(), points[1].y()))
            finally:
                self._placing = False

        elif isinstance(region, Text):
            position = handle.pos()
            moved = self._from_item(position.x(), position.y())
            if moved is None:
                return
            region.x, region.y = moved

        self.regions_changed.emit()

    def restyle(self, region):
        """Re-apply a region's colour, width, dash, text and visibility after it was edited.

        Updates the existing items rather than replacing them. Rebuilding to change a pen is both
        wasteful and was the crash above; it is only needed when the *set* of items changes, which
        is when a label appears or disappears.
        """
        entry = self._entry_for(region)
        if entry is None:
            return

        if self._bulk:
            self._draw_bulk()
            self.regions_changed.emit()
            return

        wants_label = bool(region.text)
        if wants_label != (entry.label is not None):
            self._destroy_items(entry)
            self._build_items(entry)
        else:
            self._restyle_in_place(entry)

        self.regions_changed.emit()

    def _restyle_in_place(self, entry):
        """Push a region's colour, width, dash and font onto the items already drawn."""
        region = entry.region
        pen, hover_pen = self._pen(region), self._pen(region, hover=True)

        for item in entry.items:
            if hasattr(item, 'setPen') and item is not entry.label:
                item.setPen(pen)
            if hasattr(item, 'hoverPen'):
                item.hoverPen = hover_pen

        if entry.head is not None:
            entry.head.setStyle(brush=resolve_color(region.color), pen=pen)

        if entry.label is not None:
            entry.label.setText(region.text, color=resolve_color(region.color))
            entry.label.setFont(_font_of_size(region.font_size))

        self._placing = True
        try:
            self._place(entry)
        finally:
            self._placing = False
        self._apply_visibility(entry, self._current_channel(), self._view_rect())

    # -------------------------------------------------------------- drawing

    @property
    def drawing(self):
        return self._draw_kind is not None

    @property
    def draw_kind(self):
        """The shape a drag would draw, or None. Lets a toolbar show which tool is armed."""
        return self._draw_kind

    def begin_draw(self, kind, ask_text=None, **attributes):
        """Enter drawing mode for `kind`, applying `attributes` to whatever is drawn.

        A circle, box or arrow is dragged out. A text region is *clicked* into place: there is
        nothing to drag, since a label is drawn horizontally, and a drag would only suggest
        otherwise.

        `ask_text` is called once the click has landed, with the position in orig coordinates, and
        returns the label — or something empty to place nothing. Asking afterwards puts the two
        steps in the order the user thinks in: point at the thing, then say what it is called.
        Keeping it a callback is what lets this class stay free of dialogs.
        """
        if kind not in DRAWABLE:
            raise ValueError(f"cannot draw {kind!r}; expected one of {list(DRAWABLE)}")
        if self.viewer is None:
            return

        self._draw_kind = kind
        self._draw_attributes = dict(attributes)
        self._ask_text = ask_text
        self._draw_anchor = None
        self.viewer.begin_exclusive_drag(self, self._drag_event, on_revoked=self._draw_revoked)
        self.draw_mode_changed.emit(True)

    def cancel_draw(self):
        if not self.drawing:
            return
        self._clear_preview()
        self._draw_kind = None
        self._ask_text = None
        self._draw_anchor = None
        if self.viewer is not None:
            self.viewer.end_exclusive_drag(self)
        self.draw_mode_changed.emit(False)

    def _draw_revoked(self):
        """Something else took the drag; drop out of drawing mode without fighting for it."""
        self._clear_preview()
        self._draw_kind = None
        self._ask_text = None
        self._draw_anchor = None
        self.draw_mode_changed.emit(False)

    def place_at(self, item_x, item_y):
        """Create the pending region at one point, for shapes with nothing to drag."""
        return self.place(self._draw_kind, item_x, item_y, ask_text=self._ask_text,
                          **self._draw_attributes)

    def place(self, kind, item_x, item_y, ask_text=None, **attributes):
        """Create a default-sized region of `kind` at one point, without drawing it out.

        This is what the right-click menu uses: pointing at a feature and getting a region there is
        quicker than dragging one out, and a default size is easy to adjust afterwards. `ask_text`
        is called for a text region's label, as in `begin_draw`.
        """
        if kind not in DRAWABLE:
            raise ValueError(f"cannot place {kind!r}; expected one of {list(DRAWABLE)}")

        origin = self._from_item(item_x, item_y)
        if origin is None:
            return None

        attributes = dict(attributes)
        if kind == "text":
            label = attributes.pop("text", "")
            if not label and ask_text is not None:
                label = (ask_text(*origin) or "").strip()
                if not label:
                    return None        # the user declined; a text region needs text
            region = Text(x=origin[0], y=origin[1], text=label or "Label", **attributes)
        elif kind == "circle":
            region = Circle(x=origin[0], y=origin[1], radius=DEFAULT_RADIUS, **attributes)
        elif kind == "box":
            region = Box(x=origin[0], y=origin[1], width=DEFAULT_BOX[0],
                         height=DEFAULT_BOX[1], **attributes)
        else:
            region = Arrow(x=origin[0], y=origin[1], length=DEFAULT_ARROW_LENGTH, angle=0.0,
                           **attributes)
        return self._drawn(region)

    def _drawn(self, region):
        """Add a region the user has just drawn, and announce it exactly once.

        Both the drag and the click paths end here. They each used to emit `region_drawn`
        themselves *and* call `place_at`, which emitted too, so a click-sized drag announced the
        same region twice.
        """
        self.add(region)
        self.region_drawn.emit(region)
        return region

    def _on_scene_clicked(self, event):
        """Place a text region where the user clicked.

        A click without movement never reaches `mouseDragEvent`, so text used to need a small drag
        to appear at all. This listens for the click itself, and does nothing unless the text tool
        is the one armed.
        """
        if self._draw_kind != "text" or self.viewer is None:
            return
        if event.button() != Qt.MouseButton.LeftButton:
            return

        image_item = self.viewer.imv.getImageItem()
        if image_item is None:
            return

        position = image_item.mapFromScene(event.scenePos())
        self.place_at(position.x(), position.y())
        self.cancel_draw()
        event.accept()

    def _drag_event(self, event):
        """The view's drag handler while drawing. Runs on the GUI thread."""
        if event.button() != Qt.MouseButton.LeftButton or self.viewer is None:
            event.ignore()
            return

        image_item = self.viewer.imv.getImageItem()
        if image_item is None:
            event.ignore()
            return

        if self._draw_kind == "text":
            # Placed by `_on_scene_clicked`. Swallowing the drag here means dragging draws no
            # misleading rubber-band line for something that is only ever a point.
            event.accept()
            return

        position = image_item.mapFromScene(event.scenePos())
        if event.isStart():
            start = image_item.mapFromScene(event.buttonDownScenePos())
            self._draw_anchor = (start.x(), start.y())
            event.accept()
            return

        if self._draw_anchor is None:
            event.ignore()
            return

        if event.isFinish():
            self._finish_draw(self._draw_anchor, (position.x(), position.y()))
            self._clear_preview()
            self._draw_anchor = None
            self.cancel_draw()
            event.accept()
            return

        self._update_preview(self._draw_anchor, (position.x(), position.y()))
        event.accept()

    def _finish_draw(self, start, end):
        """Turn a completed drag into a region. A drag of nothing becomes a single-click place."""
        dx, dy = end[0] - start[0], end[1] - start[1]
        if math.hypot(dx, dy) < MIN_SIZE:
            return self.place_at(*start)

        kind, attributes = self._draw_kind, dict(self._draw_attributes)

        if kind == "arrow":
            tail = self._from_item(*start)
            tip = self._from_item(*end)
            if tail is None or tip is None:
                return None
            return self._drawn(Arrow.from_points(tail[0], tail[1], tip[0], tip[1],
                                               **attributes))

        if kind == "circle":
            centre = self._from_item(*start)
            if centre is None:
                return None
            radius = math.hypot(dx, dy)
            return self._drawn(Circle(x=centre[0], y=centre[1], radius=radius, **attributes))

        if kind == "box":
            middle = self._from_item((start[0] + end[0]) / 2.0, (start[1] + end[1]) / 2.0)
            if middle is None:
                return None
            # The drag is axis-aligned on screen, so the box's angle in orig space is whatever
            # angle appears as zero on screen under the current flip and rotation.
            return self._drawn(Box(x=middle[0], y=middle[1], width=abs(dx), height=abs(dy),
                                   angle=self._orig_angle(0.0), **attributes))

        return self.place_at(*start)

    # ---------------------------------------------------------------- preview

    def _update_preview(self, start, end):
        """A dashed outline following the drag, so the shape is visible before it exists."""
        if self._draw_preview is None:
            self._draw_preview = pg.PlotDataItem(
                pen=pg.mkPen(self._draw_attributes.get("color", "yellow"),
                             width=1, style=Qt.PenStyle.DashLine))
            self._draw_preview.setZValue(25)
            self._add_to_scene(self._draw_preview)

        xs, ys = _preview_outline(self._draw_kind, start, end)
        self._draw_preview.setData(xs, ys)

    def _clear_preview(self):
        if self._draw_preview is not None:
            self._remove_from_scene(self._draw_preview)
            self._draw_preview = None


def _preview_outline(kind, start, end):
    """Points tracing the shape a drag would produce."""
    (x0, y0), (x1, y1) = start, end

    if kind == "circle":
        radius = math.hypot(x1 - x0, y1 - y0)
        angles = [math.radians(a) for a in range(0, 361, 10)]
        return ([x0 + radius * math.cos(a) for a in angles],
                [y0 + radius * math.sin(a) for a in angles])

    if kind == "box":
        return ([x0, x1, x1, x0, x0], [y0, y0, y1, y1, y0])

    return ([x0, x1], [y0, y1])


def _font_of_size(point_size):
    from PySide6.QtGui import QFont

    font = QFont()
    font.setPointSize(max(1, int(point_size)))
    return font


def _arrow_head_angle(direction_deg):
    """The `ArrowItem` angle that makes a head point along `direction_deg` on screen.

    Two conversions collapse into one subtraction, and getting it wrong left the head pointing
    somewhere other than its own line for anything but a horizontal arrow:

    - `ArrowItem` points *opposite* its `angle` option — measured: `head = angle + 180`.
    - The head is `pxMode=True`, which sets `ItemIgnoresTransformations`, so it is painted in raw
      screen coordinates where **y increases downward** — while the view, and therefore
      `direction_deg`, has y upward. The vertical component has to be mirrored.

    Hence `180 - direction` rather than `direction + 180`; the two agree only at 0° and 180°, which
    is why a horizontal arrow looked right. The PA compass uses `angle + 180` correctly because its
    arrows are `pxMode=False` and so *are* transformed with the view.
    """
    return (180.0 - float(direction_deg)) % 360.0


def _box_outline(centre, width, height, angle_deg):
    """The four corners of a rotated box, closed, as `[(x, y), ...]` in ImageItem coordinates."""
    cx, cy = centre
    radians = math.radians(angle_deg)
    cos, sin = math.cos(radians), math.sin(radians)
    half_w, half_h = width / 2.0, height / 2.0
    corners = ((-half_w, -half_h), (half_w, -half_h), (half_w, half_h), (-half_w, half_h))
    points = [(cx + dx * cos - dy * sin, cy + dx * sin + dy * cos) for dx, dy in corners]
    return points + [points[0]]


def _arrow_outline(centre, length, angle_deg):
    """An arrow as a single polyline: tail to tip, then back out along each barb.

    Drawn in one stroke so a whole set of arrows fits in one item; the barbs are what make it read
    as an arrow rather than a line, since there is no `ArrowItem` per region here.
    """
    x0, y0 = centre
    radians = math.radians(angle_deg)
    tip = (x0 + length * math.cos(radians), y0 + length * math.sin(radians))
    barb = length * BULK_BARB
    left = math.radians(angle_deg + 150.0)
    right = math.radians(angle_deg - 150.0)
    return [
        (x0, y0), tip,
        (tip[0] + barb * math.cos(left), tip[1] + barb * math.sin(left)), tip,
        (tip[0] + barb * math.cos(right), tip[1] + barb * math.sin(right)),
    ]


def _bulk_items_for(group, colour, pen):
    """The one or two items drawing a whole style group."""
    items = []
    if group["circles"]:
        xs, ys, sizes = (np.array(values, dtype=float)
                         for values in zip(*group["circles"], strict=True))
        items.append(pg.ScatterPlotItem(x=xs, y=ys, size=sizes, symbol='o', pen=pen,
                                        brush=None, pxMode=False))
    if group["marks"]:
        xs, ys = (np.array(values, dtype=float) for values in zip(*group["marks"], strict=True))
        items.append(pg.ScatterPlotItem(x=xs, y=ys, size=8, symbol='+', pen=pen,
                                        brush=resolve_color(colour),
                                        pxMode=True))
    if group["paths"]:
        # One polyline per shape in a single item, separated by NaN, which `connect='finite'`
        # treats as a break rather than a line across the image.
        xs, ys = [], []
        for outline in group["paths"]:
            xs.extend(point[0] for point in outline)
            ys.extend(point[1] for point in outline)
            xs.append(float('nan'))
            ys.append(float('nan'))
        items.append(pg.PlotDataItem(x=np.array(xs), y=np.array(ys), connect='finite', pen=pen))
    return items


def _label_item(region, anchor, hover_pen=None):
    """A label for `region.text`, in the region's own colour and font size.

    A text region's label is interactive — it is the region — while a shape's caption is not: a box
    is grabbed by its own outline, and a caption that swallowed clicks would just be in the way.
    """
    if hover_pen is not None:
        label = RegionTextItem(region.text, color=resolve_color(region.color), anchor=anchor,
                               hover_pen=hover_pen)
    else:
        label = pg.TextItem(region.text, color=resolve_color(region.color), anchor=anchor)
        # A QGraphicsItem accepts every mouse button by default, so a caption would otherwise sit
        # in front of the shape it belongs to and swallow presses meant for it or for panning.
        label.setAcceptedMouseButtons(Qt.MouseButton.NoButton)
    label.setFont(_font_of_size(region.font_size))
    return label


def _label_offset(region):
    """How far above a shape's centre its label sits, in display pixels."""
    if isinstance(region, Circle):
        return region.radius
    if isinstance(region, Box):
        return max(region.width, region.height) / 2.0
    if isinstance(region, Arrow):
        return 0.0
    return 0.0


def region_kind(region):
    """The file-format name of a region's shape, for callers building menus and tables."""
    return region.TYPE if isinstance(region, Region) else None

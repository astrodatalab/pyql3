import gc

from PySide6.QtWidgets import QDialog, QVBoxLayout
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QGuiApplication

from pyql3.core import coords


def keep_on_screen(window):
    """Nudge `window` back inside the screen if it opened partly outside it.

    Qt centres a dialog on its parent, so one wider than the main window starts off the left edge —
    the Region List is 760 px against a 600 px window, which put it at x=-40 and drew
    `Window position ... outside any known screen` in the terminal. Qt then moves it to the primary
    screen itself, so the dialog is not lost, but it appears somewhere unrelated to where the user
    was looking.
    """
    screen = window.screen() or QGuiApplication.primaryScreen()
    if screen is None:
        return

    available = screen.availableGeometry()
    frame = window.frameGeometry()
    x = min(max(frame.x(), available.left()), max(available.left(),
                                                  available.right() - frame.width() + 1))
    y = min(max(frame.y(), available.top()), max(available.top(),
                                                 available.bottom() - frame.height() + 1))
    if (x, y) != (frame.x(), frame.y()):
        window.move(x, y)


def as_center(value):
    """Coerce a slot argument into an (x, y) pixel centre, or None.

    QAction.triggered is declared ``triggered(bool checked=False)`` and PySide6
    selects that overload for any slot accepting one argument, so a menu action
    hands an ``initial_center`` parameter a bool rather than a centre. Only a
    real 2-element pair is usable; anything else means "no centre given".
    """
    if value is None or isinstance(value, bool):
        return None
    try:
        x, y = value
    except (TypeError, ValueError):
        return None
    return (x, y)


class BaseToolDialog(QDialog):
    def __init__(self, parent=None, image_viewer=None, title="Tool"):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle(title)
        self.image_viewer = image_viewer
        self.roi = None
        #: ROIs taken out of the scene but not yet released. See `remove_roi_from_viewer`.
        self._retired_rois = []

        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        
        # Don't block the main window
        self.setModal(False)
        
    def showEvent(self, event):
        """Make sure the dialog is somewhere the user can see it, the first time it opens.

        Only the first time: moving it deliberately afterwards is the user's business.
        """
        super().showEvent(event)
        if not getattr(self, '_placed_on_screen', False):
            self._placed_on_screen = True
            keep_on_screen(self)

    def roi_plane_pixels(self, roi=None):
        """The whole pixels of a rectangular ROI, taken from the plane on screen.

        Returns `(pixels, (x slice, y slice))`, or `None` when the box covers no pixel of the
        plane. `pixels` are in the displayed units (`data_multiplier` applied) and the slices
        index `image_viewer.current_plane()`, so a caller can recover where on the image they
        came from. `ROI.getArrayRegion` is not used for this: it interpolates linearly, which
        lowers peaks whenever the box is not on the pixel grid, and it fills the part of the
        box off the image with zeros (`BUGS.md` R9). The tool ROIs have no rotate handle, so an
        axis-aligned box describes them completely.
        """
        roi = self.roi if roi is None else roi
        if roi is None or self.image_viewer is None:
            return None
        plane = self.image_viewer.current_plane()
        if plane is None or plane.ndim != 2:
            return None
        pos, size = roi.pos(), roi.size()
        slices = coords.box_index_slices(pos.x(), pos.y(), size.x(), size.y(), *plane.shape)
        if slices is None:
            return None
        return plane[slices] * self.image_viewer.data_multiplier, slices

    def add_roi_to_viewer(self, roi):
        if self.roi is not None:
            self.remove_roi_from_viewer()
            
        self.roi = roi
        if self.image_viewer and hasattr(self.image_viewer, 'imv'):
            img_item = self.image_viewer.imv.getImageItem()
            if img_item:
                self.roi.setParentItem(img_item)
            else:
                self.image_viewer.imv.getView().addItem(self.roi)
        self.roi.sigRegionChanged.connect(self.on_roi_changed)
        
    def remove_roi_from_viewer(self):
        if self.roi is not None and self.image_viewer is not None:
            import warnings
            with warnings.catch_warnings():
                warnings.simplefilter("ignore")
                try:
                    self.roi.sigRegionChanged.disconnect(self.on_roi_changed)
                except Exception:
                    pass
            try:
                self.roi.setParentItem(None)
            except Exception:
                pass
            try:
                self.image_viewer.imv.getView().removeItem(self.roi)
            except Exception:
                pass
            # Retire, do not release. `self.roi = None` here dropped the last Python
            # reference to a QGraphicsItem, and every caller that removes an ROI does so in
            # order to build its replacement -- so the collector ran *inside*
            # `ROI.addScaleHandle` -> `GraphicsObject.__init__` and freed the old item
            # mid-construction. Segfault, in `toggle_roi_shape`, reproducibly on the second
            # shape change. Same failure and same remedy as `BUGS.md` M18 in region_layer:
            # the event loop owns the release.
            self._retired_rois.append(self.roi)
            self.roi = None
            QTimer.singleShot(0, self._drop_retired_rois)

    def _drop_retired_rois(self):
        """Release ROIs taken out of the scene, once the event loop is back.

        The explicit `gc.collect()` is deliberate: these items sit in reference cycles, so
        dropping the list frees nothing on its own.

        Each valid ROI is also destroyed explicitly first. Left to the collector, a ROI, its
        handles and its context menu are freed in whatever order the cycle happens to be
        walked, and in one such order `callCppDestructor` ran on a handle whose C++ object was
        already gone: a segfault in `gc.collect()` that appeared and disappeared with
        unrelated allocations (adding one label to a dialog was enough). Destroying the ROI
        outright invalidates its wrappers, which the collector then skips.
        """
        if not self._retired_rois:
            return
        import shiboken6
        for roi in self._retired_rois:
            if shiboken6.isValid(roi):
                shiboken6.delete(roi)
        self._retired_rois = []
        gc.collect()
            
    def closeEvent(self, event):
        self.remove_roi_from_viewer()
        self.disable_draw_mode()
        super().closeEvent(event)
        
    def on_roi_changed(self):
        pass

    def setup_draw_button(self, layout):
        from PySide6.QtWidgets import QPushButton
        # "Region", not "Box": the shape a tool draws is the tool's own choice -- the Depth
        # Plot draws a circle by default -- so the button must not name one of them.
        self.btn_draw = QPushButton("Draw Region")
        self.btn_draw.setCheckable(True)
        self.btn_draw.clicked.connect(self.toggle_draw_mode)
        layout.insertWidget(0, self.btn_draw)
        
    def toggle_draw_mode(self, checked):
        if checked:
            self.enable_draw_mode()
        else:
            self.disable_draw_mode()
            
    def enable_draw_mode(self):
        """Take over the view's drag handling so a drag draws this tool's box.

        Ownership goes through the viewer rather than being arranged here. Each tool used to
        save and restore `mouseDragEvent` itself, which breaks as soon as two tools are in draw
        mode: the second saves the first's handler, and restoring in the wrong order leaves the
        view unable to pan at all. `begin_exclusive_drag` also revokes any other tool's draw
        mode, so the two buttons cannot both look active.
        """
        if self.image_viewer is None or self.roi is None:
            return
        self.image_viewer.begin_exclusive_drag(
            self, self.custom_mouse_drag, on_revoked=self._draw_mode_revoked)

    def disable_draw_mode(self):
        if self.image_viewer is not None:
            self.image_viewer.end_exclusive_drag(self)
        if hasattr(self, '_drag_start_pos'):
            del self._drag_start_pos
        if hasattr(self, 'btn_draw') and self.btn_draw.isChecked():
            self.btn_draw.setChecked(False)

    def _draw_mode_revoked(self):
        """Something else took the drag; drop the button without asking for it back."""
        if hasattr(self, '_drag_start_pos'):
            del self._drag_start_pos
        if hasattr(self, 'btn_draw') and self.btn_draw.isChecked():
            self.btn_draw.setChecked(False)
            
    def custom_mouse_drag(self, ev):
        if self.image_viewer is None or self.roi is None:
            ev.ignore()
            return
            
        from PySide6.QtCore import Qt
        
        # Only draw on left click
        if ev.button() != Qt.MouseButton.LeftButton:
            ev.ignore()
            return

        if ev.isStart():
            pos = self.image_viewer.imv.getImageItem().mapFromScene(ev.buttonDownScenePos())
            self._drag_start_pos = (pos.x(), pos.y())
            self.roi.blockSignals(True)
            self.roi.setPos(pos)
            self.roi.setSize([1e-5, 1e-5]) # very small
            self.roi.blockSignals(False)
            ev.accept()
        elif ev.isFinish():
            if hasattr(self, '_drag_start_pos'):
                del self._drag_start_pos
            self.disable_draw_mode()
            self.on_roi_changed()
            ev.accept()
        else:
            pos = self.image_viewer.imv.getImageItem().mapFromScene(ev.scenePos())
            start = getattr(self, '_drag_start_pos', (self.roi.pos().x(), self.roi.pos().y()))
            
            x0, y0 = start[0], start[1]
            x1, y1 = pos.x(), pos.y()
            
            new_x = min(x0, x1)
            new_y = min(y0, y1)
            new_w = max(1e-5, abs(x1 - x0))
            new_h = max(1e-5, abs(y1 - y0))
            
            self.roi.blockSignals(True)
            self.roi.setPos([new_x, new_y])
            self.roi.setSize([new_w, new_h])
            self.roi.blockSignals(False)
            
            self.on_roi_changed()
            ev.accept()

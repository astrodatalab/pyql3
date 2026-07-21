from PySide6.QtWidgets import QDialog, QVBoxLayout
from PySide6.QtCore import Qt
import pyqtgraph as pg

class BaseToolDialog(QDialog):
    def __init__(self, parent=None, image_viewer=None, title="Tool"):
        super().__init__(parent)
        self.setWindowFlags(Qt.Window)
        self.setWindowTitle(title)
        self.image_viewer = image_viewer
        self.roi = None
        
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(10, 10, 10, 10)
        
        # Don't block the main window
        self.setModal(False)
        
    def add_roi_to_viewer(self, roi):
        if self.roi is not None:
            self.remove_roi_from_viewer()
            
        self.roi = roi
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
                self.image_viewer.imv.getView().removeItem(self.roi)
            except Exception:
                pass
            self.roi = None
            
    def closeEvent(self, event):
        self.remove_roi_from_viewer()
        self.disable_draw_mode()
        super().closeEvent(event)
        
    def on_roi_changed(self):
        pass

    def setup_draw_button(self, layout):
        from PySide6.QtWidgets import QPushButton
        self.btn_draw = QPushButton("Draw Box / Region")
        self.btn_draw.setCheckable(True)
        self.btn_draw.clicked.connect(self.toggle_draw_mode)
        layout.insertWidget(0, self.btn_draw)
        
    def toggle_draw_mode(self, checked):
        if checked:
            self.enable_draw_mode()
        else:
            self.disable_draw_mode()
            
    def enable_draw_mode(self):
        if self.image_viewer is None or self.roi is None:
            return
        if not hasattr(self, '_old_drag'):
            self._old_drag = self.image_viewer.imv.getView().mouseDragEvent
        self.image_viewer.imv.getView().mouseDragEvent = self.custom_mouse_drag
        # Disable panning temporarily
        self.image_viewer.imv.getView().setMouseEnabled(x=False, y=False)
        
    def disable_draw_mode(self):
        if hasattr(self, '_old_drag') and self.image_viewer is not None:
            self.image_viewer.imv.getView().mouseDragEvent = self._old_drag
            self.image_viewer.imv.getView().setMouseEnabled(x=True, y=True)
            del self._old_drag
        if hasattr(self, '_drag_start_pos'):
            del self._drag_start_pos
        if hasattr(self, 'btn_draw') and self.btn_draw.isChecked():
            self.btn_draw.setChecked(False)
            
    def custom_mouse_drag(self, ev):
        if self.image_viewer is None or self.roi is None:
            ev.ignore()
            return
            
        import pyqtgraph as pg
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

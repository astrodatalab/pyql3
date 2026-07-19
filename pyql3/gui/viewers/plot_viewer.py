import pyqtgraph as pg
from PySide6.QtWidgets import QWidget, QVBoxLayout

class PlotViewer(QWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.layout = QVBoxLayout(self)
        self.layout.setContentsMargins(0, 0, 0, 0)
        
        self.plot_widget = pg.PlotWidget()
        self.plot_widget.setBackground('w')
        self.plot_widget.showGrid(x=True, y=True)
        self.layout.addWidget(self.plot_widget)
        
        self.curve = self.plot_widget.plot(pen=pg.mkPen('b', width=2))
        
    def set_data(self, x, y, title="1D Profile", xlabel="Pixel", ylabel="Intensity"):
        """Plots 1D data (e.g., line profiles or spectra)."""
        self.plot_widget.setTitle(title, color='k')
        self.plot_widget.setLabel('bottom', xlabel)
        self.plot_widget.setLabel('left', ylabel)
        self.curve.setData(x, y)
        
    def clear(self):
        self.curve.setData([], [])

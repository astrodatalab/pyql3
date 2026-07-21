import numpy as np
from PySide6.QtWidgets import (
    QDialog, QVBoxLayout, QHBoxLayout, QPushButton, QLabel, 
    QComboBox, QSpinBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QFileDialog, QHeaderView, QWidget, QAbstractItemView, QColorDialog, QLineEdit,
    QGroupBox, QFormLayout, QMenu, QApplication
)
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor, QPalette
import pyqtgraph as pg
import astropy.io.ascii as ascii
from astropy.coordinates import SkyCoord
import astropy.units as u
import warnings

from pyql3.gui.tools.base_tool import BaseToolDialog

def map_to_display(image_viewer, orig_x, orig_y):
    if image_viewer.display_data is None:
        return orig_x, orig_y
        
    shape = image_viewer.display_data.shape
    is_3d = (image_viewer.display_data.ndim == 3)
    max_x = shape[1] if is_3d else shape[0]
    max_y = shape[2] if is_3d else shape[1]
    
    k = image_viewer.rot_angle // 90
    orig_max_x = max_y if k % 2 == 1 else max_x
    orig_max_y = max_x if k % 2 == 1 else max_y
    
    curr_x, curr_y = float(orig_x), float(orig_y)
    
    if image_viewer.flip:
        curr_x = orig_max_x - 1 - curr_x
        
    for _ in range(k):
        curr_x, curr_y = orig_max_y - 1 - curr_y, curr_x
        orig_max_x, orig_max_y = orig_max_y, orig_max_x
        
    return curr_x, curr_y

class PlotCatalogDialog(BaseToolDialog):
    def __init__(self, parent=None, image_viewer=None):
        super().__init__(parent, image_viewer, "Plot Catalog")
        self.resize(600, 500)
        
        self.catalog_table = None
        self.catalog_data = None
        self.scatter_item = None
        self.highlight_item = None
        self.text_items = []
        
        # Default marker settings
        self.marker_color = QColor(255, 165, 0) # Orange default
        
        self.setup_ui()
        
    def setup_ui(self):
        # Data Source Group
        src_group = QGroupBox("Data Source")
        src_layout = QHBoxLayout()
        
        self.btn_load = QPushButton("Load Catalog (CSV/TXT)...")
        self.btn_load.clicked.connect(self.load_catalog)
        self.lbl_file = QLabel("No file loaded")
        
        src_layout.addWidget(self.btn_load)
        src_layout.addWidget(self.lbl_file)
        src_layout.addStretch()
        src_group.setLayout(src_layout)
        self.layout.addWidget(src_group)
        
        # Coordinate Mapping Group
        coord_group = QGroupBox("Coordinate Mapping")
        coord_layout = QHBoxLayout()
        
        coord_layout.addWidget(QLabel("Type:"))
        self.combo_coord_type = QComboBox()
        self.combo_coord_type.addItems(["Display Pixels", "FITS Pixels", "World (RA/DEC)"])
        self.combo_coord_type.currentIndexChanged.connect(self.update_columns_for_type)
        coord_layout.addWidget(self.combo_coord_type)
        
        coord_layout.addWidget(QLabel("  X/RA Col:"))
        self.combo_x = QComboBox()
        self.combo_x.currentIndexChanged.connect(self.update_plot)
        coord_layout.addWidget(self.combo_x)
        
        coord_layout.addWidget(QLabel("  Y/DEC Col:"))
        self.combo_y = QComboBox()
        self.combo_y.currentIndexChanged.connect(self.update_plot)
        coord_layout.addWidget(self.combo_y)
        
        coord_layout.addStretch()
        coord_group.setLayout(coord_layout)
        self.layout.addWidget(coord_group)
        
        # Styling Group
        style_group = QGroupBox("Marker Styling")
        style_layout = QHBoxLayout()
        
        self.chk_master_toggle = QCheckBox("Show All")
        self.chk_master_toggle.setChecked(True)
        self.chk_master_toggle.stateChanged.connect(self.update_plot)
        style_layout.addWidget(self.chk_master_toggle)
        
        self.btn_color = QPushButton("Color")
        self.btn_color.setStyleSheet(f"background-color: {self.marker_color.name()};")
        self.btn_color.clicked.connect(self.choose_color)
        style_layout.addWidget(self.btn_color)
        
        style_layout.addWidget(QLabel("Shape:"))
        self.combo_shape = QComboBox()
        self.combo_shape.addItems(["o (Circle)", "s (Square)", "t (Triangle)", "d (Diamond)", "+ (Cross)", "x (X)"])
        self.combo_shape.currentIndexChanged.connect(self.update_plot)
        style_layout.addWidget(self.combo_shape)
        
        style_layout.addWidget(QLabel("Size:"))
        self.spin_size = QSpinBox()
        self.spin_size.setRange(1, 50)
        self.spin_size.setValue(10)
        self.spin_size.valueChanged.connect(self.update_plot)
        style_layout.addWidget(self.spin_size)
        
        self.chk_show_name = QCheckBox("Labels:")
        self.chk_show_name.stateChanged.connect(self.update_plot)
        style_layout.addWidget(self.chk_show_name)
        
        self.combo_name = QComboBox()
        self.combo_name.currentIndexChanged.connect(self.update_plot)
        style_layout.addWidget(self.combo_name)
        
        style_layout.addStretch()
        style_group.setLayout(style_layout)
        self.layout.addWidget(style_group)
        
        # Search bar directly on top of table
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search catalog...")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self.filter_table)
        self.layout.addWidget(self.search_bar)
        
        # Table
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self.on_table_selection)
        
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)
        
        self.layout.addWidget(self.table)
        
        self.lbl_status = QLabel("Loaded: 0 sources | 0 plotted | 0 out of bounds")
        self.layout.addWidget(self.lbl_status)
        
    def load_catalog(self):
        filepath, _ = QFileDialog.getOpenFileName(self, "Open Catalog", "", "Catalog Files (*.csv *.txt *.dat);;All Files (*)")
        if not filepath:
            return
            
        try:
            with warnings.catch_warnings():
                warnings.simplefilter('ignore')
                self.catalog_data = ascii.read(filepath, guess=True)
                
            import os
            self.lbl_file.setText(os.path.basename(filepath))
            self.populate_table()
            self.auto_assign_columns()
            self.update_plot()
        except Exception as e:
            self.lbl_file.setText(f"Error loading file: {e}")
            
    def populate_table(self):
        if self.catalog_data is None:
            return
            
        cols = self.catalog_data.colnames
        self.table.setColumnCount(len(cols))
        self.table.setHorizontalHeaderLabels(cols)
        self.table.setRowCount(len(self.catalog_data))
        
        for i, row in enumerate(self.catalog_data):
            for j, col in enumerate(cols):
                val = row[col]
                # Format floats nicely, otherwise just string
                if isinstance(val, (float, np.floating)):
                    text = f"{val:.5g}"
                else:
                    text = str(val)
                item = QTableWidgetItem(text)
                self.table.setItem(i, j, item)
                
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
        
        # Update combos
        self.combo_x.blockSignals(True)
        self.combo_y.blockSignals(True)
        self.combo_name.blockSignals(True)
        
        self.combo_x.clear()
        self.combo_y.clear()
        self.combo_name.clear()
        
        self.combo_x.addItems(cols)
        self.combo_y.addItems(cols)
        self.combo_name.addItems(cols)
        
        self.combo_x.blockSignals(False)
        self.combo_y.blockSignals(False)
        self.combo_name.blockSignals(False)
        
    def filter_table(self, text):
        text = text.lower()
        for row in range(self.table.rowCount()):
            match = False
            for col in range(self.table.columnCount()):
                item = self.table.item(row, col)
                if item and text in item.text().lower():
                    match = True
                    break
            self.table.setRowHidden(row, not match)
            
    def auto_assign_columns(self):
        if self.catalog_data is None:
            return
            
        cols = [c.lower() for c in self.catalog_data.colnames]
        
        # Auto detect RA/DEC vs X/Y
        has_ra = any(c in ['ra', 'right ascension', 'alpha'] for c in cols)
        has_dec = any(c in ['dec', 'declination', 'delta'] for c in cols)
        has_x = any(c in ['x', 'xcenter', 'xc', 'x_c'] for c in cols)
        has_y = any(c in ['y', 'ycenter', 'yc', 'y_c'] for c in cols)
        
        self.combo_coord_type.blockSignals(True)
        if has_x and has_y:
            self.combo_coord_type.setCurrentIndex(1) # Default to FITS Pixels
        elif has_ra and has_dec:
            self.combo_coord_type.setCurrentIndex(2) # World
        self.combo_coord_type.blockSignals(False)
        
        self.update_columns_for_type()
        
        # Auto detect name column
        for i, c in enumerate(cols):
            if c in ['name', 'id', 'object', 'source']:
                self.combo_name.setCurrentIndex(i)
                break
                
    def update_columns_for_type(self):
        if self.catalog_data is None:
            return
            
        cols = [c.lower() for c in self.catalog_data.colnames]
        is_world = self.combo_coord_type.currentIndex() == 2
        
        self.combo_x.blockSignals(True)
        self.combo_y.blockSignals(True)
        
        found_x = False
        found_y = False
        
        if is_world:
            # Try to find RA/DEC
            for i, c in enumerate(cols):
                if not found_x and c in ['ra', 'right ascension', 'alpha']:
                    self.combo_x.setCurrentIndex(i)
                    found_x = True
                elif not found_y and c in ['dec', 'declination', 'delta']:
                    self.combo_y.setCurrentIndex(i)
                    found_y = True
        else:
            # Try to find X/Y
            for i, c in enumerate(cols):
                if not found_x and c in ['x', 'xcenter', 'xc', 'x_c']:
                    self.combo_x.setCurrentIndex(i)
                    found_x = True
                elif not found_y and c in ['y', 'ycenter', 'yc', 'y_c']:
                    self.combo_y.setCurrentIndex(i)
                    found_y = True
                    
        # Fallback to numeric columns if explicit names were not found
        if not found_x or not found_y:
            numeric_cols = []
            for i, cname in enumerate(self.catalog_data.colnames):
                try:
                    val = self.catalog_data[cname][0]
                    if isinstance(val, (float, int, np.number)):
                        numeric_cols.append(i)
                    else:
                        float(val)
                        numeric_cols.append(i)
                except (ValueError, TypeError, IndexError):
                    pass
                    
            if not found_x and len(numeric_cols) > 0:
                self.combo_x.setCurrentIndex(numeric_cols[0])
            if not found_y and len(numeric_cols) > 1:
                self.combo_y.setCurrentIndex(numeric_cols[1])
                    
        self.combo_x.blockSignals(False)
        self.combo_y.blockSignals(False)
        self.update_plot()
        
    def choose_color(self):
        color = QColorDialog.getColor(self.marker_color, self, "Select Marker Color")
        if color.isValid():
            self.marker_color = color
            self.btn_color.setStyleSheet(f"background-color: {color.name()};")
            self.update_plot()
            
    def _get_pg_symbol(self):
        shape_str = self.combo_shape.currentText()
        if shape_str.startswith("o"): return "o"
        if shape_str.startswith("s"): return "s"
        if shape_str.startswith("t"): return "t"
        if shape_str.startswith("d"): return "d"
        if shape_str.startswith("+"): return "+"
        if shape_str.startswith("x"): return "x"
        return "o"
        
    def update_plot(self):
        if self.image_viewer is None or self.catalog_data is None:
            return
            
        if self.scatter_item is None:
            self.scatter_item = pg.ScatterPlotItem()
            self.scatter_item.setZValue(10)
            self.image_viewer.imv.getView().addItem(self.scatter_item)
            
        if self.highlight_item is None:
            self.highlight_item = pg.ScatterPlotItem()
            self.highlight_item.setZValue(11)
            self.image_viewer.imv.getView().addItem(self.highlight_item)
        # Clean up old text items
        for txt in self.text_items:
            self.image_viewer.imv.getView().removeItem(txt)
        self.text_items.clear()
        
        if not hasattr(self, 'chk_master_toggle'):
            return
            
        if not self.chk_master_toggle.isChecked():
            self.scatter_item.clear()
            self.highlight_item.clear()
            self.lbl_status.setText(f"Loaded: {len(self.catalog_data) if self.catalog_data else 0} sources | Markers hidden")
            return
            
        x_col = self.combo_x.currentText()
        y_col = self.combo_y.currentText()
        name_col = self.combo_name.currentText()
        
        if not x_col or not y_col or x_col not in self.catalog_data.colnames or y_col not in self.catalog_data.colnames:
            self.scatter_item.clear()
            return
            
        coord_idx = self.combo_coord_type.currentIndex()
        
        pts_x = []
        pts_y = []
        
        oob_count = 0
        
        if self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            is_3d = (self.image_viewer.display_data.ndim == 3)
            max_x = shape[1] if is_3d else shape[0]
            max_y = shape[2] if is_3d else shape[1]
        else:
            max_x = float('inf')
            max_y = float('inf')
        
        for row in self.catalog_data:
            val_x = row[x_col]
            val_y = row[y_col]
            
            if coord_idx == 2:
                try:
                    try:
                        # Try parsing as float degrees
                        f_x = float(val_x)
                        f_y = float(val_y)
                        crd = SkyCoord(f_x, f_y, unit=(u.deg, u.deg))
                    except ValueError:
                        # Parse as HMS/DMS string
                        crd = SkyCoord(val_x, val_y, unit=(u.hourangle, u.deg))
                    val_x = crd.ra.deg
                    val_y = crd.dec.deg
                    orig_x, orig_y = val_x, val_y
                except Exception as e:
                    print(f"RA/DEC Parse Error: {e}")
                    continue
            else:
                try:
                    val_x = float(val_x)
                    val_y = float(val_y)
                except ValueError:
                    continue
                orig_x, orig_y = val_x, val_y
            
            if coord_idx == 0:
                # Display Pixels: Map exactly to Screen
                disp_x, disp_y = val_x, val_y
            else:
                if coord_idx == 2:
                    if getattr(self.image_viewer, 'wcs', None) is None:
                        continue
                    # Transform RA/DEC to FITS pixels
                    try:
                        if self.image_viewer.wcs.naxis == 2:
                            orig_x, orig_y = self.image_viewer.wcs.world_to_pixel_values(val_x, val_y)
                        else:
                            phys = self.image_viewer.wcs.world_axis_physical_types
                            coords_in = [0.0] * self.image_viewer.wcs.naxis
                            for ax_idx, p in enumerate(phys):
                                if p == 'pos.eq.ra':
                                    coords_in[ax_idx] = val_x
                                elif p == 'pos.eq.dec':
                                    coords_in[ax_idx] = val_y
                                else:
                                    coords_in[ax_idx] = self.image_viewer.wcs.wcs.crval[ax_idx]
                                    
                            pixel_coords = self.image_viewer.wcs.world_to_pixel_values(*coords_in)
                            
                            ax1_idx = int(getattr(self.image_viewer, 'current_x_axis', 'AXIS 1').split()[-1]) - 1
                            ax2_idx = int(getattr(self.image_viewer, 'current_y_axis', 'AXIS 2').split()[-1]) - 1
                            
                            orig_x = pixel_coords[ax1_idx]
                            orig_y = pixel_coords[ax2_idx]
                            
                    except Exception as e:
                        print(f"WCS Transform Error: {e}")
                        continue
                        
                disp_x, disp_y = map_to_display(self.image_viewer, orig_x, orig_y)
                
            if 0 <= disp_x < max_x and 0 <= disp_y < max_y:
                pts_x.append(disp_x + 0.5)
                pts_y.append(disp_y + 0.5)
                
                if self.chk_show_name.isChecked() and name_col in self.catalog_data.colnames:
                    name_str = str(row[name_col])
                    txt = pg.TextItem(name_str, color=self.marker_color.name(), anchor=(0, 1))
                    txt.setZValue(12)
                    txt.setPos(disp_x + 0.5, disp_y + 0.5)
                    self.image_viewer.imv.getView().addItem(txt)
                    self.text_items.append(txt)
            else:
                oob_count += 1
                
        symbol = self._get_pg_symbol()
        size = self.spin_size.value()
        
        # Transparent brush, solid pen (outline)
        pen = pg.mkPen(color=self.marker_color, width=2)
        brush = pg.mkBrush(color=(0, 0, 0, 0))
        
        self.scatter_item.setData(x=pts_x, y=pts_y, symbol=symbol, size=size, pen=pen, brush=brush)
        self.lbl_status.setText(f"Loaded: {len(self.catalog_data)} sources | {len(pts_x)} plotted | {oob_count} out of bounds")
        self.on_table_selection() # Update highlight
        
    def on_table_selection(self):
        if self.highlight_item is None or self.catalog_data is None:
            return
            
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            self.highlight_item.clear()
            self.highlight_item.setVisible(False)
            return
            
        row_idx = selected_rows[0].row()
        row = self.catalog_data[row_idx]
        
        x_col = self.combo_x.currentText()
        y_col = self.combo_y.currentText()
        
        if x_col not in self.catalog_data.colnames or y_col not in self.catalog_data.colnames:
            return
            
        val_x = row[x_col]
        val_y = row[y_col]
        
        coord_idx = self.combo_coord_type.currentIndex()
        
        if coord_idx == 2:
            try:
                try:
                    f_x = float(val_x)
                    f_y = float(val_y)
                    crd = SkyCoord(f_x, f_y, unit=(u.deg, u.deg))
                except ValueError:
                    crd = SkyCoord(val_x, val_y, unit=(u.hourangle, u.deg))
                val_x = crd.ra.deg
                val_y = crd.dec.deg
                orig_x, orig_y = val_x, val_y
            except Exception:
                self.highlight_item.clear()
                return
        else:
            try:
                val_x = float(val_x)
                val_y = float(val_y)
            except ValueError:
                self.highlight_item.clear()
                return
            orig_x, orig_y = val_x, val_y
        
        if coord_idx == 0:
            disp_x, disp_y = val_x, val_y
        else:
            if coord_idx == 2:
                if getattr(self.image_viewer, 'wcs', None) is None:
                    return
                try:
                    if self.image_viewer.wcs.naxis == 2:
                        orig_x, orig_y = self.image_viewer.wcs.world_to_pixel_values(val_x, val_y)
                    else:
                        phys = self.image_viewer.wcs.world_axis_physical_types
                        coords_in = [0.0] * self.image_viewer.wcs.naxis
                        for ax_idx, p in enumerate(phys):
                            if p == 'pos.eq.ra': coords_in[ax_idx] = val_x
                            elif p == 'pos.eq.dec': coords_in[ax_idx] = val_y
                            else: coords_in[ax_idx] = self.image_viewer.wcs.wcs.crval[ax_idx]
                        pixel_coords = self.image_viewer.wcs.world_to_pixel_values(*coords_in)
                        ax1_idx = int(getattr(self.image_viewer, 'current_x_axis', 'AXIS 1').split()[-1]) - 1
                        ax2_idx = int(getattr(self.image_viewer, 'current_y_axis', 'AXIS 2').split()[-1]) - 1
                        orig_x = pixel_coords[ax1_idx]
                        orig_y = pixel_coords[ax2_idx]
                except Exception:
                    return
                    
            disp_x, disp_y = map_to_display(self.image_viewer, orig_x, orig_y)
        
        # Draw white highlight marker
        pen = pg.mkPen(color=QColor(255, 255, 255), width=4)
        brush = pg.mkBrush(color=(0, 0, 0, 0))
        size = self.spin_size.value() + 10
        self.highlight_item.setData(x=[disp_x], y=[disp_y], symbol='o', size=size, pen=pen, brush=brush)
        self.highlight_item.setVisible(True)
        
        # Center view if within valid data range
        if self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            is_3d = (self.image_viewer.display_data.ndim == 3)
            max_x = shape[1] if is_3d else shape[0]
            max_y = shape[2] if is_3d else shape[1]
            
            if 0 <= disp_x < max_x and 0 <= disp_y < max_y:
                view = self.image_viewer.imv.getView()
                view_rect = view.viewRect()
                width = view_rect.width()
                height = view_rect.height()
                # Use setRange with padding=0 to preserve current zoom exactly
                view.setRange(xRange=(disp_x - width/2, disp_x + width/2), 
                              yRange=(disp_y - height/2, disp_y + height/2), 
                              padding=0)
        
    def show_context_menu(self, pos):
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            return
            
        row_idx = selected_rows[0].row()
        row_data = self.catalog_data[row_idx]
        
        menu = QMenu(self)
        copy_action = menu.addAction("Copy Coordinates")
        center_action = menu.addAction("Center on Source")
        menu.addSeparator()
        delete_action = menu.addAction("Delete Marker")
        
        action = menu.exec(self.table.viewport().mapToGlobal(pos))
        
        if action == copy_action:
            x_col = self.combo_x.currentText()
            y_col = self.combo_y.currentText()
            if x_col in self.catalog_data.colnames and y_col in self.catalog_data.colnames:
                coords = f"X: {row_data[x_col]}, Y: {row_data[y_col]}"
                QApplication.clipboard().setText(coords)
                
        elif action == center_action:
            # Trigger table selection to highlight and center
            self.on_table_selection()
            
        elif action == delete_action:
            self.catalog_data.remove_row(row_idx)
            self.populate_table()
            self.auto_assign_columns()
            self.update_plot()

    def closeEvent(self, event):
        if self.scatter_item is not None and self.image_viewer is not None:
            try:
                self.image_viewer.imv.getView().removeItem(self.scatter_item)
            except Exception:
                pass
            self.scatter_item = None
            
        if self.highlight_item is not None and self.image_viewer is not None:
            try:
                self.image_viewer.imv.getView().removeItem(self.highlight_item)
            except Exception:
                pass
            self.highlight_item = None
            
        for txt in self.text_items:
            if self.image_viewer is not None:
                try:
                    self.image_viewer.imv.getView().removeItem(txt)
                except Exception:
                    pass
        self.text_items.clear()
        
        super().closeEvent(event)

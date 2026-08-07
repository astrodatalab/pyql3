import os
from collections import namedtuple

import numpy as np
from PySide6.QtWidgets import (
    QHBoxLayout, QPushButton, QLabel,
    QComboBox, QSpinBox, QCheckBox, QTableWidget, QTableWidgetItem,
    QFileDialog, QHeaderView, QAbstractItemView, QColorDialog, QLineEdit,
    QGroupBox, QMenu, QApplication, QInputDialog
)
from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QKeySequence, QShortcut
import pyqtgraph as pg
import astropy.io.ascii as ascii
from astropy.io import fits
from astropy.table import Table
from astropy.coordinates import SkyCoord
import astropy.units as u
import warnings

from pyql3.gui.tools.base_tool import BaseToolDialog

# Suffixes that make a file worth trying as a FITS table before handing it to
# astropy's ASCII reader. Compressed variants are included because astropy opens
# them transparently.
FITS_SUFFIXES = (
    '.fits', '.fit', '.fts', '.fz',
    '.fits.gz', '.fit.gz', '.fts.gz', '.fits.bz2', '.fits.zip',
)


# Column names recognised when guessing which columns hold coordinates. FITS source
# tables rarely use the bare names a hand-written CSV does, so the photutils
# (`xcentroid`) and SExtractor (`X_IMAGE`, `ALPHA_J2000`) spellings are included.
# Comparison is done in lower case.
RA_COLUMN_NAMES = ('ra', 'right ascension', 'alpha', 'raj2000', 'ra_j2000',
                   'alpha_j2000', 'ra_deg', 'radeg')
DEC_COLUMN_NAMES = ('dec', 'declination', 'delta', 'decj2000', 'dec_j2000',
                    'delta_j2000', 'dec_deg', 'decdeg')
X_COLUMN_NAMES = ('x', 'xcenter', 'xc', 'x_c', 'xcentroid', 'x_image', 'xpix',
                  'x_pix', 'xpos', 'x_pos')
Y_COLUMN_NAMES = ('y', 'ycenter', 'yc', 'y_c', 'ycentroid', 'y_image', 'ypix',
                  'y_pix', 'ypos', 'y_pos')


def looks_like_fits(filepath):
    return str(filepath).lower().endswith(FITS_SUFFIXES)


FitsTableExt = namedtuple('FitsTableExt', 'index name label')


def fits_table_extensions(filepath):
    """List the table extensions of a FITS file as `FitsTableExt` records.

    `CompImageHDU` subclasses `BinTableHDU` in astropy — a tile-compressed *image* would
    otherwise be offered as a catalog — so it is excluded explicitly.
    """
    exts = []
    with fits.open(filepath, memmap=False) as hdul:
        for idx, hdu in enumerate(hdul):
            if not isinstance(hdu, (fits.BinTableHDU, fits.TableHDU)):
                continue
            if isinstance(hdu, fits.CompImageHDU):
                continue
            name = hdu.name or 'TABLE'
            nrows = hdu.header.get('NAXIS2', 0)
            ncols = hdu.header.get('TFIELDS', 0)
            exts.append(FitsTableExt(
                idx, name, f"[{idx}] {name} — {nrows} rows x {ncols} cols"))
    return exts


def read_fits_table(filepath, hdu=None):
    """Read one table extension of a FITS file into an `astropy.table.Table`.

    Returns ``(table, label)``, where `label` names the extension that was used and notes
    any columns that had to be dropped. `hdu` may be an index, an EXTNAME, or None to take
    the first table extension in the file.

    Vector (multi-element) columns are removed: a catalog overlay needs one scalar value
    per row, and an OSIRIS-sized spectrum column would put 465 numbers in a table cell.
    """
    exts = fits_table_extensions(filepath)
    if not exts:
        raise ValueError("this FITS file contains no table extension")

    if hdu is None:
        chosen = exts[0]
    elif isinstance(hdu, str):
        matches = [e for e in exts if e.name.upper() == hdu.upper()]
        if not matches:
            raise ValueError(f"no table extension named {hdu!r} in this file")
        chosen = matches[0]
    else:
        matches = [e for e in exts if e.index == hdu]
        if not matches:
            raise ValueError(f"HDU {hdu} is not a table extension of this file")
        chosen = matches[0]

    table = Table.read(filepath, hdu=chosen.index)
    label = chosen.name

    vector_cols = [name for name in table.colnames if table[name].ndim > 1]
    if vector_cols:
        table.remove_columns(vector_cols)
        label += f" (skipped {len(vector_cols)} vector column"
        label += "s)" if len(vector_cols) > 1 else ")"
    if not table.colnames:
        raise ValueError("no scalar columns in this table extension")

    return table, label


def to_float(val):
    """Coerce one catalog cell to a plottable float, or None if it is not one.

    FITS tables bring in cases the ASCII reader never produced: masked cells (TNULL /
    undefined values) and NaNs. `float(np.ma.masked)` yields NaN with a warning rather
    than raising, so masking has to be tested for rather than caught.
    """
    if val is None or val is np.ma.masked or np.ma.is_masked(val):
        return None
    try:
        f = float(val)
    except (TypeError, ValueError):
        return None
    return f if np.isfinite(f) else None


def map_to_display(image_viewer, orig_x, orig_y):
    """Map a FITS-axis pixel coordinate to display coordinates.

    Kept as a module-level function because the catalog code calls it per row; the arithmetic
    itself lives in `pyql3.core.coords` and is shared with the WCS readout and the Depth Plot
    (`BUGS.md` B14). Unloaded viewers pass the coordinate through unchanged, as before.
    """
    mapped = image_viewer.orig_to_display(orig_x, orig_y)
    return (orig_x, orig_y) if mapped is None else mapped

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
        
        self.btn_load = QPushButton("Load Catalog (CSV/TXT/FITS)...")
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
        search_row = QHBoxLayout()
        self.search_bar = QLineEdit()
        self.search_bar.setPlaceholderText("Search catalog...")
        self.search_bar.setClearButtonEnabled(True)
        self.search_bar.textChanged.connect(self.filter_table)
        search_row.addWidget(self.search_bar)

        # Selecting a row highlights that source and recentres the view on it, and Qt's only way
        # back out of a single-selection table is ctrl-clicking the selected row — which nobody
        # discovers. Escape and this button are the ways out that can be found.
        self.btn_clear_selection = QPushButton("Clear Selection")
        self.btn_clear_selection.setToolTip(
            "Remove the highlight from the image (or press Escape in the table)")
        self.btn_clear_selection.setEnabled(False)
        self.btn_clear_selection.clicked.connect(self.clear_selection)
        search_row.addWidget(self.btn_clear_selection)
        self.layout.addLayout(search_row)
        
        # Table
        self.table = QTableWidget()
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.itemSelectionChanged.connect(self.on_table_selection)
        
        self.table.setContextMenuPolicy(Qt.CustomContextMenu)
        self.table.customContextMenuRequested.connect(self.show_context_menu)

        clear_shortcut = QShortcut(QKeySequence(Qt.Key.Key_Escape), self.table)
        clear_shortcut.setContext(Qt.ShortcutContext.WidgetShortcut)
        clear_shortcut.activated.connect(self.clear_selection)
        
        self.layout.addWidget(self.table)
        
        self.lbl_status = QLabel("Loaded: 0 sources | 0 plotted | 0 out of bounds")
        self.layout.addWidget(self.lbl_status)
        
    def load_catalog(self):
        filepath, _ = QFileDialog.getOpenFileName(
            self, "Open Catalog", "",
            "Catalog Files (*.csv *.txt *.dat *.tbl *.ecsv *.fits *.fit *.fts *.fits.gz);;"
            "FITS Tables (*.fits *.fit *.fts *.fz *.fits.gz);;"
            "Text Catalogs (*.csv *.txt *.dat *.tbl *.ecsv);;"
            "All Files (*)")
        if not filepath:
            return

        hdu = None
        if looks_like_fits(filepath):
            proceed, hdu = self._choose_fits_hdu(filepath)
            if not proceed:
                return
        self.load_catalog_file(filepath, hdu=hdu)

    def _choose_fits_hdu(self, filepath):
        """Ask which table extension to read, when the file holds more than one.

        Returns ``(proceed, hdu)``. `hdu` is None when there is nothing to choose between,
        which leaves `read_fits_table` to take the first table extension (or to raise the
        real error, if the file cannot be opened at all).
        """
        try:
            exts = fits_table_extensions(filepath)
        except Exception:
            return True, None
        if len(exts) <= 1:
            return True, exts[0].index if exts else None

        labels = [ext.label for ext in exts]
        choice, ok = QInputDialog.getItem(
            self, "Select FITS Table",
            f"{os.path.basename(filepath)} contains several tables:",
            labels, 0, False)
        if not ok:
            return False, None
        return True, exts[labels.index(choice)].index

    def load_catalog_file(self, filepath, hdu=None):
        """Load a catalog from a file path. Can be called programmatically.

        Reads a FITS table when the file is one, and an ASCII table otherwise. `hdu`
        selects the FITS table extension (index or EXTNAME); the first table extension is
        used when it is None.
        """
        try:
            if looks_like_fits(filepath):
                self.catalog_data, ext_label = read_fits_table(filepath, hdu)
                name = f"{os.path.basename(filepath)}  {ext_label}"
            else:
                try:
                    with warnings.catch_warnings():
                        warnings.simplefilter('ignore')
                        self.catalog_data = ascii.read(filepath, guess=True)
                    name = os.path.basename(filepath)
                except Exception as ascii_error:
                    # A FITS table under an unfamiliar name still has to load; if it is
                    # not one either, the ASCII failure is the message worth reporting.
                    try:
                        self.catalog_data, ext_label = read_fits_table(filepath, hdu)
                    except Exception:
                        raise ascii_error from None
                    name = f"{os.path.basename(filepath)}  {ext_label}"

            self.set_catalog_table(self.catalog_data, name)
        except Exception as e:
            # astropy's failed format guess is dozens of lines long; a QLabel gets one
            lines = [line for line in str(e).strip().splitlines() if line.strip()]
            msg = lines[0][:120] if lines else type(e).__name__
            self.lbl_file.setText(f"Error loading file: {msg}")

    def set_catalog_table(self, table, name):
        """Show a table that is already in memory, rather than reading one from a file.

        The seam a caller needs to hand this tool a source list it built itself — the Region menu
        sends the drawn regions here, where they gain the table, the search box and the row
        highlighting that this tool has and the region overlay does not.
        """
        self.catalog_data = table
        self.lbl_file.setText(name)
        self.populate_table()
        self.auto_assign_columns()
        self.update_plot()

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
                elif isinstance(val, bytes):
                    # FITS character columns can come through as bytes
                    text = val.decode('utf-8', 'replace').strip()
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
        has_ra = any(c in RA_COLUMN_NAMES for c in cols)
        has_dec = any(c in DEC_COLUMN_NAMES for c in cols)
        has_x = any(c in X_COLUMN_NAMES for c in cols)
        has_y = any(c in Y_COLUMN_NAMES for c in cols)
        
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
        
        x_names = RA_COLUMN_NAMES if is_world else X_COLUMN_NAMES
        y_names = DEC_COLUMN_NAMES if is_world else Y_COLUMN_NAMES

        for i, c in enumerate(cols):
            if not found_x and c in x_names:
                self.combo_x.setCurrentIndex(i)
                found_x = True
            elif not found_y and c in y_names:
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
        
    def _row_to_display(self, row):
        """Resolve one catalog row to display pixel coordinates, or None if it cannot be.

        Shared by the marker pass and by the table-selection highlight so the two cannot
        drift apart. The returned pair is *not* half-pixel centred — callers add the 0.5
        themselves.
        """
        if self.catalog_data is None or self.image_viewer is None:
            return None

        x_col = self.combo_x.currentText()
        y_col = self.combo_y.currentText()
        if x_col not in self.catalog_data.colnames or y_col not in self.catalog_data.colnames:
            return None

        val_x = row[x_col]
        val_y = row[y_col]
        coord_idx = self.combo_coord_type.currentIndex()

        if coord_idx == 2:
            # Decimal degrees when both cells are numbers, sexagesimal otherwise
            f_x, f_y = to_float(val_x), to_float(val_y)
            try:
                if f_x is not None and f_y is not None:
                    crd = SkyCoord(f_x, f_y, unit=(u.deg, u.deg))
                else:
                    crd = SkyCoord(val_x, val_y, unit=(u.hourangle, u.deg))
                val_x = float(crd.ra.deg)
                val_y = float(crd.dec.deg)
            except Exception:
                return None
        else:
            val_x, val_y = to_float(val_x), to_float(val_y)
            if val_x is None or val_y is None:
                return None

        if coord_idx == 0:
            return val_x, val_y

        orig_x, orig_y = val_x, val_y

        if coord_idx == 2:
            if getattr(self.image_viewer, 'wcs', None) is None:
                return None
            try:
                wcs = self.image_viewer.wcs
                if wcs.naxis == 2:
                    orig_x, orig_y = wcs.world_to_pixel_values(val_x, val_y)
                else:
                    # Never index the celestial axes by position: OSIRIS puts RA on
                    # FITS axis 3, other IFUs do not. Identify them from the WCS.
                    phys = wcs.world_axis_physical_types
                    coords_in = [0.0] * wcs.naxis
                    for ax_idx, p in enumerate(phys):
                        if p == 'pos.eq.ra':
                            coords_in[ax_idx] = val_x
                        elif p == 'pos.eq.dec':
                            coords_in[ax_idx] = val_y
                        else:
                            coords_in[ax_idx] = wcs.wcs.crval[ax_idx]

                    pixel_coords = wcs.world_to_pixel_values(*coords_in)

                    ax1_idx, ax2_idx = self.image_viewer.display_axis_indices()

                    orig_x = float(pixel_coords[ax1_idx])
                    orig_y = float(pixel_coords[ax2_idx])
            except Exception:
                return None

        return map_to_display(self.image_viewer, orig_x, orig_y)

    def update_plot(self):
        if self.image_viewer is None or self.catalog_data is None:
            return
            
        img_item = self.image_viewer.imv.getImageItem()
        if self.scatter_item is None:
            self.scatter_item = pg.ScatterPlotItem()
            self.scatter_item.setZValue(10)
            self.scatter_item.setParentItem(img_item)
            
        if self.highlight_item is None:
            self.highlight_item = pg.ScatterPlotItem()
            self.highlight_item.setZValue(11)
            self.highlight_item.setParentItem(img_item)
        # Clean up old text items
        self._clear_text_items()


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
            
        pts_x = []
        pts_y = []

        oob_count = 0
        bad_count = 0

        if self.image_viewer.display_data is not None:
            shape = self.image_viewer.display_data.shape
            is_3d = (self.image_viewer.display_data.ndim == 3)
            max_x = shape[1] if is_3d else shape[0]
            max_y = shape[2] if is_3d else shape[1]
        else:
            max_x = float('inf')
            max_y = float('inf')
        
        # Save list of all valid label coordinates for viewport culling: (center_x, center_y, name_str)
        self.all_label_points = []

        for row in self.catalog_data:
            resolved = self._row_to_display(row)
            if resolved is None:
                # Unparseable, masked, or not convertible through the WCS
                bad_count += 1
                continue
            disp_x, disp_y = resolved

            if 0 <= disp_x < max_x and 0 <= disp_y < max_y:
                pts_x.append(disp_x + 0.5)
                pts_y.append(disp_y + 0.5)
                
                if name_col in self.catalog_data.colnames:
                    name_str = str(row[name_col])
                    self.all_label_points.append((disp_x + 0.5, disp_y + 0.5, name_str))
            else:
                oob_count += 1
                
        symbol = self._get_pg_symbol()
        size = self.spin_size.value()
        
        pen = pg.mkPen(color=self.marker_color, width=2)
        brush = pg.mkBrush(color=(0, 0, 0, 0))
        
        self.scatter_item.setData(x=pts_x, y=pts_y, symbol=symbol, size=size, pen=pen, brush=brush)
        status = f"Loaded: {len(self.catalog_data)} sources | {len(pts_x)} plotted | {oob_count} out of bounds"
        if bad_count:
            status += f" | {bad_count} unusable coordinates"
        self.lbl_status.setText(status)

        # Connect view range changes for debounced hide-on-pan / show-on-stop
        view = self.image_viewer.imv.getView()
        if not getattr(self, '_range_connected', False):
            self._label_timer = QTimer()
            self._label_timer.setSingleShot(True)
            self._label_timer.setInterval(200)  # ms delay after panning stops
            self._label_timer.timeout.connect(self.update_visible_text_labels)
            view.sigRangeChanged.connect(self._on_view_range_changed)
            self._range_connected = True

        self.update_visible_text_labels()
        self.on_table_selection()

    def _remove_scene_item(self, item):
        """Take a graphics item out of the viewer's scene entirely.

        `setParentItem(None)` is **not** removal: in Qt it makes the item a top-level item
        in the *same* scene, so it stays painted and simply stops tracking the image item
        (B7). `ViewBox.removeItem` is the right call — it drops the item from the ViewBox's
        `addedItems` bookkeeping as well as from the scene — and it tolerates items that
        were parented to the ImageItem instead of added to the view, which is how the
        markers get there.
        """
        if item is None:
            return
        if self.image_viewer is not None and hasattr(self.image_viewer, 'imv'):
            try:
                self.image_viewer.imv.getView().removeItem(item)
                return
            except Exception:
                pass
        # No viewer to ask (or it is already torn down): go straight to the scene
        try:
            scene = item.scene()
            if scene is not None:
                scene.removeItem(item)
            else:
                item.setParentItem(None)
        except Exception:
            pass

    def _clear_text_items(self):
        for txt in self.text_items:
            self._remove_scene_item(txt)
        self.text_items.clear()

    def _on_view_range_changed(self):
        """Called on every pan/zoom frame. Hides text instantly and debounces re-render."""
        # Hide all text items immediately for smooth panning
        for txt in self.text_items:
            txt.setVisible(False)
        # Restart debounce timer — labels re-appear 200ms after panning stops
        if hasattr(self, '_label_timer'):
            self._label_timer.start()

    def update_visible_text_labels(self):
        """Viewport culling: Renders text labels ONLY for catalog sources within current screen viewport."""
        if self.image_viewer is None or not hasattr(self.image_viewer, 'imv'):
            return

        view = self.image_viewer.imv.getView()

        # Remove old visible text items
        self._clear_text_items()

        if not hasattr(self, 'chk_show_name') or not self.chk_show_name.isChecked():
            return

        if not getattr(self, 'all_label_points', None):
            return

        rect = view.viewRect()
        img_item = self.image_viewer.imv.getImageItem()

        for px, py, name_str in self.all_label_points:
            parent_pt = img_item.mapToParent(pg.QtCore.QPointF(px, py)) if img_item else pg.QtCore.QPointF(px, py)
            if rect.contains(parent_pt.x(), parent_pt.y()):
                txt = pg.TextItem(name_str, color=self.marker_color.name(), anchor=(0, 1))
                txt.setZValue(12)
                txt.setPos(parent_pt.x(), parent_pt.y())
                view.addItem(txt)
                self.text_items.append(txt)
        
    def clear_selection(self):
        """Drop the selected row and its highlight, leaving the view where it is.

        Deliberately does not move the view back: the user may have panned since, and returning to
        wherever the selection happened to leave things would be its own surprise.
        """
        self.table.clearSelection()
        self.table.setCurrentItem(None)
        if self.highlight_item is not None:
            self.highlight_item.clear()
            self.highlight_item.setVisible(False)
        if hasattr(self, 'btn_clear_selection'):
            self.btn_clear_selection.setEnabled(False)

    def on_table_selection(self):
        if self.highlight_item is None or self.catalog_data is None:
            return
            
        selected_rows = self.table.selectedItems()
        if hasattr(self, 'btn_clear_selection'):
            self.btn_clear_selection.setEnabled(bool(selected_rows))
        if not selected_rows:
            self.highlight_item.clear()
            self.highlight_item.setVisible(False)
            return
            
        row_idx = selected_rows[0].row()
        row = self.catalog_data[row_idx]

        resolved = self._row_to_display(row)
        if resolved is None:
            self.highlight_item.clear()
            return
        disp_x, disp_y = resolved

        # Draw red highlight marker centered on pixel (disp_x + 0.5, disp_y + 0.5)
        pen = pg.mkPen(color=QColor(255, 0, 0), width=3)
        brush = pg.mkBrush(color=(0, 0, 0, 0))
        size = self.spin_size.value() + 10
        self.highlight_item.setData(x=[disp_x + 0.5], y=[disp_y + 0.5], symbol='o', size=size, pen=pen, brush=brush)
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
                center_x = disp_x + 0.5
                center_y = disp_y + 0.5
                view.setRange(xRange=(center_x - width/2, center_x + width/2), 
                              yRange=(center_y - height/2, center_y + height/2), 
                              padding=0)
        
    def build_context_menu(self, row_idx):
        """The menu for one table row.

        Built separately from being shown because `QMenu.exec` is modal and blocks until dismissed,
        so a menu that is popped up cannot be inspected — the same split as
        `MainWindow.build_region_menu`.
        """
        row_data = self.catalog_data[row_idx]

        menu = QMenu(self)
        menu.addAction("Copy Coordinates").triggered.connect(
            lambda: self.copy_row_coordinates(row_data))
        menu.addAction("Center on Source").triggered.connect(self.on_table_selection)
        menu.addAction("Clear Selection").triggered.connect(self.clear_selection)
        menu.addSeparator()
        menu.addAction("Delete Marker").triggered.connect(lambda: self.delete_row(row_idx))
        return menu

    def copy_row_coordinates(self, row_data):
        x_col = self.combo_x.currentText()
        y_col = self.combo_y.currentText()
        if x_col in self.catalog_data.colnames and y_col in self.catalog_data.colnames:
            QApplication.clipboard().setText(
                f"X: {row_data[x_col]}, Y: {row_data[y_col]}")

    def delete_row(self, row_idx):
        self.catalog_data.remove_row(row_idx)
        self.populate_table()
        self.auto_assign_columns()
        self.update_plot()

    def show_context_menu(self, pos):
        selected_rows = self.table.selectedItems()
        if not selected_rows:
            return

        menu = self.build_context_menu(selected_rows[0].row())
        # Held on self: a QMenu with no Python owner is deleted before it can be shown.
        self._context_menu = menu
        menu.exec(self.table.viewport().mapToGlobal(pos))

    def closeEvent(self, event):
        if getattr(self, '_range_connected', False) and self.image_viewer is not None:
            try:
                self.image_viewer.imv.getView().sigRangeChanged.disconnect(self._on_view_range_changed)
            except Exception:
                pass
            self._range_connected = False
        if hasattr(self, '_label_timer'):
            self._label_timer.stop()

        for attr in ('scatter_item', 'highlight_item'):
            self._remove_scene_item(getattr(self, attr, None))
            setattr(self, attr, None)

        self._clear_text_items()

        super().closeEvent(event)

import gc
import os
from collections import namedtuple

import numpy as np
from PySide6.QtWidgets import (
    QHBoxLayout, QPushButton, QLabel,
    QComboBox, QSpinBox, QCheckBox, QTableView,
    QFileDialog, QAbstractItemView, QColorDialog, QLineEdit,
    QGroupBox, QMenu, QApplication, QInputDialog
)
from PySide6.QtCore import Qt, QTimer, QAbstractTableModel, QModelIndex
from PySide6.QtGui import QColor, QKeySequence, QShortcut
import pyqtgraph as pg
import astropy.io.ascii as ascii
from astropy.io import fits
from astropy.table import Table
from astropy.coordinates import SkyCoord
import astropy.units as u
import warnings

from pyql3.gui.label_policy import (
    LABEL_REDRAW_DELAY_MS,
    LabelDensityGuard,
    grown_for_labels,
)
from pyql3.gui.tools.base_tool import BaseToolDialog

# Suffixes that make a file worth trying as a FITS table before handing it to
# astropy's ASCII reader. Compressed variants are included because astropy opens
# them transparently.
FITS_SUFFIXES = (
    '.fits', '.fit', '.fts', '.fz',
    '.fits.gz', '.fit.gz', '.fts.gz', '.fits.bz2', '.fits.zip',
)


# Column names recognised when guessing which columns hold coordinates, **best match
# first** — the tuple order is the ranking. FITS source tables rarely use the bare names a
# hand-written CSV does, so the photutils (`xcentroid`, `x_fit`) and SExtractor
# (`X_IMAGE`, `ALPHA_J2000`) spellings are included. `x_fit` outranks `x_init` because the
# fitted position is the measurement and the initial one is only the guess the fit started
# from. Comparison is done in lower case.
RA_COLUMN_NAMES = ('ra', 'right ascension', 'alpha', 'raj2000', 'ra_j2000',
                   'alpha_j2000', 'ra_deg', 'radeg')
DEC_COLUMN_NAMES = ('dec', 'declination', 'delta', 'decj2000', 'dec_j2000',
                    'delta_j2000', 'dec_deg', 'decdeg')
X_COLUMN_NAMES = ('x', 'xcenter', 'xc', 'x_c', 'xcentroid', 'x_image', 'xpix',
                  'x_pix', 'xpos', 'x_pos', 'x_fit', 'x_init')
Y_COLUMN_NAMES = ('y', 'ycenter', 'yc', 'y_c', 'ycentroid', 'y_image', 'ypix',
                  'y_pix', 'ypos', 'y_pos', 'y_fit', 'y_init')

# A name list can never keep up with what pipelines actually emit, so a pixel column is
# also recognised from its axis letter. Anything after the letter is accepted when a
# separator says the letter stood on its own (`x_fit`, `X-POS`, `x.1`); without a separator
# the remainder has to be a coordinate word, so that `xwin_image` is recognised while
# `year` and `ymag` are not. RA/Dec have no such letter and match by name only.
_COORD_SEPARATORS = '_-. '
_COORD_WORDS = ('centroid', 'center', 'centre', 'cen', 'coord', 'image',
                'pixel', 'pix', 'pos', 'win', 'world', 'fit', 'init', 'c')

# Rank bands, compared against the index into the name tuples above. An explicit name beats
# a separated spelling, which beats a run-on coordinate word.
_RANK_SEPARATED = 100
_RANK_WORD = 200


def coord_column_rank(colname, explicit, letter=None):
    """How well `colname` matches one coordinate axis. Lower is better; None is no match.

    `explicit` is the ordered tuple of known spellings for that axis. `letter` enables the
    axis-letter heuristic described above and is `'x'` or `'y'` for pixel columns, None for
    RA/Dec.
    """
    name = colname.lower()
    if name in explicit:
        return explicit.index(name)
    if letter is None or not name.startswith(letter):
        return None
    rest = name[1:]
    if not rest:
        return None
    if rest[0] in _COORD_SEPARATORS:
        return _RANK_SEPARATED
    if any(rest.startswith(word) for word in _COORD_WORDS):
        return _RANK_WORD
    return None


def best_coord_column(colnames, explicit, letter=None):
    """Index of the column most likely to hold this axis, or None if nothing matches.

    Ties go to the column that comes first in the table, so a catalog listing the same
    spelling twice behaves predictably.
    """
    best_rank = None
    best_idx = None
    for idx, cname in enumerate(colnames):
        rank = coord_column_rank(cname, explicit, letter)
        if rank is not None and (best_rank is None or rank < best_rank):
            best_rank, best_idx = rank, idx
    return best_idx


def paired_column(colnames, chosen_idx, letter, mate_letter):
    """Index of `chosen_idx`'s counterpart on the other axis, spelled the same way.

    A catalog carrying both `x_init`/`y_init` and `x_fit`/`y_fit` offers two valid pairs, and
    taking the best X with the first-listed Y mixes them: `x_fit` with `y_init` is a position
    no row ever had. Returns None when the counterpart is absent.
    """
    if chosen_idx is None:
        return None
    name = colnames[chosen_idx].lower()
    if not name.startswith(letter):
        return None
    mate = mate_letter + name[1:]
    for idx, cname in enumerate(colnames):
        if cname.lower() == mate:
            return idx
    return None


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


# What a masked cell (a FITS TNULL, or an undefined value) reads as in the table. This is
# what `str(np.ma.masked)` produced when every cell was formatted individually, and it is
# kept so the table looks the same as it always has.
MASKED_TEXT = '--'

# How many rows to sample when fitting the column widths. Qt's default is 1000, which costs
# 0.76 s on a 66k-row catalog because every sampled cell is a Python call into the model;
# 100 rows is 0.06 s and picks the same widths for every catalog tried.
COLUMN_WIDTH_SAMPLE_ROWS = 100

#: A ceiling on catalog labels built at once. A hang guard, exactly as
#: `label_policy.LABEL_SAFETY_LIMIT` is for regions, but measured for this overlay: a catalog can
#: be asked for one label per row, and a JWST prior catalog has 66,196 of them.
#:
#: Pooling made the repeat cost almost free, so this bounds only the *first* build of a set — what
#: it costs to tick *Labels*, or to zoom out until that many sources are in view. Measured with the
#: pool in place, first build against redraw after a pan:
#:
#: ===========  ===========  ===========
#: labels       first build  pan redraw
#: ===========  ===========  ===========
#: 1,000        0.22 s       0.01 s
#: 5,000        1.44 s       0.03 s
#: 10,000       4.80 s       0.11 s
#: 20,000       20.87 s      0.12 s
#: ===========  ===========  ===========
#:
#: The first column is superlinear because each new item's `addItem` walks the ViewBox, so the
#: ceiling has to sit where a one-off stall is still tolerable: 5000 is 1.4 s, and the same value
#: the region overlay settled on for a comparable ~1 s. 10,000 would be five seconds.
CATALOG_LABEL_LIMIT = 5000

#: How much larger than the visible set the label pool may grow before it is trimmed, and the
#: floor below which trimming is not worth the churn. A pan that changes the count slightly should
#: reuse what is there rather than free and rebuild it.
LABEL_POOL_SLACK = 1.5
LABEL_POOL_MIN = 200

# The "no parent" index a table model is asked about. Held as a singleton because Qt always
# passes a parent, so the override signatures have to accept one, and constructing a
# QModelIndex in a default argument builds it once at import anyway.
NO_PARENT = QModelIndex()


def cell_text(value):
    """One catalog cell as the string the table shows."""
    if value is None or value is np.ma.masked or np.ma.is_masked(value):
        return MASKED_TEXT
    if isinstance(value, (float, np.floating)):
        return f"{value:.5g}"
    if isinstance(value, bytes):
        # FITS character columns can come through as bytes
        return value.decode('utf-8', 'replace').strip()
    return str(value)


def column_texts(column):
    """Every cell of one column as display strings, read as a whole column.

    Reading cells one at a time through `Table.Row` costs about 1.7 us each — almost all of it
    numpy re-wrapping a masked scalar per cell (`MaskedArray.view` -> `__array_finalize__` ->
    `_update_from`) — which came to 3.9 s over a 66196 x 34 catalog. The same values read
    column-wise take 0.24 s for byte-identical output.
    """
    values = np.asarray(column)
    if values.dtype.kind == 'f':
        texts = [f"{v:.5g}" for v in values.tolist()]
    elif values.dtype.kind == 'S':
        texts = [v.decode('utf-8', 'replace').strip() for v in values.tolist()]
    else:
        texts = [str(v) for v in values.tolist()]

    # np.asarray on a MaskedColumn hands back the fill values, so the mask is applied after
    mask = np.ma.getmaskarray(column) if np.ma.isMaskedArray(column) else None
    if mask is not None and mask.any():
        texts = [MASKED_TEXT if m else t
                 for t, m in zip(texts, mask.tolist(), strict=True)]
    return texts


class CatalogTableModel(QAbstractTableModel):
    """A read-only view of an astropy `Table` for the catalog table.

    Nothing is built per row. `QTableWidget` needed one `QTableWidgetItem` per cell, which on a
    66196 x 34 JWST prior catalog meant 2.25 M objects — 7.7 s and 1.03 GB before the window
    came back, spent so that the ~30 rows which fit on screen could be drawn. A model formats
    a cell only when Qt asks for it, so a load costs nothing per row: the same catalog loads in
    0.57 s and adds 67 MB.

    **Every row is still present and scrollable** — this trades when the work happens, not how
    much of the catalog is shown.
    """

    def __init__(self, table=None, parent=None):
        super().__init__(parent)
        self._table = None
        self._colnames = []
        self._search_rows = None
        if table is not None:
            self.set_table(table)

    # -- the astropy table behind the view ---------------------------------------------

    def set_table(self, table):
        """Show a different catalog."""
        self.beginResetModel()
        self._table = table
        self._colnames = list(table.colnames) if table is not None else []
        self._search_rows = None
        self.endResetModel()

    def remove_row(self, row):
        """Drop one row, telling Qt about that row rather than resetting the whole view."""
        if self._table is None or not 0 <= row < len(self._table):
            return
        self.beginRemoveRows(NO_PARENT, row, row)
        self._table.remove_row(row)
        self._search_rows = None
        self.endRemoveRows()

    def search_rows(self):
        """One lower-cased string per row, for the search box; built on first use.

        Built column-wise for the reason `column_texts` documents, kept until the table
        changes, and not built at all for a catalog nobody searches.
        """
        if self._search_rows is None:
            parts = [''] * self.rowCount()
            for cname in self._colnames:
                for i, text in enumerate(column_texts(self._table[cname])):
                    parts[i] = f"{parts[i]} {text}"
            self._search_rows = [p.lower() for p in parts]
        return self._search_rows

    # -- QAbstractTableModel -----------------------------------------------------------

    def rowCount(self, parent=NO_PARENT):
        if parent.isValid() or self._table is None:
            return 0
        return len(self._table)

    def columnCount(self, parent=NO_PARENT):
        return 0 if parent.isValid() else len(self._colnames)

    def data(self, index, role=Qt.DisplayRole):
        if role != Qt.DisplayRole or not index.isValid():
            return None
        column = self._table[self._colnames[index.column()]]
        return cell_text(column[index.row()])

    def headerData(self, section, orientation, role=Qt.DisplayRole):
        if role != Qt.DisplayRole:
            return None
        if orientation == Qt.Horizontal:
            return self._colnames[section]
        return str(section + 1)


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
        
        # Held above setup_ui, because update_plot is a slot the widgets it builds connect to
        self._plot_suspended = 0

        self.catalog_table = None
        self.catalog_data = None
        self.scatter_item = None
        self.highlight_item = None
        self.text_items = []
        #: The status line update_plot last wrote, restored when the label ceiling lifts.
        self._plot_status = ""
        #: Label items taken out of the scene but not yet released. See `_retire_items`.
        self._retired = []
        #: Applies CATALOG_LABEL_LIMIT and reports when that verdict changes.
        self._label_guard = LabelDensityGuard(on_change=self._on_labels_suppressed)
        
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
        
        # Table. A model-backed QTableView rather than a QTableWidget, so that a large
        # catalog costs nothing per row -- see CatalogTableModel for the measurements.
        self.table_model = CatalogTableModel(parent=self)
        self.table = QTableView()
        self.table.setModel(self.table_model)
        self.table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SingleSelection)
        self.table.horizontalHeader().setResizeContentsPrecision(COLUMN_WIDTH_SAMPLE_ROWS)
        # The selection model survives the model resets in set_table, so this connection is
        # made once. Reconnecting it per load would fire the handler for the reset itself.
        self.table.selectionModel().selectionChanged.connect(self.on_table_selection)
        
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

        # A reload must not undo a column choice made by hand. Re-running the guess on every
        # load meant a manual x_fit/y_fit selection was silently replaced the next time the
        # file was read, putting the overlay back where the guess wanted it.
        keep = self._column_choice()
        self.populate_table()
        # Assigning the columns reaches update_plot through update_columns_for_type;
        # suspending it keeps a load to one O(rows) plotting pass instead of two.
        self._plot_suspended += 1
        try:
            if not self._restore_column_choice(keep):
                self.auto_assign_columns()
        finally:
            self._plot_suspended -= 1
        self.update_plot()

    def _column_choice(self):
        """The current X/Y/name/type choice, or None if nothing has been chosen yet.

        Read *before* `populate_table` refills the combo boxes, which forgets it.
        """
        if self.combo_x.count() == 0:
            return None
        return {
            'coord_type': self.combo_coord_type.currentIndex(),
            'x': self.combo_x.currentText(),
            'y': self.combo_y.currentText(),
            'name': self.combo_name.currentText(),
        }

    def _restore_column_choice(self, keep):
        """Re-apply a remembered choice. False if the new table cannot honour it.

        Both X and Y have to be present: restoring one of the pair would plot a coordinate
        no row ever had. The coordinate type comes back with them, since keeping x_fit/y_fit
        while resetting the type would move every marker. The name column is optional — a
        missing label costs only the label, so the usual guess covers it.
        """
        if not keep:
            return False
        cols = self.catalog_data.colnames
        if keep['x'] not in cols or keep['y'] not in cols:
            return False

        for combo, value in ((self.combo_coord_type, keep['coord_type']),
                             (self.combo_x, keep['x']),
                             (self.combo_y, keep['y'])):
            combo.blockSignals(True)
            if isinstance(value, int):
                combo.setCurrentIndex(value)
            else:
                combo.setCurrentText(value)
            combo.blockSignals(False)

        if keep['name'] in cols:
            self.combo_name.blockSignals(True)
            self.combo_name.setCurrentText(keep['name'])
            self.combo_name.blockSignals(False)
        else:
            self._assign_name_column()
        return True

    def populate_table(self):
        if self.catalog_data is None:
            return
            
        cols = self.catalog_data.colnames

        # Handing the table to the model is the whole of it: no cell is touched until Qt
        # paints one. The header is left Interactive (QTableView's default), never
        # ResizeToContents -- that mode is persistent, and leaving it on made the *next* load
        # quadratic, freezing the window for minutes (see BUGS.md M28).
        self.table_model.set_table(self.catalog_data)
        self.table.resizeColumnsToContents()
        # A model reset clears the hidden rows, so a search still in the box is re-applied
        self.filter_table(self.search_bar.text())

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
        """Hide the rows that do not contain `text`. No row is ever dropped.

        Rows are hidden on the view rather than filtered through a `QSortFilterProxyModel`,
        for two reasons. A view row is then always *the* catalog row, and the highlight and
        Delete Marker both index `catalog_data` with it — through a proxy each would need
        `mapToSource`, and getting that wrong deletes a source the user never selected. It is
        also the faster of the two: 0.02 s against the proxy's 1.47 s over 66k rows.

        The pass is bracketed by `setUpdatesEnabled(False)`, because `setRowHidden` re-lays the
        view out on each of those 66k calls otherwise — 1.26 s against 0.02 s. Blocking the
        vertical header's signals as well looks like more of the same optimisation and is not:
        it is no faster, and it leaves the scroll bar's range at its unfiltered value.
        """
        needle = text.lower()
        shown = ([needle in hay for hay in self.table_model.search_rows()] if needle
                 else [True] * self.table_model.rowCount())

        self.table.setUpdatesEnabled(False)
        try:
            for row, visible in enumerate(shown):
                self.table.setRowHidden(row, not visible)
        finally:
            self.table.setUpdatesEnabled(True)
            
    def auto_assign_columns(self):
        if self.catalog_data is None:
            return

        cols = self.catalog_data.colnames

        # Auto detect RA/DEC vs X/Y
        has_x = best_coord_column(cols, X_COLUMN_NAMES, 'x') is not None
        has_y = best_coord_column(cols, Y_COLUMN_NAMES, 'y') is not None
        has_ra = best_coord_column(cols, RA_COLUMN_NAMES) is not None
        has_dec = best_coord_column(cols, DEC_COLUMN_NAMES) is not None

        self.combo_coord_type.blockSignals(True)
        if has_x and has_y:
            self.combo_coord_type.setCurrentIndex(1) # Default to FITS Pixels
        elif has_ra and has_dec:
            self.combo_coord_type.setCurrentIndex(2) # World
        self.combo_coord_type.blockSignals(False)

        self.update_columns_for_type()
        self._assign_name_column()

    def _assign_name_column(self):
        """Point the label combo at whichever column reads as a source name.

        Signals are blocked because `update_plot` runs once at the end of the load; leaving
        them live spent a second full O(rows) plotting pass on every catalog opened.
        """
        self.combo_name.blockSignals(True)
        for i, c in enumerate(self.catalog_data.colnames):
            if c.lower() in ('name', 'id', 'object', 'source'):
                self.combo_name.setCurrentIndex(i)
                break
        self.combo_name.blockSignals(False)

    def _numeric_column_indices(self):
        """Indices of the columns holding numbers, as a last resort for the coordinate guess.

        Tested on the column's dtype rather than by calling `float()` on row 0. The old test
        accepted a string column of digits — which is how a JWST prior catalog's `id` came to
        be plotted as an X coordinate — and warned on a masked first element.
        """
        return [i for i, cname in enumerate(self.catalog_data.colnames)
                if np.issubdtype(self.catalog_data[cname].dtype, np.number)]

    def update_columns_for_type(self):
        if self.catalog_data is None:
            return

        cols = self.catalog_data.colnames
        is_world = self.combo_coord_type.currentIndex() == 2

        self.combo_x.blockSignals(True)
        self.combo_y.blockSignals(True)

        if is_world:
            x_idx = best_coord_column(cols, RA_COLUMN_NAMES)
            y_idx = best_coord_column(cols, DEC_COLUMN_NAMES)
        else:
            x_idx = best_coord_column(cols, X_COLUMN_NAMES, 'x')
            y_idx = best_coord_column(cols, Y_COLUMN_NAMES, 'y')
            # Keep both axes on one spelling once X is settled
            mate = paired_column(cols, x_idx, 'x', 'y')
            if mate is not None:
                y_idx = mate

        # Fallback to numeric columns if no name matched
        if x_idx is None or y_idx is None:
            numeric = [i for i in self._numeric_column_indices()
                       if i not in (x_idx, y_idx)]
            if x_idx is None and numeric:
                x_idx = numeric.pop(0)
            if y_idx is None and numeric:
                y_idx = numeric.pop(0)

        if x_idx is not None:
            self.combo_x.setCurrentIndex(x_idx)
        if y_idx is not None:
            self.combo_y.setCurrentIndex(y_idx)

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
        # Suspended while a load assigns the columns: each assignment would otherwise cost a
        # full pass over every row for a plot that is about to be replaced anyway.
        if self._plot_suspended:
            return
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
        # Hide the labels, keeping the pool: this method ends by calling
        # update_visible_text_labels, which reuses the items for whatever is now in view.
        # Destroying them here is what made every plot refresh pay to rebuild them.
        self._hide_all_labels()

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
        # Kept so that _on_labels_suppressed can put it back after its own message
        self._plot_status = status
        self.lbl_status.setText(status)

        # Connect view range changes for debounced hide-on-pan / show-on-stop
        view = self.image_viewer.imv.getView()
        if not getattr(self, '_range_connected', False):
            self._label_timer = QTimer()
            self._label_timer.setSingleShot(True)
            self._label_timer.setInterval(LABEL_REDRAW_DELAY_MS)
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
        # Hidden, not destroyed: the items are reused on the redraw after the view settles
        self._hide_all_labels()
        # Restart debounce timer — labels re-appear LABEL_REDRAW_DELAY_MS after panning stops
        if hasattr(self, '_label_timer'):
            self._label_timer.start()

    def _on_labels_suppressed(self, count):
        """Say why the labels went away, once, when the ceiling starts or stops refusing them.

        Without this the checkbox looks broken: it is ticked and no text appears. `MainWindow`
        does the same for regions from `RegionLayer.labels_suppressed`.
        """
        if count:
            self.lbl_status.setText(
                f"{count:,} labels in view — more than the {CATALOG_LABEL_LIMIT:,} that can be "
                "drawn at once. Zoom in to label fewer sources.")
        else:
            # Only the message needs undoing. The pass that called this is about to draw the
            # labels itself, and re-running update_plot from inside it would recurse.
            self.lbl_status.setText(self._plot_status)

    def _hide_all_labels(self):
        """Hide every pooled label without giving up the items."""
        for txt in self.text_items:
            txt.setVisible(False)

    def update_visible_text_labels(self):
        """Draw labels for the catalog sources in view, reusing the items from last time.

        Three things bound the cost, all of them shared with the region overlay
        (`pyql3.gui.label_policy`): labels are hidden while the view moves and rebuilt
        `LABEL_REDRAW_DELAY_MS` after it settles, culled to the visible rect grown by
        `LABEL_CULL_MARGIN`, and refused outright above `CATALOG_LABEL_LIMIT`.

        The items are **pooled**, which is where this differs from the region overlay. A region's
        label is built once with its region and then only toggled, because the region count is
        whatever the user drew; a catalog's count is whatever the file has, and building one item
        per row for a 66k-row catalog would move the same blowup from pan time to load time.
        Rebuilding the visible set from scratch on every redraw was what made a pan cost 23.9 s at
        20,000 labels — the per-label cost rises with the number of items already in the scene
        (0.22 ms at 1000, 0.85 ms at 20,000), because each `addItem` walks the ViewBox. Reusing
        the items skips all of that: only the text and the position change.
        """
        if self.image_viewer is None or not hasattr(self.image_viewer, 'imv'):
            return

        if not hasattr(self, 'chk_show_name') or not self.chk_show_name.isChecked():
            self._hide_all_labels()
            return

        if not getattr(self, 'all_label_points', None):
            self._hide_all_labels()
            return

        view = self.image_viewer.imv.getView()
        img_item = self.image_viewer.imv.getImageItem()
        rect = grown_for_labels(view.viewRect())

        wanted = []
        for px, py, name_str in self.all_label_points:
            point = pg.QtCore.QPointF(px, py)
            parent_pt = img_item.mapToParent(point) if img_item else point
            if rect is None or rect.contains(parent_pt):
                wanted.append((parent_pt, name_str))

        if not self._label_guard.allows(len(wanted), CATALOG_LABEL_LIMIT):
            self._hide_all_labels()
            return

        # Grow the pool to what is needed; it is only ever added to here, and trimmed back
        # deliberately in _trim_label_pool
        colour = self.marker_color.name()
        while len(self.text_items) < len(wanted):
            txt = pg.TextItem(color=colour, anchor=(0, 1))
            txt.setZValue(12)
            view.addItem(txt)
            self.text_items.append(txt)

        for txt, (parent_pt, name_str) in zip(self.text_items, wanted, strict=False):
            txt.setColor(colour)
            txt.setText(name_str)
            txt.setPos(parent_pt)
            txt.setVisible(True)
        for txt in self.text_items[len(wanted):]:
            txt.setVisible(False)

        self._trim_label_pool(len(wanted))

    def _trim_label_pool(self, needed):
        """Give back the pool once it is far larger than the view needs.

        Without this the pool sits at its high-water mark for the life of the dialog, so zooming
        out once and back in would keep a view's worth of labels alive at ~35 kB each. The slack
        factor stops a pan that shifts the count slightly from churning items.

        Retired items go through `_retire_items`, never straight out of scope: dropping the last
        Python reference to a `QGraphicsItem` can segfault (`BUGS.md` M18, M27).
        """
        keep = max(LABEL_POOL_MIN, int(needed * LABEL_POOL_SLACK))
        if len(self.text_items) <= keep:
            return
        excess = self.text_items[keep:]
        del self.text_items[keep:]
        for txt in excess:
            self._remove_scene_item(txt)
        self._retire_items(excess)

    def _retire_items(self, items):
        """Hold `items` until the event loop is back, then release them with a collection.

        The pattern `region_layer` arrived at the hard way: these items sit in reference cycles,
        so dropping the list frees nothing without an explicit `gc.collect()`, and freeing them
        inside the call that replaced them is what crashed (`BUGS.md` M18).
        """
        if not items:
            return
        self._retired.extend(items)
        QTimer.singleShot(0, self._release_retired)

    def _release_retired(self):
        if not self._retired:
            return
        self._retired.clear()
        gc.collect()

    def clear_selection(self):
        """Drop the selected row and its highlight, leaving the view where it is.

        Deliberately does not move the view back: the user may have panned since, and returning to
        wherever the selection happened to leave things would be its own surprise.
        """
        self.table.clearSelection()
        self.table.selectionModel().clearCurrentIndex()
        if self.highlight_item is not None:
            self.highlight_item.clear()
            self.highlight_item.setVisible(False)
        if hasattr(self, 'btn_clear_selection'):
            self.btn_clear_selection.setEnabled(False)

    def on_table_selection(self):
        if self.highlight_item is None or self.catalog_data is None:
            return
            
        selected_rows = self.table.selectionModel().selectedRows()
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
        """Drop one source from the catalog.

        Tells the model about the single row instead of rebuilding the table, and leaves the
        column choice alone: deleting a row cannot change which columns hold the coordinates,
        and re-guessing here discarded a manual choice exactly as a reload used to (M28).
        """
        self.table_model.remove_row(row_idx)
        # Every row below the deleted one has shifted up, so the search has to be re-applied
        # rather than left pointing at the rows its flags were computed for
        self.filter_table(self.search_bar.text())
        self.update_plot()

    def show_context_menu(self, pos):
        selected_rows = self.table.selectionModel().selectedRows()
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

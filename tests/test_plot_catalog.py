import numpy as np
import pyqtgraph as pg
import pytest
from astropy.io import fits

from PySide6.QtWidgets import QHeaderView

from pyql3.gui.tools.plot_catalog import (
    DEC_COLUMN_NAMES,
    cell_text,
    column_texts,
    PlotCatalogDialog,
    X_COLUMN_NAMES,
    Y_COLUMN_NAMES,
    coord_column_rank,
    fits_table_extensions,
    looks_like_fits,
    read_fits_table,
    to_float,
)
from pyql3.gui.viewers.image_viewer import ImageViewer


@pytest.fixture
def catalog_file(tmp_path):
    path = tmp_path / "catalog.csv"
    lines = ["ID,X,Y,Flux"]
    for i in range(5):
        lines.append(f"Src{i},{10 + i * 3},{12 + i * 2},{5.0 - i * 0.4}")
    path.write_text("\n".join(lines) + "\n")
    return str(path)


@pytest.fixture
def viewer(qapp):
    np.random.seed(0)
    v = ImageViewer()
    v.set_data(np.random.rand(40, 40).astype(np.float32))
    return v


def _marker_items(viewer):
    """Every catalog-drawn item currently living in the viewer's scene."""
    scene = viewer.imv.getView().scene()
    return [it for it in scene.items() if isinstance(it, (pg.ScatterPlotItem, pg.TextItem))]


def _off_the_scene(item):
    """True once `item` is out of the scene: removed from it, or deleted outright (R86)."""
    import shiboken6

    return not shiboken6.isValid(item) or item.scene() is None


def _open_dialog(viewer, catalog_file, with_labels=True):
    dlg = PlotCatalogDialog(None, viewer)
    dlg.load_catalog_file(catalog_file)
    if with_labels and hasattr(dlg, 'chk_show_name'):
        dlg.chk_show_name.setChecked(True)
        dlg.update_visible_text_labels()
    return dlg


def test_catalog_markers_are_removed_from_the_scene_on_close(viewer, catalog_file):
    """B7: closeEvent only called setParentItem(None), which in Qt makes an item top-level
    in the *same* scene rather than removing it.

    This test deliberately holds its own references to the items, because that is what
    makes the defect deterministic. The markers were parented with setParentItem() rather
    than view.addItem(), so PySide6 kept Python ownership; detaching the parent and then
    dropping the dialog's attribute left refcounting to destroy them, which usually cleaned
    the scene up by accident. Any surviving reference -- and this test is one -- leaves the
    marker painted with nothing able to clear it.
    """
    dlg = _open_dialog(viewer, catalog_file)
    scatter, highlight = dlg.scatter_item, dlg.highlight_item
    texts = list(dlg.text_items)

    assert scatter is not None and highlight is not None
    assert len(scatter.getData()[0]) == 5, "catalog did not plot"
    assert texts, "expected text labels with Show Name enabled"
    scene = viewer.imv.getView().scene()
    assert scatter.scene() is scene

    dlg.close()

    assert _off_the_scene(scatter), "scatter item left in the scene"
    assert _off_the_scene(highlight), "highlight item left in the scene"
    for txt in texts:
        assert _off_the_scene(txt), "text label left in the scene"
    assert _marker_items(viewer) == []
    # Deleting an item would also pass the checks above; the ViewBox must have let go of it too
    added = viewer.imv.getView().addedItems
    assert not any(item is entry for item in [scatter, highlight] + texts for entry in added), \
        "an item was deleted without being removed from the ViewBox"
    assert dlg.scatter_item is None and dlg.highlight_item is None
    assert dlg.text_items == []


def test_markers_do_not_accumulate_across_open_close_cycles(viewer, catalog_file):
    """MainWindow caches the dialog and reopens it, so leaked items would pile up."""
    baseline = len(_marker_items(viewer))

    for _ in range(3):
        dlg = _open_dialog(viewer, catalog_file)
        assert len(_marker_items(viewer)) > baseline
        dlg.close()
        assert len(_marker_items(viewer)) == baseline

    assert len(_marker_items(viewer)) == baseline


def test_close_is_idempotent(viewer, catalog_file):
    """Qt can deliver closeEvent more than once; the second pass must not raise."""
    dlg = _open_dialog(viewer, catalog_file)
    dlg.close()
    dlg.close()
    assert _marker_items(viewer) == []


def test_close_without_a_viewer_does_not_raise(qapp, catalog_file):
    """The fallback path when there is no viewer to ask for its ViewBox."""
    dlg = PlotCatalogDialog(None, None)
    dlg.load_catalog_file(catalog_file)
    dlg.close()
    assert dlg.scatter_item is None and dlg.highlight_item is None


def test_close_after_the_viewer_is_torn_down_does_not_raise(viewer, catalog_file):
    """Shutdown ordering is not guaranteed: the dialog may outlive the ImageView."""
    dlg = _open_dialog(viewer, catalog_file)
    dlg.image_viewer = None
    dlg.close()
    assert dlg.scatter_item is None and dlg.highlight_item is None


def test_reopening_redraws_the_markers(viewer, catalog_file):
    """Removal must not be so thorough that a reopened dialog draws nothing."""
    dlg = _open_dialog(viewer, catalog_file)
    dlg.close()

    dlg2 = _open_dialog(viewer, catalog_file)
    assert dlg2.scatter_item is not None
    assert len(dlg2.scatter_item.getData()[0]) == 5
    assert dlg2.scatter_item.scene() is viewer.imv.getView().scene()
    dlg2.close()


def test_text_labels_are_reused_not_rebuilt_on_refresh(viewer, catalog_file):
    """update_visible_text_labels runs after every pan, so the items have to be reused.

    This test used to assert the opposite — that the previous generation of items had left the
    scene — because the labels were destroyed and rebuilt on every redraw. That is what made one
    pan cost 23.9 s at 20,000 labels (`BUGS.md` M30), so the contract is now reuse: the same
    objects are repositioned and re-texted, and nothing accumulates in the scene.
    """
    dlg = _open_dialog(viewer, catalog_file)
    first = list(dlg.text_items)
    assert first

    dlg.update_visible_text_labels()

    assert len(dlg.text_items) == len(first)
    assert all(a is b for a, b in zip(dlg.text_items, first, strict=True)), (
        "labels were rebuilt instead of reused")
    for txt in first:
        assert txt.scene() is not None, "a reused label was dropped from the scene"
    live = [it for it in _marker_items(viewer) if isinstance(it, pg.TextItem)]
    assert len(live) == len(dlg.text_items), "labels accumulated in the scene"
    dlg.close()


def test_removal_does_not_depend_on_garbage_collection(viewer, catalog_file):
    """B7, the actual defect: the scene must be clean *before* refcounting gets involved.

    Pre-fix, `_marker_items()` came back empty here too -- but only because dropping the
    dialog's last reference destroyed the C++ objects. Asserting emptiness with the collector
    off is what distinguishes explicit removal from accidental cleanup. Since R86 the dialog
    also deletes the items itself, so each is either out of the scene or gone.
    """
    import gc

    dlg = _open_dialog(viewer, catalog_file)
    alive = [dlg.scatter_item, dlg.highlight_item] + list(dlg.text_items)

    gc.disable()
    try:
        dlg.close()
        assert _marker_items(viewer) == [], "items were left for the collector to clean up"
    finally:
        gc.enable()

    assert all(_off_the_scene(item) for item in alive)


# --------------------------------------------------------------------------------------
# FITS table catalogs
# --------------------------------------------------------------------------------------


def _write_fits_catalog(path):
    """A FITS catalog exercising what an ASCII catalog never produces.

    * a primary image HDU and a tile-compressed image HDU, neither of which is a catalog
      (`CompImageHDU` subclasses `BinTableHDU`, so it has to be excluded deliberately)
    * two table extensions, so the extension has to be chosen
    * a character column, a vector column, and a NaN coordinate
    """
    names = np.array([f"Src{i}" for i in range(6)])
    xs = np.array([10.0, 13.0, 16.0, 19.0, 22.0, np.nan])
    ys = np.array([12.0, 14.0, 16.0, 18.0, 20.0, 20.0])
    flux = np.arange(6, dtype=np.float32)
    spec = np.arange(24, dtype=np.float32).reshape(6, 4)

    sources = fits.BinTableHDU.from_columns([
        fits.Column(name='NAME', format='10A', array=names),
        fits.Column(name='X', format='D', array=xs),
        fits.Column(name='Y', format='D', array=ys),
        fits.Column(name='FLUX', format='E', array=flux),
        fits.Column(name='SPEC', format='4E', array=spec),
    ], name='SOURCES')

    backup = fits.BinTableHDU.from_columns([
        fits.Column(name='X', format='D', array=np.array([5.0, 6.0])),
        fits.Column(name='Y', format='D', array=np.array([7.0, 8.0])),
    ], name='BACKUP')

    # Integer columns with TNULL come back masked, not as NaN
    masked = fits.BinTableHDU.from_columns([
        fits.Column(name='X', format='J', array=np.array([11, -99, 13]), null=-99),
        fits.Column(name='Y', format='J', array=np.array([11, 12, 13]), null=-99),
    ], name='MASKED')

    hdul = fits.HDUList([
        fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)),
        sources,
        backup,
        fits.CompImageHDU(np.zeros((4, 4), dtype=np.float32), name='COMPRESSED'),
        masked,
    ])
    hdul.writeto(path, overwrite=True)
    return str(path)


@pytest.fixture
def fits_catalog_file(tmp_path):
    return _write_fits_catalog(tmp_path / "catalog.fits")


def test_looks_like_fits_recognises_the_usual_spellings():
    for name in ("a.fits", "A.FIT", "b.fts", "c.fits.gz", "d.fz"):
        assert looks_like_fits(name), name
    for name in ("a.csv", "b.txt", "c.dat", "fits.csv"):
        assert not looks_like_fits(name), name


def test_fits_table_extensions_lists_tables_only(fits_catalog_file):
    """The primary image and the tile-compressed image must not be offered as catalogs."""
    exts = fits_table_extensions(fits_catalog_file)
    assert [ext.index for ext in exts] == [1, 2, 4]
    assert [ext.name for ext in exts] == ['SOURCES', 'BACKUP', 'MASKED']
    assert '6 rows' in exts[0].label


def test_read_fits_table_defaults_to_the_first_table_and_drops_vector_columns(fits_catalog_file):
    table, label = read_fits_table(fits_catalog_file)
    assert table.colnames == ['NAME', 'X', 'Y', 'FLUX'], "vector column was not dropped"
    assert len(table) == 6
    assert 'SOURCES' in label and 'vector' in label


def test_read_fits_table_accepts_an_index_or_an_extname(fits_catalog_file):
    by_index, label_index = read_fits_table(fits_catalog_file, 2)
    by_name, label_name = read_fits_table(fits_catalog_file, 'BACKUP')
    assert by_index.colnames == by_name.colnames == ['X', 'Y']
    assert len(by_index) == len(by_name) == 2
    assert 'BACKUP' in label_index and 'BACKUP' in label_name


def test_read_fits_table_rejects_a_file_with_no_table(tmp_path):
    path = tmp_path / "image_only.fits"
    fits.PrimaryHDU(np.zeros((4, 4), dtype=np.float32)).writeto(path)
    with pytest.raises(ValueError, match="no table extension"):
        read_fits_table(str(path))


def test_fits_catalog_plots_its_sources(viewer, fits_catalog_file):
    dlg = _open_dialog(viewer, fits_catalog_file)

    assert dlg.catalog_data is not None
    assert len(dlg.scatter_item.getData()[0]) == 5, "the five valid rows should plot"
    # X/Y found by name, so FITS Pixels is the coordinate mode
    assert dlg.combo_coord_type.currentIndex() == 1
    assert (dlg.combo_x.currentText(), dlg.combo_y.currentText()) == ('X', 'Y')
    assert 'SOURCES' in dlg.lbl_file.text()
    assert 'unusable' in dlg.lbl_status.text(), "the NaN row should be reported"
    dlg.close()


def test_fits_catalog_labels_are_text_not_byte_reprs(viewer, fits_catalog_file):
    dlg = _open_dialog(viewer, fits_catalog_file)
    assert dlg.combo_name.currentText() == 'NAME'
    assert dlg.table.model().index(0, 0).data() == 'Src0'
    assert [txt.toPlainText() for txt in dlg.text_items][:1] == ['Src0']
    dlg.close()


def test_masked_coordinates_are_skipped_not_plotted_at_zero(viewer, fits_catalog_file):
    """`float(np.ma.masked)` is NaN, not an exception -- masking has to be tested for."""
    dlg = PlotCatalogDialog(None, viewer)
    dlg.load_catalog_file(fits_catalog_file, hdu='MASKED')
    assert len(dlg.catalog_data) == 3
    assert len(dlg.scatter_item.getData()[0]) == 2
    assert '1 unusable' in dlg.lbl_status.text()
    dlg.close()


def test_hdu_argument_selects_the_extension(viewer, fits_catalog_file):
    dlg = PlotCatalogDialog(None, viewer)
    dlg.load_catalog_file(fits_catalog_file, hdu=2)
    assert dlg.catalog_data.colnames == ['X', 'Y']
    assert len(dlg.scatter_item.getData()[0]) == 2
    dlg.close()


def test_a_single_table_file_needs_no_extension_prompt(qapp, tmp_path):
    """_choose_fits_hdu must not open a modal dialog when there is nothing to choose."""
    path = tmp_path / "one_table.fits"
    hdu = fits.BinTableHDU.from_columns([
        fits.Column(name='X', format='D', array=np.array([1.0])),
        fits.Column(name='Y', format='D', array=np.array([2.0])),
    ], name='ONLY')
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(path)

    dlg = PlotCatalogDialog(None, None)
    assert dlg._choose_fits_hdu(str(path)) == (True, 1)
    # An unreadable file defers to load_catalog_file, which reports the real error
    assert dlg._choose_fits_hdu(str(tmp_path / "missing.fits")) == (True, None)
    dlg.close()


def test_a_fits_table_under_an_unfamiliar_name_still_loads(viewer, tmp_path):
    path = _write_fits_catalog(tmp_path / "catalog.cat")
    dlg = PlotCatalogDialog(None, viewer)
    dlg.load_catalog_file(path)
    assert dlg.catalog_data is not None and dlg.catalog_data.colnames[:2] == ['NAME', 'X']
    assert len(dlg.scatter_item.getData()[0]) == 5
    dlg.close()


def test_a_broken_text_catalog_reports_the_text_error(viewer, tmp_path):
    """The FITS fallback must not mask the real failure of a genuine text catalog."""
    empty = tmp_path / "broken.csv"
    empty.write_text("")
    missing = tmp_path / "absent.csv"

    for path in (empty, missing):
        dlg = PlotCatalogDialog(None, viewer)
        dlg.load_catalog_file(str(path))
        text = dlg.lbl_file.text()
        assert text.startswith("Error loading file"), text
        assert 'table extension' not in text, "reported the FITS fallback's error instead"
        assert '\n' not in text and len(text) < 200, "astropy's guess dump leaked into the UI"
        dlg.close()


def test_to_float_rejects_what_cannot_be_plotted():
    assert to_float(3) == 3.0
    assert to_float("2.5") == 2.5
    assert to_float(np.float32(1.5)) == 1.5
    for bad in (None, np.ma.masked, np.nan, np.inf, "abc", np.arange(3)):
        assert to_float(bad) is None, bad


def test_world_coordinate_fits_table_maps_through_the_cube_wcs(loaded_viewer, tmp_path):
    """RA/DEC columns in a FITS table have to round-trip through the 3D WCS.

    OSIRIS puts RA on FITS axis 3, so this also checks that the celestial axes are found
    from the WCS rather than assumed to be the first two.
    """
    wcs = loaded_viewer.wcs
    pixels = [(10.0, 12.0), (20.0, 20.0), (30.0, 28.0)]

    ras, decs = [], []
    for px, py in pixels:
        # world_to_pixel_values takes/returns FITS axis order: (WAVE, DEC, RA)
        world = wcs.pixel_to_world_values(0.0, py, px)
        ras.append(float(world[2]))
        decs.append(float(world[1]))

    hdu = fits.BinTableHDU.from_columns([
        fits.Column(name='NAME', format='6A', array=np.array(['a', 'b', 'c'])),
        fits.Column(name='RA', format='D', array=np.array(ras)),
        fits.Column(name='DEC', format='D', array=np.array(decs)),
    ], name='WORLDCAT')
    path = tmp_path / "world.fits"
    fits.HDUList([fits.PrimaryHDU(), hdu]).writeto(path)

    dlg = PlotCatalogDialog(None, loaded_viewer)
    dlg.load_catalog_file(str(path))

    assert dlg.combo_coord_type.currentIndex() == 2, "should have chosen World (RA/DEC)"
    assert (dlg.combo_x.currentText(), dlg.combo_y.currentText()) == ('RA', 'DEC')

    got_x, got_y = dlg.scatter_item.getData()
    assert len(got_x) == 3
    for (px, py), gx, gy in zip(pixels, got_x, got_y, strict=True):
        assert abs(gx - (px + 0.5)) < 0.01, (gx, px)
        assert abs(gy - (py + 0.5)) < 0.01, (gy, py)
    dlg.close()


def test_sexagesimal_world_coordinates_still_parse(loaded_viewer, tmp_path):
    """The string branch of the coordinate resolver, now shared with the highlight path."""
    wcs = loaded_viewer.wcs
    world = wcs.pixel_to_world_values(0.0, 20.0, 20.0)
    from astropy.coordinates import SkyCoord

    crd = SkyCoord(float(world[2]), float(world[1]), unit='deg')
    hms, dms = crd.to_string('hmsdms').split()

    path = tmp_path / "sexagesimal.csv"
    path.write_text(f"NAME,RA,DEC\ncenter,{hms},{dms}\n")

    dlg = PlotCatalogDialog(None, loaded_viewer)
    dlg.load_catalog_file(str(path))
    got_x, got_y = dlg.scatter_item.getData()
    assert len(got_x) == 1
    assert abs(got_x[0] - 20.5) < 0.05 and abs(got_y[0] - 20.5) < 0.05

    # the table-selection highlight must resolve the same row the same way
    dlg.table.selectRow(0)
    hx, hy = dlg.highlight_item.getData()
    assert abs(hx[0] - got_x[0]) < 1e-9 and abs(hy[0] - got_y[0]) < 1e-9
    dlg.close()


def test_read_fits_table_rejects_an_extension_that_is_not_a_table(fits_catalog_file):
    """HDU 0 is the primary image and HDU 3 is a compressed image, not catalogs."""
    for bad_hdu in (0, 3, 99):
        with pytest.raises(ValueError, match="not a table extension"):
            read_fits_table(fits_catalog_file, bad_hdu)
    with pytest.raises(ValueError, match="no table extension named"):
        read_fits_table(fits_catalog_file, 'NOSUCHNAME')


# ------------------------------------------------- clearing a row selection

def catalog_with_rows(qapp, loaded_viewer, count=3):
    """A catalog dialog holding a few sources, in pixel coordinates."""
    from astropy.table import Table

    from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

    dialog = PlotCatalogDialog(None, loaded_viewer)
    dialog.set_catalog_table(Table({
        "name": [f"src {i}" for i in range(count)],
        "x": [float(i + 2) for i in range(count)],
        "y": [float(i + 2) for i in range(count)],
    }), "test")
    return dialog


def test_selecting_a_row_highlights_a_source(qapp, loaded_viewer):
    dialog = catalog_with_rows(qapp, loaded_viewer)
    try:
        dialog.table.selectRow(1)
        assert dialog.highlight_item.isVisible()
    finally:
        dialog.close()


def test_a_selection_can_be_cleared(qapp, loaded_viewer):
    """Qt's only built-in way out of a single-selection table is ctrl-clicking the selected row."""
    dialog = catalog_with_rows(qapp, loaded_viewer)
    try:
        dialog.table.selectRow(1)
        assert dialog.highlight_item.isVisible()

        dialog.clear_selection()

        assert not dialog.highlight_item.isVisible(), "the highlight stayed on the image"
        assert dialog.table.selectionModel().selectedRows() == []
        assert not dialog.table.currentIndex().isValid(), "the row still reads as current"
    finally:
        dialog.close()


def test_the_clear_button_follows_whether_anything_is_selected(qapp, loaded_viewer):
    dialog = catalog_with_rows(qapp, loaded_viewer)
    try:
        assert not dialog.btn_clear_selection.isEnabled(), "enabled with nothing selected"

        dialog.table.selectRow(0)
        assert dialog.btn_clear_selection.isEnabled()

        dialog.btn_clear_selection.click()
        assert not dialog.btn_clear_selection.isEnabled()
        assert not dialog.highlight_item.isVisible()
    finally:
        dialog.close()


def test_clearing_leaves_the_view_where_it_is(qapp, loaded_viewer):
    """Selecting recentres the view; un-selecting should not go wandering back."""
    dialog = catalog_with_rows(qapp, loaded_viewer)
    try:
        dialog.table.selectRow(2)
        before = loaded_viewer.imv.getView().viewRect()

        dialog.clear_selection()

        after = loaded_viewer.imv.getView().viewRect()
        assert (after.center().x(), after.center().y()) == \
            pytest.approx((before.center().x(), before.center().y()))
    finally:
        dialog.close()


def test_clearing_an_empty_selection_is_harmless(qapp, loaded_viewer):
    dialog = catalog_with_rows(qapp, loaded_viewer)
    try:
        dialog.clear_selection()
        dialog.clear_selection()
        assert not dialog.highlight_item.isVisible()
    finally:
        dialog.close()


def test_escape_in_the_table_clears_the_selection(qapp, loaded_viewer):
    from PySide6.QtCore import Qt
    from PySide6.QtGui import QKeySequence, QShortcut

    dialog = catalog_with_rows(qapp, loaded_viewer)
    try:
        dialog.table.selectRow(1)
        shortcuts = [child for child in dialog.table.findChildren(QShortcut)]
        escapes = [s for s in shortcuts if s.key() == QKeySequence(Qt.Key.Key_Escape)]
        assert escapes, "no Escape shortcut on the table"

        escapes[0].activated.emit()
        assert not dialog.highlight_item.isVisible()
    finally:
        dialog.close()


def test_the_context_menu_offers_clear_selection(qapp, loaded_viewer):
    """Built rather than shown: `QMenu.exec` is modal and would block the suite forever."""
    dialog = catalog_with_rows(qapp, loaded_viewer)
    try:
        dialog.table.selectRow(1)
        menu = dialog.build_context_menu(1)

        labels = [action.text() for action in menu.actions()]
        assert "Clear Selection" in labels, labels

        next(a for a in menu.actions() if a.text() == "Clear Selection").trigger()
        assert not dialog.highlight_item.isVisible()
    finally:
        dialog.close()


def test_the_context_menu_still_copies_and_deletes(qapp, loaded_viewer):
    """The menu was rebuilt to be testable; its existing actions must still work."""
    dialog = catalog_with_rows(qapp, loaded_viewer)
    try:
        menu = dialog.build_context_menu(1)
        next(a for a in menu.actions() if a.text() == "Copy Coordinates").trigger()
        assert "X: 3.0" in qapp.clipboard().text()

        before = len(dialog.catalog_data)
        next(a for a in dialog.build_context_menu(1).actions()
             if a.text() == "Delete Marker").trigger()
        assert len(dialog.catalog_data) == before - 1
    finally:
        dialog.close()


# --------------------------------------------------------------------------------------
# Reloading a catalog: the table header, and the columns the user chose
# --------------------------------------------------------------------------------------


def _write_named_table(path, colnames, nrows=4):
    """A one-extension FITS table whose columns are named as asked, all of them numeric."""
    cols = [fits.Column(name=name, format='D',
                        array=np.arange(nrows, dtype=float) + 1.0 + i)
            for i, name in enumerate(colnames)]
    fits.HDUList([fits.PrimaryHDU(),
                  fits.BinTableHDU.from_columns(cols, name='CAT')]).writeto(path)
    return str(path)


def test_the_table_header_is_not_left_auto_resizing(viewer, fits_catalog_file):
    """Loading a second catalog froze the window for minutes.

    `QHeaderView.ResizeToContents` is a *persistent* mode, not a one-shot sizing. Leaving it
    on made the next load quadratic: each of the `rows x cols` setItem() calls invalidated
    its column, so Qt re-measured every cell of that column before the next insert. Measured
    on a real 4704 x 31 JWST prior catalog: 0.7 s for the first load, still running after
    144 s for the second, and an isolated QTableWidget refill of the same size reproduced it
    with no pyql3 code involved.
    """
    dlg = _open_dialog(viewer, fits_catalog_file)
    try:
        header = dlg.table.horizontalHeader()
        for col in range(dlg.table.model().columnCount()):
            assert header.sectionResizeMode(col) != QHeaderView.ResizeToContents, (
                f"column {col} left in ResizeToContents; the next load will be quadratic")
    finally:
        dlg.close()


def test_the_columns_are_still_sized_to_their_contents(viewer, fits_catalog_file):
    """Releasing the resize mode must not give up the fitted widths it was there for."""
    dlg = _open_dialog(viewer, fits_catalog_file)
    try:
        cols = dlg.catalog_data.colnames
        wide = dlg.table.columnWidth(cols.index('NAME'))
        narrow = dlg.table.columnWidth(cols.index('X'))
        assert wide > narrow, "columns were left at Qt's uniform default width"
    finally:
        dlg.close()


def test_reloading_replaces_the_rows_rather_than_stacking_them(viewer, fits_catalog_file):
    """The second load has to leave the table holding exactly the new catalog."""
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(fits_catalog_file, hdu='SOURCES')
        dlg.load_catalog_file(fits_catalog_file, hdu='BACKUP')
        assert dlg.table.model().rowCount() == 2
        assert dlg.table.model().columnCount() == 2
        header = dlg.table.horizontalHeader()
        for col in range(dlg.table.model().columnCount()):
            assert header.sectionResizeMode(col) != QHeaderView.ResizeToContents
    finally:
        dlg.close()


@pytest.mark.parametrize("xname,yname", [
    ('x_fit', 'y_fit'),            # photutils PSF photometry
    ('X_FIT', 'Y_FIT'),            # ... in upper case
    ('x-pos', 'y-pos'),            # any separator after the axis letter
    ('XWIN_IMAGE', 'YWIN_IMAGE'),  # SExtractor, no separator after the letter
    ('xcentroid', 'ycentroid'),    # photutils segmentation
])
def test_pixel_columns_are_recognised_by_their_axis_letter(viewer, tmp_path, xname, yname):
    path = _write_named_table(tmp_path / f"{xname}.fits", ['flux', xname, yname])
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(path)
        assert (dlg.combo_x.currentText(), dlg.combo_y.currentText()) == (xname, yname)
        assert dlg.combo_coord_type.currentIndex() == 1, "should be FITS Pixels"
    finally:
        dlg.close()


def test_the_fitted_position_outranks_the_initial_guess(viewer, tmp_path):
    """x_init comes first in the file; x_fit is the measurement, so it is the one to plot."""
    path = _write_named_table(tmp_path / "both.fits",
                              ['x_init', 'y_init', 'flux_init', 'x_fit', 'y_fit'])
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(path)
        assert (dlg.combo_x.currentText(), dlg.combo_y.currentText()) == ('x_fit', 'y_fit')
    finally:
        dlg.close()


def test_the_two_axes_are_kept_on_one_spelling(viewer, tmp_path):
    """y_init precedes y_fit, but pairing x_fit with y_init would plot a mixed coordinate."""
    path = _write_named_table(tmp_path / "mixed.fits", ['x_fit', 'y_init', 'y_fit'])
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(path)
        assert (dlg.combo_x.currentText(), dlg.combo_y.currentText()) == ('x_fit', 'y_fit')
    finally:
        dlg.close()


def test_a_word_that_merely_begins_with_the_axis_letter_is_not_a_coordinate():
    """`year` must not be read as a Y column, nor `xray_flux` as an X column."""
    assert coord_column_rank('year', Y_COLUMN_NAMES, 'y') is None
    assert coord_column_rank('ymag', Y_COLUMN_NAMES, 'y') is None
    assert coord_column_rank('xray', X_COLUMN_NAMES, 'x') is None
    # and the ones that are
    assert coord_column_rank('x_fit', X_COLUMN_NAMES, 'x') is not None
    assert coord_column_rank('XWIN_IMAGE', X_COLUMN_NAMES, 'x') is not None
    # RA/Dec have no axis letter, so they match by name only
    assert coord_column_rank('delta_j2000', DEC_COLUMN_NAMES) is not None
    assert coord_column_rank('dust', DEC_COLUMN_NAMES) is None


def test_a_manual_column_choice_survives_reloading_the_same_catalog(viewer, fits_catalog_file):
    """B: a reload re-ran the guess and silently threw away the user's own choice.

    The reported sequence was exactly this -- set X to x_fit and Y to y_fit by hand, reload,
    and land back on the guessed columns with the overlay in the wrong place.
    """
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(fits_catalog_file, hdu='SOURCES')
        dlg.combo_x.setCurrentText('FLUX')
        dlg.combo_y.setCurrentText('X')
        dlg.combo_name.setCurrentText('Y')

        dlg.load_catalog_file(fits_catalog_file, hdu='SOURCES')

        assert dlg.combo_x.currentText() == 'FLUX'
        assert dlg.combo_y.currentText() == 'X'
        assert dlg.combo_name.currentText() == 'Y'
    finally:
        dlg.close()


def test_a_choice_the_new_table_cannot_honour_falls_back_to_guessing(viewer, fits_catalog_file):
    """BACKUP has no FLUX column, so keeping half the pair would plot a coordinate nobody
    asked for. Guess again instead."""
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(fits_catalog_file, hdu='SOURCES')
        dlg.combo_x.setCurrentText('FLUX')
        dlg.combo_y.setCurrentText('X')

        dlg.load_catalog_file(fits_catalog_file, hdu='BACKUP')

        assert (dlg.combo_x.currentText(), dlg.combo_y.currentText()) == ('X', 'Y')
    finally:
        dlg.close()


def test_a_kept_choice_keeps_its_coordinate_type(viewer, tmp_path):
    """The type and the columns are one decision: restoring x_fit/y_fit while resetting the
    type back to Display Pixels would move every marker."""
    path = _write_named_table(tmp_path / "typed.fits", ['x_fit', 'y_fit'])
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(path)
        dlg.combo_coord_type.setCurrentIndex(0)   # Display Pixels, by hand
        dlg.combo_x.setCurrentText('x_fit')
        dlg.combo_y.setCurrentText('y_fit')

        dlg.load_catalog_file(path)

        assert dlg.combo_coord_type.currentIndex() == 0
        assert (dlg.combo_x.currentText(), dlg.combo_y.currentText()) == ('x_fit', 'y_fit')
    finally:
        dlg.close()


# --------------------------------------------------------------------------------------
# The model-backed table: every row present, and a view row is the catalog row
# --------------------------------------------------------------------------------------


def test_every_row_of_a_large_catalog_is_present_and_readable(viewer, tmp_path):
    """The point of the model is *when* the cells are formatted, not how many are shown.

    A user who loads a catalog expects to be able to look at all of it, so this asserts the
    count and reads the last row — a truncating implementation passes a count check alone.
    """
    n = 20000
    path = tmp_path / "large.fits"
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.BinTableHDU.from_columns([
            fits.Column(name='x_fit', format='D', array=np.arange(n, dtype=float)),
            fits.Column(name='y_fit', format='D', array=np.arange(n, dtype=float) * 2),
        ], name='CAT'),
    ]).writeto(path)

    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(str(path))
        model = dlg.table.model()
        assert model.rowCount() == n, "rows went missing"
        assert model.index(n - 1, 0).data() == f"{float(n - 1):.5g}"
        assert model.index(n - 1, 1).data() == f"{float((n - 1) * 2):.5g}"
        assert f"Loaded: {n} sources" in dlg.lbl_status.text()
    finally:
        dlg.close()


def test_a_masked_cell_still_reads_as_a_dash(viewer, fits_catalog_file):
    """What `str(np.ma.masked)` produced when each cell was formatted on its own."""
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(fits_catalog_file, hdu='MASKED')
        assert dlg.table.model().index(1, 0).data() == '--'
        assert dlg.table.model().index(0, 0).data() == '11'
    finally:
        dlg.close()


def test_searching_hides_rows_without_dropping_them(viewer, fits_catalog_file):
    dlg = _open_dialog(viewer, fits_catalog_file)
    try:
        dlg.filter_table('Src3')
        assert dlg.table.model().rowCount() == 6, "the catalog itself must not shrink"
        assert not dlg.table.isRowHidden(3)
        assert dlg.table.isRowHidden(0)

        dlg.filter_table('')
        assert [dlg.table.isRowHidden(r) for r in range(6)] == [False] * 6
    finally:
        dlg.close()


def test_a_row_selected_while_filtered_highlights_that_same_source(viewer, fits_catalog_file):
    """The trap a QSortFilterProxyModel would have introduced.

    `on_table_selection` and `delete_row` both index `catalog_data` with the view's row
    number, so the two have to stay the same row. Filtering must not renumber anything.
    """
    dlg = _open_dialog(viewer, fits_catalog_file)
    try:
        dlg.filter_table('Src3')          # only row 3 is left visible
        dlg.table.selectRow(3)
        expected = dlg.catalog_data[3]

        x, y = dlg.highlight_item.getData()
        assert (x[0], y[0]) == (expected['X'] + 0.5, expected['Y'] + 0.5)
    finally:
        dlg.close()


def test_deleting_a_row_while_filtered_removes_the_selected_source(viewer, fits_catalog_file):
    dlg = _open_dialog(viewer, fits_catalog_file)
    try:
        dlg.filter_table('Src3')
        dlg.table.selectRow(3)
        doomed = float(dlg.catalog_data[3]['X'])
        before = len(dlg.catalog_data)

        next(a for a in dlg.build_context_menu(3).actions()
             if a.text() == "Delete Marker").trigger()

        assert len(dlg.catalog_data) == before - 1
        assert dlg.table.model().rowCount() == before - 1
        assert not np.any(np.asarray(dlg.catalog_data['X']) == doomed)
    finally:
        dlg.close()


def test_a_search_still_in_the_box_survives_a_reload(viewer, fits_catalog_file):
    """A model reset clears Qt's hidden rows, so the filter has to be re-applied."""
    dlg = _open_dialog(viewer, fits_catalog_file)
    try:
        dlg.filter_table('Src3')
        dlg.search_bar.setText('Src3')

        dlg.load_catalog_file(fits_catalog_file, hdu='SOURCES')

        assert not dlg.table.isRowHidden(3)
        assert dlg.table.isRowHidden(0), "the search was silently dropped by the reload"
    finally:
        dlg.close()


def test_column_texts_matches_cell_by_cell_formatting(fits_catalog_file):
    """The column-wise read is an optimisation, so it has to produce the same strings."""
    table, _ = read_fits_table(fits_catalog_file, 'MASKED')
    for cname in table.colnames:
        assert column_texts(table[cname]) == [cell_text(v) for v in table[cname]]


def test_filtering_updates_the_scroll_range(qapp, viewer, tmp_path):
    """Guards the batching in `filter_table`.

    `setRowHidden` re-lays the view out on every call, so a filter pass over 66k rows costs
    1.26 s unless it is bracketed by `setUpdatesEnabled(False)` (0.02 s). Blocking the vertical
    header's signals as well is the tempting next step and is wrong: it is no faster and the
    scroll bar keeps its unfiltered range, so the view scrolls through empty space.
    """
    n = 2000
    path = tmp_path / "scroll.fits"
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.BinTableHDU.from_columns([
            fits.Column(name='x_fit', format='D', array=np.arange(n, dtype=float)),
            fits.Column(name='y_fit', format='D', array=np.zeros(n)),
        ], name='CAT'),
    ]).writeto(path)

    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(str(path))
        dlg.show()
        dlg.table.resize(400, 300)
        qapp.processEvents()
        unfiltered = dlg.table.verticalScrollBar().maximum()
        assert unfiltered > 0, "the view never laid out, so this proves nothing"

        dlg.filter_table('1999')
        qapp.processEvents()
        assert sum(0 if dlg.table.isRowHidden(r) else 1 for r in range(n)) == 1
        assert dlg.table.verticalScrollBar().maximum() < unfiltered, (
            "the scroll range still covers the hidden rows")
    finally:
        dlg.close()


# --------------------------------------------------------------------------------------
# Label density: the ceiling, the cull margin and the item pool
# --------------------------------------------------------------------------------------


def _labelled_catalog(tmp_path, n, spread=40.0):
    """`n` named sources evenly along the diagonal of a `spread`-pixel box.

    Deliberately deterministic: these tests set a view range and assert how many labels fall in
    it, so randomly placed sources would make "a small view" mean a different count each run.
    """
    step = (spread / n) if n else 0.0
    coords = np.arange(n, dtype=float) * step
    path = tmp_path / f"labels{n}.fits"
    fits.HDUList([
        fits.PrimaryHDU(),
        fits.BinTableHDU.from_columns([
            fits.Column(name='id', format='10A',
                        array=np.array([f"S{i}" for i in range(n)])),
            fits.Column(name='x_fit', format='D', array=coords),
            fits.Column(name='y_fit', format='D', array=coords),
        ], name='CAT'),
    ]).writeto(path)
    return str(path)


def test_too_many_labels_are_refused_as_a_hang_guard(viewer, tmp_path, monkeypatch):
    """A ceiling so an enormous catalog cannot lock the window up, not a readability rule.

    At 20,000 labels one redraw took 23.9 s, and the per-label cost climbs with the number of
    items already in the scene, so a 66k-row catalog could not be drawn at all.
    """
    from pyql3.gui.tools import plot_catalog as module

    monkeypatch.setattr(module, "CATALOG_LABEL_LIMIT", 5)
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(_labelled_catalog(tmp_path, 20))
        dlg.chk_show_name.setChecked(True)
        dlg.update_visible_text_labels()

        assert not any(txt.isVisible() for txt in dlg.text_items)
        assert "20 labels in view" in dlg.lbl_status.text()
        assert "Zoom in" in dlg.lbl_status.text()
    finally:
        dlg.close()


def test_labels_come_back_once_few_enough_are_in_view(viewer, tmp_path, monkeypatch):
    """The ceiling counts what is *in view*, so zooming in is the way back to labels."""
    from pyql3.gui.tools import plot_catalog as module

    monkeypatch.setattr(module, "CATALOG_LABEL_LIMIT", 5)
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(_labelled_catalog(tmp_path, 20))
        dlg.chk_show_name.setChecked(True)
        dlg.update_visible_text_labels()
        assert dlg._label_guard.suppressing

        # sources sit every 2 px along the diagonal, so this view holds three of them
        viewer.imv.getView().setRange(xRange=(0, 5), yRange=(0, 5), padding=0)
        dlg.update_visible_text_labels()

        assert not dlg._label_guard.suppressing
        assert any(txt.isVisible() for txt in dlg.text_items), "labels never came back"
    finally:
        dlg.close()


def test_a_label_just_off_the_edge_is_still_drawn(viewer, tmp_path):
    """Culling on the exact visible rect makes edge text flicker as the user pans.

    `label_policy.grown_for_labels` grows the rect first, as the region overlay has always done;
    the catalog tool used a bare `rect.contains()`.
    """
    path = _labelled_catalog(tmp_path, 1, spread=0.0)   # one source, at (0, 0)
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(path)
        dlg.chk_show_name.setChecked(True)
        # A view starting just past the source: inside the grown rect, outside the exact one
        viewer.imv.getView().setRange(xRange=(1.0, 21.0), yRange=(1.0, 21.0), padding=0)
        dlg.update_visible_text_labels()

        assert any(txt.isVisible() for txt in dlg.text_items), (
            "a label just outside the view was culled, so it will flicker while panning")
    finally:
        dlg.close()


def test_the_label_pool_is_given_back_when_the_view_needs_far_fewer(viewer, tmp_path,
                                                                   monkeypatch):
    """Otherwise the pool sits at its high-water mark for the life of the dialog."""
    from pyql3.gui.tools import plot_catalog as module

    monkeypatch.setattr(module, "LABEL_POOL_MIN", 4)
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(_labelled_catalog(tmp_path, 60, spread=40.0))
        dlg.chk_show_name.setChecked(True)
        viewer.imv.getView().setRange(xRange=(0, 40), yRange=(0, 40), padding=0)
        dlg.update_visible_text_labels()
        crowded = len(dlg.text_items)
        assert crowded > 10, f"expected a crowded view, got {crowded} labels"

        viewer.imv.getView().setRange(xRange=(0, 1), yRange=(0, 1), padding=0)
        dlg.update_visible_text_labels()

        assert len(dlg.text_items) < crowded, "the pool was never given back"
    finally:
        dlg.close()


def test_closing_the_catalog_releases_its_retired_labels(viewer, tmp_path, monkeypatch):
    """Labels trimmed from the pool wait for a timer; closing must not leave them to it.

    A dialog replaced by `MainWindow._open_tool` is deleted, and its timer may never fire, so
    the labels would be freed by the collector in whatever order it reaches them (`BUGS.md` M31).
    """
    from pyql3.gui.tools import plot_catalog as module

    monkeypatch.setattr(module, "LABEL_POOL_MIN", 4)
    dlg = PlotCatalogDialog(None, viewer)
    dlg.load_catalog_file(_labelled_catalog(tmp_path, 60, spread=40.0))
    dlg.chk_show_name.setChecked(True)
    viewer.imv.getView().setRange(xRange=(0, 40), yRange=(0, 40), padding=0)
    dlg.update_visible_text_labels()
    viewer.imv.getView().setRange(xRange=(0, 1), yRange=(0, 1), padding=0)
    dlg.update_visible_text_labels()
    assert dlg._retired, "the pool trim retired nothing, so this checks nothing"

    dlg.close()

    assert dlg._retired == []


# --------------------------------------------------------------------------------------
# Markers follow the image: resolved once per geometry, re-placed when the image changes
# --------------------------------------------------------------------------------------


def _tan_wcs(ctype=('RA---TAN', 'DEC--TAN'), crval=(266.4, -29.0), crpix=(30, 20),
             cdelt=(-0.0001, 0.0001)):
    from astropy.wcs import WCS

    w = WCS(naxis=2)
    w.wcs.ctype = list(ctype)
    w.wcs.crval = list(crval)
    w.wcs.crpix = list(crpix)
    w.wcs.cdelt = list(cdelt)
    return w


def _scatter_points(dlg):
    xs, ys = dlg.scatter_item.getData()
    return [(float(x), float(y)) for x, y in zip(xs, ys, strict=True)]


def test_markers_follow_a_flip_and_a_new_image(qapp):
    """R8: markers kept the display coordinates they were drawn at when the image changed."""
    from astropy.table import Table

    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32))     # orig dims (60, 40)
    dlg = PlotCatalogDialog(None, viewer)
    dlg.show()
    try:
        dlg.set_catalog_table(Table({"name": ["a", "b"], "x": [5.0, 50.0], "y": [7.0, 30.0]}),
                              "t")
        assert _scatter_points(dlg) == [(5.5, 7.5), (50.5, 30.5)]
        dlg.table.selectRow(0)

        # Where the user has panned to must survive the redraw (R60 is about recentring)
        view = viewer.imv.getView()
        view.setRange(xRange=(20, 30), yRange=(20, 30), padding=0)
        before = view.viewRect()

        viewer.flip = True                               # what the Rotate tool's Flip does
        viewer.refresh_display()

        expected = [tuple(c + 0.5 for c in viewer.orig_to_display(x, y))
                    for x, y in ((5.0, 7.0), (50.0, 30.0))]
        assert _scatter_points(dlg) == pytest.approx(expected)
        hx, hy = dlg.highlight_item.getData()
        assert (float(hx[0]), float(hy[0])) == pytest.approx(expected[0]), \
            "the highlight stayed where it was drawn"
        assert view.viewRect() == before, "re-placing the markers moved the view"

        viewer.flip = False
        viewer.set_data(np.zeros((20, 20), np.float32))  # a new file; source b is off it
        assert _scatter_points(dlg) == [(5.5, 7.5)]
        assert "1 plotted | 1 out of bounds" in dlg.lbl_status.text()
    finally:
        dlg.close()


def test_markers_follow_a_view_rotation(qapp):
    """R8: the markers turn with the ImageItem; the labels sit on the ViewBox and stayed put."""
    from astropy.table import Table

    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32))
    dlg = PlotCatalogDialog(None, viewer)
    dlg.show()
    try:
        dlg.set_catalog_table(Table({"name": ["a"], "x": [5.0], "y": [7.0]}), "t")
        dlg.chk_show_name.setChecked(True)
        viewer.imv.getView().setRange(xRange=(-40, 100), yRange=(-40, 100), padding=0)
        dlg.update_visible_text_labels()

        viewer.apply_view_rotation(90.0)

        (mx, my), = _scatter_points(dlg)
        assert (mx, my) == (5.5, 7.5), "the marker is in ImageItem coordinates and stays there"
        shown = [txt for txt in dlg.text_items if txt.isVisible()]
        assert len(shown) == 1, "the label was dropped"
        pos = shown[0].pos()
        assert (pos.x(), pos.y()) == pytest.approx(viewer.item_to_view(mx, my)), \
            "the label did not follow the view rotation"
    finally:
        dlg.close()


def test_a_unit_change_does_not_re_place_the_catalog(qapp, monkeypatch):
    """A unit change emits `display_changed`, and the catalog re-placed every marker and label
    for it although nothing moved: 56 ms with a 66k-row catalog. A flip still re-places them."""
    from astropy.table import Table

    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32))
    dlg = PlotCatalogDialog(None, viewer)
    dlg.show()
    try:
        dlg.set_catalog_table(Table({"name": ["a"], "x": [5.0], "y": [7.0]}), "t")
        placed = []
        real = dlg._plot_markers
        monkeypatch.setattr(dlg, "_plot_markers", lambda: (placed.append(1), real())[1])

        viewer.disp_as_dn = True
        viewer.refresh_display()
        assert placed == [], "a unit change re-placed the markers"

        viewer.flip = True
        viewer.refresh_display()
        assert placed == [1], "a flip did not re-place the markers"
    finally:
        dlg.close()


def test_an_unchanged_view_rotation_re_places_no_label(qapp, monkeypatch):
    """`set_data` draws the new plane unrotated and then announces the rotation of 0.0; the
    catalog re-placed its labels for both, 45 ms per load with 3500 labels."""
    from astropy.table import Table

    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32))
    dlg = PlotCatalogDialog(None, viewer)
    dlg.show()
    try:
        dlg.set_catalog_table(Table({"name": ["a"], "x": [5.0], "y": [7.0]}), "t")
        dlg.chk_show_name.setChecked(True)
        viewer.apply_view_rotation(20.0)
        labelled = []
        real = dlg.update_visible_text_labels
        monkeypatch.setattr(dlg, "update_visible_text_labels",
                            lambda: (labelled.append(1), real())[1])

        viewer.view_rotation_changed.emit(20.0)
        assert labelled == [], "an unchanged angle re-placed the labels"

        viewer.set_data(np.ones((40, 60), np.float32))
        assert labelled == [1], f"a load re-placed the labels {len(labelled)} times"
        assert [t for t in dlg.text_items if t.isVisible()], "the label was lost"
    finally:
        dlg.close()


def test_catalog_given_before_any_image_plots_when_one_arrives(qapp):
    """R8: `--catalog` with no image, or `--poll-dir` on an empty directory."""
    from astropy.table import Table

    w = _tan_wcs()
    ra, dec = w.pixel_to_world_values(10.0, 25.0)
    viewer = ImageViewer()
    dlg = PlotCatalogDialog(None, viewer)
    dlg.show()
    try:
        dlg.set_catalog_table(Table({"name": ["s"], "ra": [float(ra)], "dec": [float(dec)]}),
                              "t")
        assert dlg.combo_coord_type.currentIndex() == 2

        viewer.set_data(np.zeros((40, 60), np.float32), w.to_header())

        (mx, my), = _scatter_points(dlg)
        assert (mx, my) == pytest.approx((10.5, 25.5), abs=0.01)
        assert "1 plotted" in dlg.lbl_status.text()
        assert "unusable" not in dlg.lbl_status.text()
    finally:
        dlg.close()


def test_world_catalog_refresh_is_not_per_row(qapp):
    """R31: each row built its own SkyCoord and made its own WCS call, about 0.14 ms a row, on
    every change of a marker control."""
    import time

    from astropy.table import Table

    w = _tan_wcs(crpix=(1000, 1000))
    viewer = ImageViewer()
    viewer.set_data(np.zeros((2000, 2000), np.float32), w.to_header())
    n = 20000
    x, y = np.random.default_rng(1).uniform(0, 2000, (2, n))
    ra, dec = w.pixel_to_world_values(x, y)

    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"id": np.arange(n), "ra": ra, "dec": dec}), "t")
        assert dlg.combo_coord_type.currentIndex() == 2

        start = time.perf_counter()
        dlg.spin_size.setValue(11)
        elapsed = time.perf_counter() - start
        assert elapsed < 0.5, f"one marker-size step took {elapsed:.2f} s"

        resolved = dlg._resolve_orig_positions()
        assert resolved.shape == (n, 2)
        for i in (0, 1, n // 2, n - 1):
            want = w.world_to_pixel_values(ra[i], dec[i])
            assert tuple(resolved[i]) == pytest.approx(tuple(float(v) for v in want), abs=1e-6)
    finally:
        dlg.close()


def test_world_catalog_on_a_dec_first_2d_image(qapp):
    """R61: a collapsed OSIRIS cube leaves Dec on FITS axis 1; RA was passed first anyway."""
    from astropy.table import Table

    w = _tan_wcs(ctype=('DEC--TAN', 'RA---TAN'), crval=(-29.0, 266.4),
                 cdelt=(0.0001, 0.0001))
    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32), w.to_header())
    dec, ra = w.pixel_to_world_values(10, 25)            # a star at 0-based pixel (10, 25)
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"name": ["s"], "ra": [float(ra)], "dec": [float(dec)]}),
                              "t")
        assert dlg.combo_coord_type.currentIndex() == 2
        (mx, my), = _scatter_points(dlg)
        assert (mx, my) == pytest.approx((10.5, 25.5), abs=0.01)
    finally:
        dlg.close()


def test_closing_the_catalog_retires_its_markers_and_labels(viewer, catalog_file, monkeypatch):
    """R86: tear_down dropped the scatter items and the label pool instead of retiring them,
    leaving the collector to free them in whatever order it walked their cycles (M18, M31)."""
    import shiboken6
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    dlg = _open_dialog(viewer, catalog_file)
    dlg.show()
    items = [dlg.scatter_item, dlg.highlight_item] + list(dlg.text_items)
    assert len(items) > 2, "no labels were drawn, so this checks nothing"

    released = []
    original = dlg._release_retired

    def spy():
        released.extend(dlg._retired)
        original()
    monkeypatch.setattr(dlg, "_release_retired", spy)

    QTest.keyClick(dlg, Qt.Key.Key_Escape)

    assert not dlg.isVisible()
    for item in items:
        assert any(item is gone for gone in released), f"{item!r} was not retired"
    assert not any(shiboken6.isValid(item) for item in items), "an item outlived the dialog"
    assert dlg._retired == []


def test_one_unreadable_sexagesimal_cell_costs_only_its_own_row():
    """`sky_degrees` reads all text rows in one SkyCoord; a bad cell must not lose the rest."""
    from astropy.table import MaskedColumn

    from pyql3.gui.tools.plot_catalog import sky_degrees

    ra = MaskedColumn(["17:45:40.0", "nonsense", "266.4", "17:45:40.0"],
                      mask=[False, False, False, True])
    dec = MaskedColumn(["-29:00:28", "-29:00:00", "-29.0", "-29:00:28"])

    got_ra, got_dec = sky_degrees(ra, dec)

    assert got_ra[0] == pytest.approx(266.41667, abs=1e-4)
    assert got_dec[0] == pytest.approx(-29.00778, abs=1e-4)
    assert np.isnan(got_ra[1]) and np.isnan(got_dec[1])
    assert (got_ra[2], got_dec[2]) == pytest.approx((266.4, -29.0)), "numbers are degrees"
    assert np.isnan(got_ra[3]), "a masked cell was given a position"


def test_labels_stay_hidden_with_show_all_off_after_a_pan(qapp):
    """R50: with Show All off, the next pan redrew the previous label list without markers."""
    from astropy.table import Table

    viewer = ImageViewer()
    viewer.show()
    viewer.set_data(np.zeros((40, 60), np.float32))
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"name": [f"s{i}" for i in range(10)],
                                     "x": [3.0 + 5 * i for i in range(10)],
                                     "y": [3.0 + 3 * i for i in range(10)]}), "t")
        dlg.chk_show_name.setChecked(True)
        assert sum(t.isVisible() for t in dlg.text_items) == 10

        dlg.chk_master_toggle.setChecked(False)
        dlg._on_view_range_changed()          # a pan frame
        dlg.update_visible_text_labels()      # what the redraw timer then calls

        assert len(dlg.scatter_item.getData()[0]) == 0
        assert not any(t.isVisible() for t in dlg.text_items), "labels came back after a pan"
    finally:
        dlg.close()
        viewer.close()


def test_float_ids_keep_their_digits_in_labels(viewer):
    """A float ID column (a pandas CSV with one missing ID is one) must not label 123456 and
    123457 both `1.2346e+05`. The table keeps its compact `.5g` cells."""
    from astropy.table import Table

    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"name": [123456.0, 123457.0, 266.41667],
                                     "x": [5.0, 10.0, 15.0], "y": [5.0, 10.0, 15.0]}), "t")
        dlg.combo_name.setCurrentText("name")
        dlg.chk_show_name.setChecked(True)

        labels = sorted(t.toPlainText() for t in dlg.text_items if t.isVisible())
        assert labels == ["123456", "123457", "266.41667"]
        assert dlg.table.model().index(0, 0).data() == "1.2346e+05"
    finally:
        dlg.close()


def test_selecting_a_row_after_unplaceable_rows_highlights_that_row(viewer):
    """Rows that cannot be placed are not plotted, so plotted index and row number differ;
    the highlight must still land on the selected row's own source."""
    from astropy.table import Table

    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"name": ["nan", "text", "good", "other"],
                                     "x": ["nan", "abc", "12.0", "30.0"],
                                     "y": ["4.0", "5.0", "21.0", "33.0"]}), "t")
        dlg.combo_coord_type.setCurrentIndex(1)
        dlg.combo_x.setCurrentText("x")
        dlg.combo_y.setCurrentText("y")
        assert "2 unusable" in dlg.lbl_status.text()

        dlg.table.selectRow(2)
        hx, hy = dlg.highlight_item.getData()
        assert (float(hx[0]), float(hy[0])) == (12.5, 21.5)
    finally:
        dlg.close()


def test_float32_labels_are_written_at_float32_precision(viewer):
    """FITS format E columns are float32; widening them to a double printed 0.1 as
    `0.10000000149011612`. A float64 column keeps its own digits."""
    from astropy.table import Table

    dlg = PlotCatalogDialog(None, viewer)
    try:
        for dtype, expected in ((np.float32, ["0.1", "266.41666"]),
                                (np.float64, ["0.1", "266.41667"])):
            dlg.set_catalog_table(Table({"name": np.array([0.1, 266.41667], dtype=dtype),
                                         "x": [5.0, 10.0], "y": [5.0, 10.0]}), "t")
            dlg.combo_name.setCurrentText("name")
            dlg.chk_show_name.setChecked(True)
            dlg.update_visible_text_labels()

            labels = [t.toPlainText() for t in dlg.text_items if t.isVisible()]
            assert labels == expected, dtype
    finally:
        dlg.close()


# ------------------------------------------- recentring: view coordinates, only when asked

def _two_source_dialog(loaded_viewer):
    """A catalog of two pixel-coordinate sources in a zoomed-in view."""
    from astropy.table import Table

    dialog = PlotCatalogDialog(None, loaded_viewer)
    dialog.set_catalog_table(Table({
        "name": ["a", "b"], "x": [5.0, 12.0], "y": [7.0, 9.0],
    }), "test")
    return dialog


def test_selecting_a_row_centres_it_under_a_view_rotation(qapp, loaded_viewer):
    """R29: the centre was passed to `setRange` in ImageItem coordinates, not view coordinates."""
    dialog = _two_source_dialog(loaded_viewer)
    try:
        view = loaded_viewer.imv.getView()
        view.setRange(xRange=(0, 6), yRange=(0, 6), padding=0)
        loaded_viewer.apply_view_rotation(90.0)

        dialog.table.selectRow(1)

        resolved = dialog._row_to_display(1)
        source = loaded_viewer.item_to_view(resolved[0] + 0.5, resolved[1] + 0.5)
        rect = view.viewRect()
        assert rect.contains(*source), "the selected source is off screen"
        assert (rect.center().x(), rect.center().y()) == pytest.approx(source, abs=0.01)
    finally:
        dialog.close()


def test_a_style_change_does_not_move_the_view(qapp, loaded_viewer):
    """R60: every catalog control called `update_plot`, which recentred on the selection."""
    dialog = _two_source_dialog(loaded_viewer)
    try:
        view = loaded_viewer.imv.getView()
        view.setRange(xRange=(0, 6), yRange=(0, 6), padding=0)
        dialog.table.selectRow(0)
        view.setRange(xRange=(8, 14), yRange=(4, 10), padding=0)
        before = view.viewRect().center()

        dialog.spin_size.setValue(dialog.spin_size.value() + 4)

        after = view.viewRect().center()
        assert (after.x(), after.y()) == pytest.approx((before.x(), before.y()))
        assert dialog.highlight_item.isVisible(), "the highlight must follow the new size"
    finally:
        dialog.close()


def test_center_on_source_recentres_the_view(qapp, loaded_viewer):
    """Recentring stays available on request, after the user has panned away."""
    dialog = _two_source_dialog(loaded_viewer)
    try:
        view = loaded_viewer.imv.getView()
        view.setRange(xRange=(0, 6), yRange=(0, 6), padding=0)
        dialog.table.selectRow(1)
        view.setRange(xRange=(0, 6), yRange=(0, 6), padding=0)

        menu = dialog.build_context_menu(1)
        next(a for a in menu.actions() if a.text() == "Center on Source").trigger()

        source = loaded_viewer.item_to_view(*(c + 0.5 for c in dialog._row_to_display(1)))
        centre = view.viewRect().center()
        assert (centre.x(), centre.y()) == pytest.approx(source, abs=0.01)
    finally:
        dialog.close()


def _sextractor_catalog(w):
    """One star at numpy [row 20, col 10], written the way SExtractor does: 1-based pixels."""
    from astropy.table import Table

    ra, dec = w.pixel_to_world_values(10, 20)
    return Table({"NUMBER": [1], "X_IMAGE": [11.0], "Y_IMAGE": [21.0],
                  "ALPHA_J2000": [float(ra)], "DELTA_J2000": [float(dec)]})


def _sextractor_viewer():
    w = _tan_wcs()
    data = np.zeros((40, 60), np.float32)
    data[20, 10] = 100.0
    viewer = ImageViewer()
    viewer.set_data(data, w.to_header())
    return viewer, w


def test_sextractor_image_columns_are_one_based(qapp):
    """R30: X_IMAGE/Y_IMAGE count the first pixel as 1, and were plotted as 0-based indices, one
    pixel off in both axes from the same row's ALPHA_J2000/DELTA_J2000."""
    viewer, w = _sextractor_viewer()
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(_sextractor_catalog(w), "sextractor")
        assert dlg.combo_coord_type.currentIndex() == 1
        assert dlg.combo_x.currentText() == "X_IMAGE"
        assert dlg.combo_pixel_origin.currentIndex() == 1, "SExtractor columns default to 1-based"
        (px, py), = _scatter_points(dlg)

        dlg.combo_coord_type.setCurrentIndex(2)
        (wx, wy), = _scatter_points(dlg)

        assert (px, py) == pytest.approx((wx, wy), abs=0.01)
        assert (px, py) == pytest.approx((10.5, 20.5), abs=0.01)
        assert viewer.display_data[int(px), int(py)] == 100.0
    finally:
        dlg.close()


def test_the_pixel_origin_is_part_of_what_positions_are_resolved_for(qapp):
    """Changing the origin has to re-resolve the positions, not return the other origin's."""
    viewer, w = _sextractor_viewer()
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(_sextractor_catalog(w), "sextractor")
        assert tuple(dlg._resolve_orig_positions()[0]) == (10.0, 20.0)

        dlg.combo_pixel_origin.setCurrentIndex(0)
        assert tuple(dlg._resolve_orig_positions()[0]) == (11.0, 21.0)
        assert _scatter_points(dlg) == [(11.5, 21.5)]

        dlg.combo_pixel_origin.setCurrentIndex(1)
        assert _scatter_points(dlg) == [(10.5, 20.5)]
    finally:
        dlg.close()


def test_the_pixel_origin_defaults_to_zero_based_for_other_columns(qapp):
    """Only the SExtractor `*_IMAGE` spellings are known to count from 1; the regions hand-off
    (`x`, `y`) and photutils (`xcentroid`) are array indices."""
    from astropy.table import Table

    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32))
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"name": ["a"], "x": [5.0], "y": [7.0]}), "regions")
        assert dlg.combo_pixel_origin.currentIndex() == 0
        assert _scatter_points(dlg) == [(5.5, 7.5)]

        dlg.set_catalog_table(Table({"id": [1], "xcentroid": [5.0], "ycentroid": [7.0]}), "phot")
        assert dlg.combo_pixel_origin.currentIndex() == 0
    finally:
        dlg.close()


def test_the_pixel_origin_is_offered_for_pixel_types_only(qapp):
    viewer, w = _sextractor_viewer()
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(_sextractor_catalog(w), "sextractor")
        for kind, shown in ((0, True), (1, True), (2, False)):
            dlg.combo_coord_type.setCurrentIndex(kind)
            assert dlg.combo_pixel_origin.isHidden() is (not shown)
            assert dlg.lbl_pixel_origin.isHidden() is (not shown)
    finally:
        dlg.close()


def test_a_pixel_origin_chosen_by_hand_survives_a_column_change_and_a_reload(qapp):
    viewer, w = _sextractor_viewer()
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(_sextractor_catalog(w), "sextractor")
        dlg.combo_pixel_origin.setCurrentIndex(0)

        dlg.combo_x.setCurrentText("NUMBER")
        dlg.combo_x.setCurrentText("X_IMAGE")
        assert dlg.combo_pixel_origin.currentIndex() == 0

        dlg.set_catalog_table(_sextractor_catalog(w), "sextractor again")
        assert dlg.combo_pixel_origin.currentIndex() == 0
    finally:
        dlg.close()


@pytest.mark.parametrize("ra_name, dec_name", [
    ("RAJ2000", "DEJ2000"),
    ("RA_ICRS", "DE_ICRS"),
    ("_RAJ2000", "_DEJ2000"),
    ("RAdeg", "DEdeg"),
])
def test_vizier_coordinate_names_select_world(qapp, ra_name, dec_name):
    """R32: none of VizieR's Dec spellings was recognised, so World was never chosen and a
    sequence number and the RA column were plotted as X and Y."""
    from astropy.table import Table

    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32))
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"Seq": [1, 2], ra_name: [266.40, 266.41],
                                     dec_name: [-29.00, -29.01], "Vmag": [12.0, 13.0]}), "vizier")
        assert dlg.combo_coord_type.currentIndex() == 2
        assert dlg.combo_x.currentText() == ra_name
        assert dlg.combo_y.currentText() == dec_name
    finally:
        dlg.close()


def _skycoord_catalog(tmp_path, with_pixels=True):
    """The shape of a photutils `SourceCatalog` table written by astropy: `sky_centroid` comes
    back as a `SkyCoord` column."""
    import astropy.units as u
    from astropy.coordinates import SkyCoord
    from astropy.table import QTable

    columns = {"label": [1, 2]}
    if with_pixels:
        columns.update(xcentroid=[10.0, 30.0], ycentroid=[20.0, 15.0])
    columns["sky_centroid"] = SkyCoord([266.40, 266.41] * u.deg, [-29.00, -29.01] * u.deg)
    columns["segment_flux"] = [1.0, 2.0]
    path = tmp_path / ("phot.fits" if with_pixels else "sky_only.fits")
    QTable(columns).write(path)
    return str(path)


def test_a_skycoord_column_does_not_break_the_column_guess(qapp, tmp_path, monkeypatch):
    """R62: `.dtype` was read on every column, and a `SkyCoord` has none; the exception left the X
    and Y selectors with their signals blocked, so choosing a column did nothing."""
    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32))
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(_skycoord_catalog(tmp_path))
        assert dlg.combo_coord_type.currentIndex() == 1
        assert dlg.combo_x.currentText() == "xcentroid"

        dlg.combo_coord_type.setCurrentIndex(2)           # World: no RA/Dec column to find
        assert not dlg.combo_x.signalsBlocked()
        assert not dlg.combo_y.signalsBlocked()
        assert dlg.combo_x.currentText() in ("label", "xcentroid", "ycentroid", "segment_flux")

        plotted = []
        real = dlg.update_plot
        monkeypatch.setattr(dlg, "update_plot", lambda: (plotted.append(1), real())[1])
        dlg.combo_x.setCurrentText("segment_flux")
        assert plotted, "picking a column did not redraw"

        # A table whose only position is the SkyCoord loads, with the selectors left usable
        dlg.load_catalog_file(_skycoord_catalog(tmp_path, with_pixels=False))
        assert "Error" not in dlg.lbl_file.text()
        assert not dlg.combo_x.signalsBlocked()
        assert not dlg.combo_y.signalsBlocked()
    finally:
        dlg.close()


def test_the_label_ceiling_message_survives_a_style_change(viewer, tmp_path, monkeypatch):
    """R75: the next `update_plot`, from a marker-size step for instance, wrote the plain status
    line over the message while the labels were still refused, and the guard says nothing again
    until its verdict changes."""
    from pyql3.gui.tools import plot_catalog as module

    monkeypatch.setattr(module, "CATALOG_LABEL_LIMIT", 3)
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.load_catalog_file(_labelled_catalog(tmp_path, 10))
        dlg.chk_show_name.setChecked(True)
        assert "10 labels in view" in dlg.lbl_status.text()

        dlg.spin_size.setValue(dlg.spin_size.value() + 2)

        assert dlg._label_guard.suppressing
        assert "10 labels in view" in dlg.lbl_status.text(), (
            "the plain status line replaced the explanation for the missing labels")
        assert "Zoom in" in dlg.lbl_status.text()

        # Once few enough are in view the plain line is back, with the marker counts
        viewer.imv.getView().setRange(xRange=(0, 5), yRange=(0, 5), padding=0)
        dlg.update_visible_text_labels()
        assert "labels in view" not in dlg.lbl_status.text()
        assert "plotted" in dlg.lbl_status.text()
    finally:
        dlg.close()


def _ceiling_dialog(viewer, monkeypatch):
    """A dialog whose ten labelled sources are refused by a ceiling of three."""
    from astropy.table import Table

    from pyql3.gui.tools import plot_catalog as module

    monkeypatch.setattr(module, "CATALOG_LABEL_LIMIT", 3)
    dlg = PlotCatalogDialog(None, viewer)
    xs = np.arange(10) * 3.0
    dlg.set_catalog_table(Table({"name": [f"S{i}" for i in range(10)], "x": xs, "y": xs,
                                 "far": xs + 1000.0}), "t")
    dlg.chk_show_name.setChecked(True)
    assert "10 labels in view" in dlg.lbl_status.text()
    return dlg


def test_the_ceiling_message_does_not_cover_a_catalog_with_nothing_on_the_image(
        viewer, monkeypatch):
    """R75 follow-up: with every row off the image no label is in view, so the guard has to be
    told; it kept refusing on the old count and its message replaced the out-of-bounds line."""
    dlg = _ceiling_dialog(viewer, monkeypatch)
    try:
        dlg.combo_x.setCurrentText("far")
        dlg.combo_y.setCurrentText("far")
        assert "out of bounds" in dlg.lbl_status.text()
        assert "labels in view" not in dlg.lbl_status.text()
        assert not dlg._label_guard.suppressing

        # And it comes back when the rows do
        dlg.combo_x.setCurrentText("x")
        dlg.combo_y.setCurrentText("y")
        assert "10 labels in view" in dlg.lbl_status.text()
    finally:
        dlg.close()


def test_the_ceiling_message_does_not_cover_unusable_world_coordinates(viewer, monkeypatch):
    """The same, for World on an image with no WCS: no row can be placed."""
    dlg = _ceiling_dialog(viewer, monkeypatch)
    try:
        dlg.combo_coord_type.setCurrentIndex(2)
        assert "unusable" in dlg.lbl_status.text()
        assert "labels in view" not in dlg.lbl_status.text()
    finally:
        dlg.close()


def test_unticking_labels_takes_the_ceiling_message_down(viewer, monkeypatch):
    dlg = _ceiling_dialog(viewer, monkeypatch)
    try:
        dlg.chk_show_name.setChecked(False)
        assert "labels in view" not in dlg.lbl_status.text()
        assert "plotted" in dlg.lbl_status.text()
    finally:
        dlg.close()


def test_a_hand_chosen_origin_does_not_reach_a_table_that_names_its_own(viewer):
    """The regions hand-off: `x`/`y` columns that are 0-based indices, handed to a dialog the user
    had set to 1-based for an earlier catalog with columns of the same names."""
    from astropy.table import Table

    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"name": ["a"], "x": [6.0], "y": [8.0]}), "daofind")
        dlg.combo_pixel_origin.setCurrentIndex(1)
        assert _scatter_points(dlg) == [(5.5, 7.5)]

        dlg.set_catalog_table(Table({"name": ["r"], "x": [5.0], "y": [7.0]}), "regions",
                              coord_type=1, pixel_origin=0)

        assert dlg.combo_coord_type.currentIndex() == 1
        assert dlg.combo_pixel_origin.currentIndex() == 0
        assert _scatter_points(dlg) == [(5.5, 7.5)]
        # The origin the caller set is not a choice by hand, so the guess may move it later
        assert dlg._origin_by_hand is False
    finally:
        dlg.close()


def test_the_coordinate_type_comes_from_the_caller_when_it_names_one(viewer):
    from astropy.table import Table

    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"name": ["a"], "x": [6.0], "y": [8.0]}), "t")
        dlg.combo_coord_type.setCurrentIndex(0)           # Display Pixels, by hand

        dlg.set_catalog_table(Table({"name": ["r"], "x": [5.0], "y": [7.0]}), "regions",
                              coord_type=1, pixel_origin=0)

        assert dlg.combo_coord_type.currentText() == "FITS Pixels"
        assert dlg.combo_x.currentText() == "x"
    finally:
        dlg.close()


def test_the_selectors_are_unblocked_when_the_column_guess_raises(qapp, monkeypatch):
    """R62, the try/finally half: any exception between the two `blockSignals` calls left the X
    and Y selectors dead."""
    from astropy.table import Table

    from pyql3.gui.tools import plot_catalog as module

    viewer = ImageViewer()
    viewer.set_data(np.zeros((40, 60), np.float32))
    dlg = PlotCatalogDialog(None, viewer)
    try:
        dlg.set_catalog_table(Table({"name": ["a"], "x": [6.0], "y": [8.0]}), "t")

        def boom(*args, **kwargs):
            raise RuntimeError("guess failed")

        monkeypatch.setattr(module, "best_coord_column", boom)
        with pytest.raises(RuntimeError):
            dlg.update_columns_for_type()

        assert not dlg.combo_x.signalsBlocked()
        assert not dlg.combo_y.signalsBlocked()
    finally:
        dlg.close()

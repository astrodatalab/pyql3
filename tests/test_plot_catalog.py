import numpy as np
import pyqtgraph as pg
import pytest
from astropy.io import fits

from pyql3.gui.tools.plot_catalog import (
    PlotCatalogDialog,
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

    assert scatter.scene() is None, "scatter item left in the scene"
    assert highlight.scene() is None, "highlight item left in the scene"
    for txt in texts:
        assert txt.scene() is None, "text label left in the scene"
    assert _marker_items(viewer) == []
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


def test_text_labels_are_replaced_not_leaked_on_refresh(viewer, catalog_file):
    """update_visible_text_labels re-renders on every pan; the old items must go."""
    dlg = _open_dialog(viewer, catalog_file)
    first = list(dlg.text_items)
    assert first

    dlg.update_visible_text_labels()

    for txt in first:
        assert txt.scene() is None, "previous label generation left in the scene"
    live = [it for it in _marker_items(viewer) if isinstance(it, pg.TextItem)]
    assert len(live) == len(dlg.text_items)
    dlg.close()


def test_removal_does_not_depend_on_garbage_collection(viewer, catalog_file):
    """B7, the actual defect: the scene must be clean *before* refcounting gets involved.

    Pre-fix, `_marker_items()` came back empty here too -- but only because dropping the
    dialog's last reference destroyed the C++ objects. Asserting emptiness while the items
    are still alive is what distinguishes explicit removal from accidental cleanup.
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

    # the objects are still alive, they are simply no longer in the scene
    assert all(item is not None for item in alive)
    assert all(item.scene() is None for item in alive)


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
    assert dlg.table.item(0, 0).text() == 'Src0'
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
        assert dialog.table.selectedItems() == []
        assert dialog.table.currentItem() is None, "the row still reads as current"
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

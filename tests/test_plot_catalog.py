import numpy as np
import pyqtgraph as pg
import pytest

from pyql3.gui.tools.plot_catalog import PlotCatalogDialog
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

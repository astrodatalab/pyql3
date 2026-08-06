"""The Region menu, the Region List dialog, and the file-format dispatch (Phase 4).

Three things are worth pinning here:

- **The dialog is a view, not a copy.** Editing a cell must reach the model region, and dragging
  the region on the image must come back to the table. Two stores of the same numbers drifting
  apart is the failure this design avoids.
- **Loading picks the format by content, not by suffix.** A ds9 file named `.yml` is an ordinary
  thing to be handed.
- **Nothing is lost in silence.** A ds9 export that cannot carry something has to say so, which
  is what the `Report` is for.

Menu actions are fired the way Qt fires them (`QAction.trigger()`), because that is the path that
delivers a `bool` to a one-argument slot and has caught real bugs here before (`BUGS.md` B0).
"""
from dataclasses import replace

import pytest
from PySide6.QtWidgets import QFileDialog, QInputDialog, QMessageBox

from pyql3.core.regions_io import load_regions, save_regions, suggested_filename, with_sky_anchors
from pyql3.core.regions_model import Arrow, Box, Circle, RegionFormatError, Text
from pyql3.gui.main_window import MainWindow
from pyql3.gui.tools.region_list import COL_ANGLE, COL_LABEL, COL_SIZE, COL_X, RegionListDialog


@pytest.fixture
def window(qapp, sample_3d_fits):
    win = MainWindow()
    win.load_fits(sample_3d_fits)
    yield win
    win.close()


@pytest.fixture
def some_regions():
    return [
        Circle(x=10.0, y=9.0, radius=3.0, text="src A", color="red"),
        Box(x=6.0, y=5.0, width=6.0, height=4.0, angle=20.0),
        Arrow(x=2.0, y=2.0, length=8.0, angle=45.0, text="outflow"),
        Text(x=14.0, y=14.0, text="knot", angle=30.0),
    ]


def menu_action(window, menu_attribute, action_text):
    """The QAction behind a menu entry, so tests fire the real thing.

    Reached through the menu `MainWindow` stores on itself rather than by walking
    `menuBar().actions()` and calling `.menu()`. That call makes the Python wrapper a transient
    owner of the QMenu, and PySide6 destroys the C++ menu — and every QAction in it — when the
    wrapper is collected, so the action is already dead by the time it is returned
    (`BUGS.md` M11).
    """
    for entry in getattr(window, menu_attribute).actions():
        if entry.text() == action_text:
            return entry
    raise AssertionError(f"no {action_text!r} in {menu_attribute}")


# ------------------------------------------------------------------ the menu

def test_the_region_menu_exists_with_its_entries(window):
    titles = [action.text() for action in window.menuBar().actions()]
    assert "Region" in titles

    entries = [entry.text() for entry in window.region_menu.actions()]
    for expected in ("New Circle", "New Box", "New Arrow", "New Text...", "Region List...",
                     "Load Regions...", "Save Regions As...", "Export ds9 Regions...",
                     "Delete All Regions"):
        assert expected in entries, entries


@pytest.mark.parametrize("label,kind", [("New Circle", "circle"), ("New Box", "box"),
                                        ("New Arrow", "arrow")])
def test_new_shape_actions_enter_drawing_mode(window, label, kind):
    menu_action(window, "region_menu", label).trigger()

    assert window.region_layer.drawing
    assert window.image_viewer.exclusive_drag_owner() is window.region_layer
    window.region_layer.cancel_draw()


def test_new_text_asks_for_a_label_first(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("knot", True)))

    menu_action(window, "region_menu", "New Text...").trigger()
    assert window.region_layer.drawing

    region = window.region_layer.place_at(5.5, 5.5)
    assert isinstance(region, Text)
    assert region.text == "knot"


def test_cancelling_the_text_prompt_draws_nothing(window, monkeypatch):
    monkeypatch.setattr(QInputDialog, "getText", staticmethod(lambda *a, **k: ("", False)))

    menu_action(window, "region_menu", "New Text...").trigger()

    assert not window.region_layer.drawing
    assert len(window.region_layer) == 0


def test_drawing_without_data_explains_itself(qapp, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information",
                        staticmethod(lambda *a, **k: shown.append(a)))
    win = MainWindow()
    try:
        menu_action(win, "region_menu", "New Circle").trigger()
        assert shown, "no explanation offered for drawing with nothing loaded"
        assert not win.region_layer.drawing
    finally:
        win.close()


def test_delete_all_asks_first_and_obeys_no(window, some_regions, monkeypatch):
    window.region_layer.set_regions(some_regions)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.No))

    menu_action(window, "region_menu", "Delete All Regions").trigger()

    assert len(window.region_layer) == len(some_regions), "deleted despite a refusal"


def test_delete_all_obeys_yes(window, some_regions, monkeypatch):
    window.region_layer.set_regions(some_regions)
    monkeypatch.setattr(QMessageBox, "question",
                        staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes))

    menu_action(window, "region_menu", "Delete All Regions").trigger()

    assert len(window.region_layer) == 0


def test_the_region_list_opens_and_is_tracked_for_teardown(window):
    menu_action(window, "region_menu", "Region List...").trigger()

    assert window._region_list_dialog.isVisible()
    # Listed in TOOL_DIALOG_ATTRS, so closing the window closes it too.
    assert '_region_list_dialog' in MainWindow.TOOL_DIALOG_ATTRS
    window.close()
    assert not window._region_list_dialog.isVisible()


# ------------------------------------------------------------- save and load

def test_saving_and_loading_a_round_trip_through_the_menu(window, some_regions, tmp_path,
                                                          monkeypatch):
    path = tmp_path / "regions.yml"
    window.region_layer.set_regions(some_regions)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))

    menu_action(window, "region_menu", "Save Regions As...").trigger()
    assert path.exists()

    window.region_layer.clear()
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))
    menu_action(window, "region_menu", "Load Regions...").trigger()

    restored = window.region_layer.regions
    assert len(restored) == len(some_regions)
    for before, after in zip(some_regions, restored, strict=True):
        # The file gains a sky anchor on the way out — that is the point of saving — so the
        # comparison is of everything else.
        assert after.sky is not None, f"{after.TYPE} lost its sky anchor"
        assert replace(after, sky=None) == before


def test_exporting_ds9_forces_the_reg_suffix(window, some_regions, tmp_path, monkeypatch):
    """The writer picks its format from the suffix, so the name has to match what was asked for."""
    chosen = tmp_path / "no_suffix"
    window.region_layer.set_regions(some_regions)
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(chosen), "")))
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: None))

    menu_action(window, "region_menu", "Export ds9 Regions...").trigger()

    assert (tmp_path / "no_suffix.reg").exists()
    assert "# Region file format" in (tmp_path / "no_suffix.reg").read_text()


def test_saving_nothing_says_so_rather_than_writing_an_empty_file(window, tmp_path, monkeypatch):
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a)))
    called = []
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: called.append(True) or ("", "")))

    menu_action(window, "region_menu", "Save Regions As...").trigger()

    assert shown, "no message for saving an empty region list"
    assert not called, "a file dialog was opened for nothing to save"


def test_a_broken_region_file_is_reported_not_raised(window, tmp_path, monkeypatch):
    bad = tmp_path / "broken.yml"
    bad.write_text("format: pyql3-regions/1\nregions:\n  - type: circle\n    x: 1\n")
    warned = []
    monkeypatch.setattr(QMessageBox, "warning", staticmethod(lambda *a, **k: warned.append(a)))
    monkeypatch.setattr(QFileDialog, "getOpenFileName",
                        staticmethod(lambda *a, **k: (str(bad), "")))

    menu_action(window, "region_menu", "Load Regions...").trigger()

    assert warned, "a malformed file passed without a word"
    assert "radius" in str(warned[0]), warned[0]


def test_a_ds9_export_that_loses_something_says_so(window, tmp_path, monkeypatch):
    """An OSIRIS cube is displayed on FITS axes 3 and 2, which ds9's image frame cannot mean."""
    path = tmp_path / "out.reg"
    window.region_layer.set_regions([Circle(x=10.0, y=9.0, radius=3.0)])
    shown = []
    monkeypatch.setattr(QMessageBox, "information", staticmethod(lambda *a, **k: shown.append(a)))
    monkeypatch.setattr(QFileDialog, "getSaveFileName",
                        staticmethod(lambda *a, **k: (str(path), "")))

    menu_action(window, "region_menu", "Export ds9 Regions...").trigger()

    assert path.exists()
    assert shown, "the conversion note was not surfaced"
    assert "sky coordinates" in str(shown[0])


# ------------------------------------------------------------ format dispatch

def test_the_format_is_chosen_by_content_not_by_suffix(tmp_path):
    """A ds9 file named .yml is an ordinary thing to be handed."""
    misnamed = tmp_path / "actually_ds9.yml"
    misnamed.write_text("# Region file format: DS9 version 4.1\nimage\ncircle(11,21,5)\n")

    region_list, report = load_regions(misnamed)

    circle, = region_list.regions
    assert (circle.x, circle.y) == (10.0, 20.0)      # ds9 counts pixels from 1
    assert report is not None, "a ds9 conversion always has a report, even an empty one"


def test_a_native_file_reports_nothing(tmp_path, some_regions):
    path = tmp_path / "regions.yml"
    save_regions(path, some_regions)

    region_list, report = load_regions(path)

    assert region_list.regions == some_regions
    assert report is None, "the native format has nothing to lose and so nothing to report"


def test_saving_a_reg_suffix_writes_ds9_format(tmp_path, some_regions):
    path = tmp_path / "regions.reg"
    report = save_regions(path, some_regions)

    assert "# Region file format" in path.read_text()
    assert report is not None


def test_an_unreadable_file_raises_os_error(tmp_path):
    with pytest.raises(OSError):
        load_regions(tmp_path / "nope.yml")


def test_a_malformed_native_file_raises_a_region_format_error(tmp_path):
    path = tmp_path / "bad.yml"
    path.write_text("format: pyql3-regions/1\nregions: {}\n")
    with pytest.raises(RegionFormatError):
        load_regions(path)


def test_suggested_filenames_sit_beside_the_cube():
    assert suggested_filename("/data/s150531_a025002.fits", ".yml") == \
        "s150531_a025002_regions.yml"
    assert suggested_filename("", ".reg") == "regions.reg"


# ---------------------------------------------------------------- sky anchors

def test_saving_records_the_sky_position_alongside_the_pixels(tmp_path, loaded_viewer):
    """The anchoring decision: pixels stay authoritative, the sky position rides along."""
    path = tmp_path / "regions.yml"
    original = Circle(x=10.0, y=9.0, radius=3.0)

    save_regions(path, [original], wcs=loaded_viewer.wcs,
                 axis_indices=loaded_viewer.display_axis_indices())

    saved, _ = load_regions(path)
    circle, = saved.regions
    assert (circle.x, circle.y) == (10.0, 9.0), "pixels must stay authoritative"
    assert circle.sky is not None, "no sky anchor was recorded"
    assert circle.sky.size_arcsec == pytest.approx(3.0 * 0.36, rel=1e-3)
    # ...and the region handed in was not modified on the way past.
    assert original.sky is None


def test_sky_anchors_are_skipped_when_the_plane_has_no_sky(loaded_viewer):
    """Wavelength against declination is a fine thing to display and has no sky position."""
    anchored = with_sky_anchors([Circle(x=1.0, y=2.0, radius=3.0)], wcs=loaded_viewer.wcs,
                               axis_indices=(0, 1))
    assert anchored[0].sky is None


def test_an_existing_sky_anchor_is_left_alone(loaded_viewer):
    from pyql3.core.regions_model import SkyAnchor

    kept = SkyAnchor(ra_deg=1.0, dec_deg=2.0)
    anchored = with_sky_anchors([Circle(x=1.0, y=2.0, radius=3.0, sky=kept)],
                                wcs=loaded_viewer.wcs,
                                axis_indices=loaded_viewer.display_axis_indices())
    assert anchored[0].sky is kept


# ------------------------------------------------------------ the list dialog

@pytest.fixture
def dialog(window, some_regions):
    window.region_layer.set_regions(some_regions)
    dlg = RegionListDialog(window, window.image_viewer)
    yield dlg
    dlg.close()


def test_the_table_lists_every_region(dialog, some_regions):
    assert dialog.table.rowCount() == len(some_regions)
    assert [dialog.table.item(row, 0).text() for row in range(dialog.table.rowCount())] == \
        [region.TYPE for region in some_regions]


def test_the_table_follows_the_layer(dialog, window):
    before = dialog.table.rowCount()
    window.region_layer.add(Circle(x=1.0, y=1.0, radius=1.0))
    assert dialog.table.rowCount() == before + 1

    window.region_layer.clear()
    assert dialog.table.rowCount() == 0


def test_editing_a_coordinate_moves_the_region(dialog, window):
    region = window.region_layer.regions[0]
    dialog.table.item(0, COL_X).setText("7.5")

    assert region.x == pytest.approx(7.5)
    item = window.region_layer.item_for(region)
    assert item is not None


def test_editing_a_size_resizes_the_region(dialog, window):
    circle = window.region_layer.regions[0]
    dialog.table.item(0, COL_SIZE).setText("6")

    assert circle.radius == pytest.approx(6.0)


def test_editing_an_angle_rotates_the_region(dialog, window):
    box = window.region_layer.regions[1]
    dialog.table.item(1, COL_ANGLE).setText("70")

    assert box.angle == pytest.approx(70.0)


def test_a_non_numeric_edit_is_refused_and_explained(dialog, window):
    region = window.region_layer.regions[0]
    original = region.x

    dialog.table.item(0, COL_X).setText("over there")

    assert region.x == original
    assert "not a number" in dialog.lbl_status.text()
    assert dialog.table.item(0, COL_X).text() == f"{original:.2f}", "the cell was not reverted"


def test_a_text_region_cannot_be_left_without_a_label(dialog, window):
    label_row = next(row for row, region in enumerate(window.region_layer.regions)
                     if isinstance(region, Text))
    region = window.region_layer.regions[label_row]

    dialog.table.item(label_row, COL_LABEL).setText("")

    assert region.text == "knot"
    assert "needs a label" in dialog.lbl_status.text()


def test_editing_a_label_reaches_the_region(dialog, window):
    region = window.region_layer.regions[0]
    dialog.table.item(0, COL_LABEL).setText("renamed")
    assert region.text == "renamed"


def test_deleting_from_the_table_removes_the_region(dialog, window):
    first = window.region_layer.regions[0]
    dialog.table.selectRow(0)

    dialog.delete_selected()

    assert first not in window.region_layer.regions
    assert len(window.region_layer) == 3


def test_delete_all_from_the_dialog(dialog, window):
    dialog.delete_all()
    assert len(window.region_layer) == 0


def test_zooming_to_a_region_centres_the_view(dialog, window):
    region = window.region_layer.regions[0]
    dialog.zoom_to(region)

    rect = window.image_viewer.imv.getView().viewRect()
    where = window.region_layer._to_item(region.x, region.y)
    assert rect.center().x() == pytest.approx(where[0], abs=1.0)
    assert rect.center().y() == pytest.approx(where[1], abs=1.0)


def test_copying_coordinates_includes_the_sky_position(dialog, window, qapp):
    region = window.region_layer.regions[0]
    dialog.copy_coordinates(region)

    text = qapp.clipboard().text()
    assert "x=10.000" in text and "y=9.000" in text
    assert "RA=" in text and "Dec=" in text
    assert "src A" in text


def test_toggling_visibility_from_the_table(dialog, window):
    region = window.region_layer.regions[0]
    dialog._set_visible(region, False)

    assert region.visible is False
    assert not window.region_layer.item_for(region).isVisible()


def test_dragging_on_the_image_updates_the_table(dialog, window):
    """The other direction: the table is a view of the layer, not a second copy."""
    region = window.region_layer.regions[0]
    item = window.region_layer.item_for(region)

    item.setPos([item.pos()[0] + 3.0, item.pos()[1]])

    assert dialog.table.item(0, COL_X).text() == f"{region.x:.2f}"


def test_properties_can_be_opened_from_the_table(dialog, window, monkeypatch):
    region = window.region_layer.regions[0]
    dialog.open_properties(region)

    editor = window._region_property_dialogs[id(region)]
    try:
        assert editor.isVisible()
        assert editor.region is region
    finally:
        editor.close()


def test_the_dialog_survives_a_viewer_with_no_regions(window):
    dlg = RegionListDialog(window, window.image_viewer)
    try:
        assert dlg.table.rowCount() == 0
        assert "No regions" in dlg.lbl_status.text()
    finally:
        dlg.close()


# ------------------------------------------------- large sets (Phase 5)

def test_the_table_declines_to_list_an_unmanageable_number(window, monkeypatch):
    """A QTableWidget cell per field is 16 s at 10,000 regions, on every drag frame."""
    from pyql3.gui.tools import region_list as module

    monkeypatch.setattr(module, "LIST_LIMIT", 5)
    window.region_layer.interactive_limit = 3
    window.region_layer.set_regions([Circle(x=float(i), y=1.0, radius=1.0) for i in range(12)])

    dialog = RegionListDialog(window, window.image_viewer)
    try:
        assert dialog.table.rowCount() == 0, "the table was filled in anyway"
        text = dialog.lbl_status.text()
        assert "too many to list" in text
        assert "12" in text, "the count has to be stated, not hidden"
        assert "still drawn, saved and exported" in text
    finally:
        dialog.close()


def test_deleting_a_selection_redraws_once(window, monkeypatch):
    """Removing one at a time would rebuild the aggregate overlay per region."""
    layer = window.region_layer
    layer.interactive_limit = 3
    regions = [Circle(x=float(i), y=1.0, radius=1.0) for i in range(10)]
    layer.set_regions(regions)

    draws = []
    original = layer._draw_bulk
    monkeypatch.setattr(layer, '_draw_bulk', lambda: (draws.append(True), original()))

    assert layer.remove_many(regions[:4]) == 4

    assert len(layer) == 6
    assert len(draws) <= 1, f"redrew the overlay {len(draws)} times for one deletion"


def test_the_window_says_when_regions_stop_being_draggable(window):
    window.region_layer.interactive_limit = 3
    window.region_layer.set_regions([Circle(x=float(i), y=1.0, radius=1.0) for i in range(10)])

    message = window.statusBar().currentMessage()
    assert "drawn as a fixed overlay" in message, message
    assert "Region List" in message, "the message should say where editing still works"


def test_a_big_region_file_loads_without_per_region_items(window, tmp_path):
    """End to end: a catalogue-sized file loads, is all there, and is drawn as an overlay."""
    path = tmp_path / "many.yml"
    save_regions(path, [Circle(x=float(i % 30), y=float(i // 30), radius=1.0)
                        for i in range(1200)])

    assert window.load_regions_from(path, announce=False) is True

    layer = window.region_layer
    assert len(layer) == 1200
    assert layer.bulk is True
    assert len(layer._bulk_items) <= 2, "should be a handful of items, not 1,200"


# ----------------------------------------------------------- the --regions flag

def test_load_regions_from_reports_a_bad_file_without_a_dialog(window, tmp_path, capsys):
    """`--regions` is typed in a terminal, so a startup failure belongs on stderr, not in a modal."""
    bad = tmp_path / "bad.yml"
    bad.write_text("format: pyql3-regions/1\nregions: {}\n")

    assert window.load_regions_from(bad, announce=False) is False
    assert "could not load regions" in capsys.readouterr().err


def test_the_regions_flag_is_documented_by_the_cli():
    """Checked through the real command line, which is the surface a user meets."""
    import subprocess
    import sys

    result = subprocess.run([sys.executable, "main.py", "--help"], capture_output=True, text=True,
                            timeout=120)
    assert result.returncode == 0, result.stderr
    assert "--regions" in result.stdout
    assert "ds9" in result.stdout, "the help should say both formats are accepted"


def test_every_command_line_path_expands_a_home_directory():
    """M6: `--catalog` was the one path that skipped `expanduser`, so `~/cat.csv` was ignored."""
    import pathlib as _pathlib

    source = _pathlib.Path("main.py").read_text()
    for flag in ("poll_dir", "catalog", "regions"):
        assert f"os.path.expanduser(args.{flag})" in source, f"--{flag} does not expand ~"

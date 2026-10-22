# Code review 2026-10-21 fixes — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fix R1–R89 from the 2026-10-21 code review, and add the shared helpers, signals and fixtures whose absence caused most of them.

**Architecture:** Five shared changes carry most of the fixes, and they come first: one integration-time and WCS helper module (`pyql3/core/header_info.py`); one whole-pixel box helper in `coords.py`; one redisplay path and two new viewer signals (`plane_changed`, `view_rotation_changed`) in `ImageViewer`; one tool lifecycle (`BaseToolDialog.tear_down()` and `MainWindow._open_tool()`); and frame-aware direction mapping on `CelestialMap`. Everything after Phase 4 is a local fix in one file or one area, built on those.

**Tech Stack:** PySide6, pyqtgraph, astropy (WCS, SkyCoord), photutils, `regions` 0.12, pytest + pytest-xdist. Everything runs through `uv run`.

**Spec:** `BUGS.md`, section "Code review — 2026-10-21" (R1–R89; R80 was added in the Phase 1 review, R81–R83 from a NIFS reference cube, R84 in the Phase 2 review, R85 in Task 9, R86 and R87 in the Phase 3 review, R88 in the Task 20 review). Each task names the entries it closes. The entry holds the reproduction, the measured numbers and the proposed regression test; read it before starting the task. Where a task below differs from an entry's "Fix (proposed)", the task wins and says why.

## Global Constraints

- Every Python invocation goes through `uv run`. Run tests with `QT_QPA_PLATFORM=offscreen`.
- Work on one branch, `code-review-2026-10-21`, created from `main` before Task 1.
- **Stage, do not commit.** Each task ends with `git add` of the files it touched. At the end of each phase, stop and report `git status --short` and a diffstat grouped by task, so the user can commit; do not start the next phase until the user says to. No `git commit` unless the user asks for that specific commit. No AI attribution in anything the user may paste into a commit.
- **Close-out, at the end of every task:** (1) `QT_QPA_PLATFORM=offscreen uv run pytest -q -n auto --dist loadfile` passes, with no new failures or skips; (2) in `BUGS.md`, change each closed entry's Status to `✅ FIXED — <files>, covered by <test names>` (for a table row, prefix the Issue cell with `✅ FIXED —`) and tick it in "Working checklist — code review 2026-10-21"; (3) `git add` the task's files.
- Write each test first and see it fail for the reason the entry describes before changing the code. The test names below are binding; the BUGS.md entries supply the measured values to assert.
- AGENTS.md invariants hold throughout: analysis and exports read `raw_data` or `current_plane()`, never a channel of `display_data` picked with `imv.currentIndex`; coordinate arithmetic lives in `pyql3/core/coords.py`; no axis index is hardcoded; a `QGraphicsItem` is retired, never dropped; context menus are tested through `build_*_menu()`; any test that can reach `QMessageBox`, `QFileDialog`, `QDialog.exec` or `choose_region_frame` stubs it first.
- When a fix changes a fact AGENTS.md or `docs/` states, change that text in the same task. No absolute paths in committed files.
- New Qt-free logic goes in `pyql3/core/` with tests that need no display.

## Review Focus

1. **Slider drags with every tool open.** `plane_changed` fires on each slider step and every visible tool recomputes. A 465-channel cube with the Depth Plot (Depth mode), Statistics, Photometry and a 66k-row RA/Dec catalog open must not re-place catalog markers or recompute the depth spectrum per step. Test in Task 11.
2. **View rotation combined with flip and rot90 in collapse mode.** Readout, regions and catalog must agree on one pixel. Task 8 parametrizes the readout test over flip × rot90 × view rotation.
3. **A real OSIRIS cube.** `real_osiris_fits` must give `data_multiplier == TRUITIME * COADDS` and an FK5 `CelestialMap.frame`. Tests in Tasks 1 and 35; they skip without `PYQL3_TEST_CUBE`.
4. **Save As over the file that is open, on Windows.** Task 3's test saves onto the open file and reloads it; it runs in the Windows release job.
5. **An auto-load arriving while the header editor is open, then a second one.** The newest deferred path is loaded once after the dialog closes. Test in Task 5.

## Decisions taken in this plan (the user may overturn them before execution)

- **S7 vs R12 (Task 17).** A file with no image data no longer replaces the window's reader. The window keeps showing, and editing the header of, the file it displays. This drops S7's "the data-less file's header can still be opened in the Header Editor".
- **R26 (Task 20).** Choosing the axis the other combo already shows swaps the two, rather than reverting the user's choice.
- **R36 (Task 15).** In Collapse Region mode a region with a channel range is visible when its range overlaps the collapse range.
- **R68 (Task 16).** The Rotate dialog is refreshed after a reload; North Up is **not** kept across frames (that would be a feature, not a fix).
- **R16 (Task 10).** A hidden tool dialog replaced by a new one is torn down and `deleteLater()`d.
- **R59 (Task 41).** Several files from one Finder open-document batch open one window each, as several command-line files do.
- **R81 (Task 20), the user's decision.** A cube that is not from OSIRIS and has no celestial axis types is taken to be `[x, y, wavelength]` (FITS axes 1, 2, 3).
- **R82, R83 (Tasks 20, 44), the user's decision.** A unit the file does not state is labelled "Unknown" and never inferred — for the data values and for the wavelength axis. The line list is unavailable on a wavelength axis of unknown unit until the user chooses a unit for it (Task 23); a chosen unit is labelled "set by you".
- **R83 (Task 44).** Total DN is offered only when `BUNIT` declares a per-second rate; a cube with no `BUNIT` (NIFS) does not get it, even though its numbers may well be counts.

## Not in this plan

The open entries from earlier reviews (B8, B9, B11, B13, B14, B19, M2, M3, M5–M10) stay open, except M1 (closed by Task 9, which merges Photometry's two `closeEvent`s into `tear_down`) and M4 (closed by Task 25).

---

## Phase 1 — Wrong numbers and lost data

### Task 1: Integration time from TRUITIME, and a realistic OSIRIS fixture (R1)

**Files:**
- Create: `pyql3/core/header_info.py`, `tests/test_header_info.py`
- Modify: `pyql3/gui/viewers/image_viewer.py:1082-1086`, `tests/conftest.py`, `AGENTS.md` (the `data_multiplier` sentence)
- Test: `tests/test_header_info.py`, `tests/test_image_viewer.py`

**Interfaces:**
- Produces: `header_info.integration_seconds(header) -> float` — `TRUITIME` when present, finite and > 0; else `ITIME` when finite and > 0; else 1.0. `header_info.total_dn_multiplier(header) -> float` — `integration_seconds(header) * COADDS` (COADDS defaults to 1; a non-finite or ≤ 0 COADDS counts as 1).
- Produces: fixture `osiris_like_fits(tmp_path) -> str`. A primary-HDU cube with NAXIS1 = 40 (`WAVE`, `CUNIT1 = 'nm'`, CRVAL1 2000, CDELT1 0.25), NAXIS2 = 51 (`DEC--TAN`), NAXIS3 = 66 (`RA---TAN`), 0.035″ pixels, a PC matrix rotating the sky axes by 35°, `RADESYS = 'FK5'`, `EQUINOX = 2000.0`, `CURRINST = 'OSIRIS'`, `ITIME = 900000`, `TRUITIME = 899.92`, `COADDS = 1`, `BUNIT = 'ADU per second per coadd'`. Data value at numpy index `[i, j, k]` is `10000*i + 100*j + k`, so every voxel identifies itself. Later tasks use this fixture for every non-square, FK5 or rotated-field case.

- [ ] **Step 1: Write the failing tests.** `test_header_info.py::test_truitime_wins_over_millisecond_itime` (899.92), `::test_itime_alone_is_seconds` (`ITIME = 10` → 10.0), `::test_zero_itime_falls_back_to_truitime`, `::test_no_exposure_keywords_means_one`, `::test_coadds_multiply`. `test_image_viewer.py::test_total_dn_uses_truitime_when_itime_is_milliseconds`: load `osiris_like_fits`, set `disp_as_dn = True`, assert `viewer.data_multiplier == pytest.approx(899.92)`. `::test_real_osiris_cube_total_dn_uses_truitime(real_osiris_fits)`: skips when the fixture is None; else asserts `data_multiplier == pytest.approx(header['TRUITIME'] * header.get('COADDS', 1))`.
- [ ] **Step 2: Run them and see them fail** (`ImportError` for the module; 900000 for the viewer).
- [ ] **Step 3: Implement** `header_info.py` and replace `image_viewer.py:1082-1086` with `self._itime_coadds = total_dn_multiplier(header)`. Update the AGENTS.md sentence "exposed as the `data_multiplier` property (`itime * coadds`)" to name `header_info.total_dn_multiplier` and the TRUITIME rule.
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R1.

### Task 2: Apertures measured where they are drawn (R2)

**Files:**
- Modify: `pyql3/gui/tools/depth_plot.py` (`aperture_geometry` `:729-741`; `set_center`; `sync_annulus_rings`; `on_radius_changed` `:749-770`; default centre `:448-458`), `pyql3/gui/tools/photometry.py:113-116`, `pyql3/gui/tools/strehl.py:76-79`
- Test: `tests/test_depth_plot.py`, `tests/test_analysis_tools.py`

**Interfaces:**
- Consumes: `coords.item_to_index`, `coords.index_to_item`.
- Produces: `DepthPlotDialog.aperture_geometry()` now returns the centre as a fractional **numpy index** `(cx, cy, r)`. `set_center((x, y))` takes a numpy index ("this pixel's centre") and places the ROI at `index_to_item`.

- [ ] **Step 1: Write the failing tests.** `test_depth_plot.py::test_a_circle_drawn_on_a_pixel_measures_that_pixel`: a cube that is zero except one 100-count pixel at index (12, 9) in every channel; put the r = 1 `CircleROI` with its ImageItem centre at (12.5, 9.5); assert the Total spectrum is 100 to 1e-9 in every channel. `test_analysis_tools.py::test_photometry_circle_drawn_on_a_pixel_measures_that_pixel`: the same on a 2-D image through `PhotometryDialog`, asserting raw flux 100. `::test_strehl_aperture_is_centred_on_the_drawn_circle`: assert the centre passed to `calculate_strehl` (monkeypatch it to record its arguments) equals the drawn ImageItem centre minus 0.5 on each axis.
- [ ] **Step 2: Run them and see them fail** (78.54 and 78.5; centre off by 0.5).
- [ ] **Step 3: Implement.** Convert once in each tool, with `item_to_index` when an ROI centre becomes a measurement centre and `index_to_item` when an index becomes an ROI position. The right-click centre (`image_viewer.py:1606-1610`, an integer index) then lands on the pixel's centre.
- [ ] **Step 4: Run** the new tests, `tests/test_depth_plot.py` and `tests/test_spectral_photometry.py`; they pass. The existing Depth Plot / Photometry cross-check must still agree.
- [ ] **Step 5: Close-out** for R2.

### Task 3: Save FITS As keeps every HDU (R3)

**Files:**
- Modify: `pyql3/gui/main_window.py:700-717`
- Test: `tests/test_data_integrity.py`

**Interfaces:**
- Consumes: `FitsReader.save(output_filepath)` (`fits_reader.py:209`), `poller.is_fits_path(path)`.

- [ ] **Step 1: Write the failing tests.** `test_data_integrity.py::test_save_as_keeps_every_extension_and_the_mode`: a PRIMARY (no data, `TELESCOP='Keck II'`) + SCI + ERR + DQ file at mode 0o664; `QFileDialog.getSaveFileName` stubbed to return a new path; assert the output's HDU names are `['PRIMARY', 'SCI', 'ERR', 'DQ']`, `TELESCOP` survives, and the mode is 0o664. `::test_save_as_onto_the_open_file_keeps_its_extensions`: the same with the target equal to the open file, then `load_fits` it again and assert four HDUs. `::test_save_as_keeps_a_fits_suffix_it_recognises`: `frame.fit` and `frame.FITS` are saved under those names; `frame` becomes `frame.fits`.
- [ ] **Step 2: Run them and see them fail** (`['PRIMARY']` only; `frame.fit.fits`).
- [ ] **Step 3: Implement** `save_file_as` as: choose the path, append `.fits` only when `not is_fits_path(path)`, call `self.fits_reader.save(path)`, show the existing success / error messages.
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R3.

### Task 4: Whole-pixel ROI sampling (R9, R19)

**Files:**
- Modify: `pyql3/core/coords.py`, `pyql3/gui/tools/base_tool.py`, `pyql3/gui/tools/statistics.py:42-66`, `pyql3/gui/tools/fitting.py:233-260`, `pyql3/gui/tools/depth_plot.py:1540-1549`, `:1626-1629`, `:892-895`
- Test: `tests/test_coords.py`, `tests/test_analysis_tools.py`, `tests/test_depth_plot.py`

**Interfaces:**
- Produces: `coords.box_index_slices(x0, y0, width, height, nx, ny) -> tuple[slice, slice] | None`. `(x0, y0, width, height)` is a box in ImageItem coordinates; the result selects the pixels whose **centres** (`i + 0.5`) lie in `[x0, x0 + width) × [y0, y0 + height)`, clipped to `[0, nx) × [0, ny)`; `None` when nothing remains. Both ends are computed from the unclamped box.
- Produces: `BaseToolDialog.roi_plane_pixels(roi=None) -> tuple[np.ndarray, tuple[slice, slice]] | None` — the whole pixels of `self.roi` (or `roi`) in `image_viewer.current_plane() * image_viewer.data_multiplier`, with the slices used; `None` when the box misses the plane.

- [ ] **Step 1: Write the failing tests.** `test_coords.py::test_box_index_slices` — parametrized: `(35.0, 25.0, 10, 10, 80, 60)` → `(slice(35, 45), slice(25, 35))`; `(35.5, 25.5, 10, 10, 80, 60)` → the same; `(-5, 0, 10, 4, 30, 30)` → `(slice(0, 5), slice(0, 4))`; `(-50, 0, 10, 4, 30, 30)` → `None`; `(100, 0, 10, 4, 30, 30)` → `None`. `test_analysis_tools.py::test_statistics_reads_whole_pixels` and `::test_peak_fit_fwhm_does_not_depend_on_box_phase`, with the values in R9. `::test_statistics_reports_the_collapsed_plane`: in Z Range mode, Mean equals `current_plane()` at the box. `test_depth_plot.py::test_a_box_half_off_the_image_measures_only_what_it_covers`, with the values in R19, plus the background-box case.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** `box_index_slices` and `roi_plane_pixels`; use them in Statistics, Peak Fit and the three Depth Plot rectangle paths. Peak Fit reports its fitted centre as the numpy index `slice.start + x_fit`, matching Task 2's convention. A `None` result clears the tool's output and says "the box does not overlap the image" (Depth Plot: in `lbl_bg_info`, as its circle path does).
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R9 and R19.

### Task 5: One auto-load gate, and a header editor bound to its file (R4, R11)

**Files:**
- Modify: `pyql3/gui/main_window.py` (`load_fits` `:607-650`, `edit_header` `:794-800`, `on_file_detected` / `_attempt_auto_load` `:1354-1382`), `pyql3/gui/dialogs/header_editor.py` (`__init__`, `apply_table_edits`, `save_header`)
- Test: `tests/test_main_window_and_poller.py`, `tests/test_poller.py`

**Interfaces:**
- Produces: `MainWindow._auto_load_generation: int`. `load_fits(filepath, ext=None, force=False, show_errors=True, cancel_auto_load=True)` — when `cancel_auto_load` is true it increments the generation, cancelling every pending retry. `_attempt_auto_load(filepath, attempt, generation)` returns at once when `generation != self._auto_load_generation` or `not self.poller.is_polling()`, and calls `load_fits(..., show_errors=False, cancel_auto_load=False)`.
- Produces: `MainWindow.auto_load_held()` — a context manager. While it is active, `on_file_detected(path)` stores `path` in `self._deferred_auto_load` (the newest wins) and returns; on exit, a stored path goes through `on_file_detected`. `edit_header` runs `dialog.exec()` inside it.
- Produces: `HeaderEditorDialog` records `(fits_reader.filepath, fits_reader.hdul)` on open; `apply_table_edits` and `save_header` refuse, with `QMessageBox.warning`, when either has changed.
- `load_fits` sets `self._last_load_error = ""` at its start, and `"no image data"` in its data-is-None branch.

- [ ] **Step 1: Write the failing tests.** `test_main_window_and_poller.py::test_auto_load_during_header_edit_does_not_rewrite_the_new_frame` (R4's reproduction; assert frame 2's `OBJECT` and `CRVAL2` on disk are unchanged). `::test_an_auto_load_held_by_the_header_editor_runs_after_it_closes`: inside `auto_load_held()`, two detections; after exit the window shows the second and `load_fits` ran once. `::test_header_editor_refuses_to_save_after_its_file_changed`. `test_poller.py::test_a_stale_retry_does_not_replace_a_newer_frame`, `::test_a_retry_does_not_replace_a_file_opened_by_hand`, `::test_no_retry_after_polling_stops`, `::test_the_failure_warning_quotes_this_files_error` (all from R11).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement** the interfaces above. `WindowManager.open_path` calls `load_fits` with the default `cancel_auto_load=True`, so a Finder file also cancels pending retries.
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R4 and R11. **End of Phase 1: stop and report the staged diff.**

---

## Phase 2 — The viewer's display pipeline

### Task 6: One redisplay path for the current z-mode (R5, R27, R57)

**Files:**
- Modify: `pyql3/gui/viewers/image_viewer.py` (`refresh_display` `:1216-1225`, `apply_axis_mapping` `:1206-1212`, `on_imv_time_changed` `:443-468`), `pyql3/gui/main_window.py:756-763` (`set_scaling`)
- Test: `tests/test_image_viewer.py`, `tests/test_menu_actions.py`

**Interfaces:**
- Produces: `ImageViewer.show_current_plane() -> None` — for 3-D data, calls `apply_z_range()` when `radio_range` is checked and `apply_z_slice()` otherwise (which already handles boxcar and the single-slice path); for 2-D data, the existing 2-D display. `refresh_display()` rebuilds nothing itself: it calls `show_current_plane()` and then emits `display_changed`.

- [ ] **Step 1: Write the failing tests.** `test_image_viewer.py::test_redisplay_keeps_the_collapsed_plane`, parametrized over flip, a 90° rotation, `disp_as_dn`, an axis change and `set_data` of the same cube, as in R5 (rendered image equals `current_plane()`, Z Min / Z Max unchanged); a boxcar variant. `::test_reload_keeps_zmax_when_the_length_is_unchanged`. `::test_frame_keys_do_not_replace_a_collapse` (R27). `test_menu_actions.py::test_scaling_menu_keeps_the_collapse_display` (R57).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** `show_current_plane` as above; `apply_axis_mapping` writes `txt_zmax` only when the z length changed; `on_imv_time_changed` re-runs `apply_z_range()` when `radio_range` is checked; `set_scaling` calls `self.image_viewer.apply_contrast()` after setting the combo.
- [ ] **Step 4: Run the tests, then `tests/test_image_viewer.py`; they pass.** B4, B12, B16 and B17's tests must still pass.
- [ ] **Step 5: Close-out** for R5, R27, R57.

### Task 7: Viewer change signals, view-rotation mapping, and the rotation pivot (R67)

**Files:**
- Modify: `pyql3/gui/viewers/image_viewer.py` (signals near `:59`; `on_slider_changed` `:903-924`; `apply_z_range`; `apply_z_slice`; `set_data`; `apply_view_rotation` `:1380-1400`)
- Test: `tests/test_image_viewer.py`

**Interfaces:**
- Produces: `ImageViewer.plane_changed = Signal()` — emitted **once** each time the values on screen change: at the end of `apply_z_range` and `apply_z_slice`, in `on_slider_changed`'s single-slice `setCurrentIndex` branch, and in `set_data`'s 2-D path. `refresh_display` emits it only through `show_current_plane`.
- Produces: `ImageViewer.view_rotation_changed = Signal(float)` — emitted by `apply_view_rotation(angle)`. `set_data` resets the rotation by calling `apply_view_rotation(0.0)` after the new plane is shown, instead of assigning `self.view_rotation = 0.0`.
- Produces: `ImageViewer.item_to_view(x, y) -> tuple[float, float]` and `view_to_item(x, y) -> tuple[float, float]`, through the ImageItem's `mapToView` / `mapFromView`.
- `display_changed` keeps its meaning: the plane's geometry changed (flip, rot90, axis mapping, new data); it is also emitted on a unit change, which redisplays through `refresh_display`.
- **Signal order (contract, added in the Phase 2 review).** `display_changed` is always emitted together with `plane_changed` and after it (P, then D), on the 2-D and the 3-D path alike; `plane_changed` alone means only the values changed (slider step, boxcar or Z Range change). `set_data` emits `view_rotation_changed(0.0)` last, after P and D. The one exception is R84 (an unparsable Z field in Z Range mode emits D without P). Covered by `test_image_viewer.py::test_viewer_signals_arrive_in_one_order`.

- [ ] **Step 1: Write the failing tests.** `::test_plane_changed_fires_once_per_change`, parametrized: slider step, boxcar change, Z Range apply, flip, unit change, `set_data` (2-D and 3-D) — a counter connected to `plane_changed` reads exactly 1. `::test_view_rotation_changed_fires_with_the_angle`. `::test_item_to_view_round_trips_under_a_view_rotation` at 0, 37, 90, 180° on the 66×51 plane of `osiris_like_fits`. `::test_view_rotation_pivots_on_the_plane_centre` (R67: on a 51×66 plane, a 180° rotation leaves `item_to_view(25.5, 33.0)` at the same view point as before).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Pivot `apply_view_rotation` on `(display_data.shape[-2] / 2, display_data.shape[-1] / 2)`.
- [ ] **Step 4: Run the tests; they pass.** `tests/test_position_angle.py` must still pass.
- [ ] **Step 5: Close-out** for R67.

### Task 8: Hover readout through the ImageItem (R6)

**Files:**
- Modify: `pyql3/gui/viewers/image_viewer.py:1625-1640` (`mouse_moved`)
- Test: `tests/test_image_viewer.py`

**Interfaces:**
- Consumes: `coords.item_to_pixel`.

- [ ] **Step 1: Write the failing tests.** `::test_readout_follows_the_view_rotation` (R6's encoded image, view rotations 0, 37, 90, 180, asserting `Value: 5020`), extended with `flip` and `rot_angle = 90` cases so the readout's pixel equals `display_to_orig` of the hovered display pixel. `::test_readout_left_of_the_image_is_out_of_bounds` (item x = −0.6).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** map with `self.imv.getImageItem().mapFromScene(pos)`; pixel indices with `coords.item_to_pixel`.
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R6. **End of Phase 2: stop and report the staged diff.**

---

## Phase 3 — One tool lifecycle

### Task 9: `tear_down()` for close and Escape alike (R15, M1, R85)

**Files:**
- Modify: `pyql3/gui/tools/base_tool.py:126-129`, `cuts.py:298`, `depth_plot.py:1052`, `photometry.py:72` and `:92` (both `closeEvent`s), `advanced_plots.py:79`, `:164`, `plot_catalog.py:1271`, `region_list.py` (disconnect `regions_changed`)
- Test: `tests/test_menu_actions.py`

**Interfaces:**
- Produces: `BaseToolDialog.tear_down() -> None` — idempotent (a `self._torn_down` flag), removes the ROI, ends draw mode, and is the one place subclasses release what they added (they override it and call `super().tear_down()`). `closeEvent(e)` calls `tear_down()` then `super().closeEvent(e)`; `reject()` calls `tear_down()` then `super().reject()`. No subclass overrides `closeEvent` any more.
- Do **not** implement `reject()` as `self.close()`: measured, it leaves the dialog on screen after both `close()` and Escape (see R15).
- `tear_down()` releases `_retired_rois` synchronously (call `_drop_retired_rois()` after removing the ROI) rather than leaving it to the `QTimer.singleShot(0, ...)`: a dialog that Task 10 replaces and `deleteLater()`s can be deleted before that timer fires, and its retired ROIs are then left to the collector, which is M31's crash pattern.
- `tear_down()` clears `scene.dragItem` and the scene's hover references (`lastHoverEvent`) when they point at its ROI or one of its handles, before the ROI is deleted. This is M31's residual exposure: the scene otherwise keeps a reference to a deleted C++ object, and the next click can raise `RuntimeError: Internal C++ object already deleted`.

- [ ] **Step 1: Write the failing tests.** `test_menu_actions.py::test_escape_tears_a_tool_down_like_close`, parametrized over every `BaseToolDialog` subclass built on `loaded_viewer`: after `QTest.keyClick(dialog, Qt.Key.Key_Escape)` the dialog is hidden, none of its items is in the viewer's scene, and `exclusive_drag_owner()` is None (arm Draw Region first where the tool has one). `::test_close_still_hides_every_tool`: same parametrization with `dialog.close()`, asserting `not dialog.isVisible()` — this guards the wrong fix above. `::test_tear_down_twice_is_harmless`.
- [ ] **Step 2: Run them and see them fail** (Escape leaves the ROI).
- [ ] **Step 3: Implement.** Move each `closeEvent` body into that class's `tear_down`. Photometry's two `closeEvent`s become one guarded `tear_down` (M1). `RegionListDialog.tear_down` disconnects `layer.regions_changed`.
- [ ] **Step 4: Run the tests and the whole suite; they pass.**
- [ ] **Step 5: Close-out** for R15, M1 and R85 (R85, found while implementing this task: `add_roi_to_viewer` retired the ROI being added, so Photometry and Strehl lost their ROI one event-loop pass after opening; M1 is a row in the B15 table: prefix its Issue cell with `✅ FIXED —`).

### Task 10: `_open_tool()` for every opener (R16)

**Files:**
- Modify: `pyql3/gui/main_window.py` (every `open_*` tool opener `:802-886`, `:943-948`, `:1299-1305`; `close_tool_dialogs` `:595-605`), `AGENTS.md` (remove the exception note added on 2026-10-21 under `TOOL_DIALOG_ATTRS`)
- Test: `tests/test_multi_window.py`, `tests/test_region_ui.py`

**Interfaces:**
- Consumes: `BaseToolDialog.tear_down()` (Task 9).
- Produces: `MainWindow._open_tool(attr: str, factory: Callable[[], QDialog]) -> QDialog` — returns the dialog in `attr` when it is alive and visible (raised); otherwise tears down and `deleteLater()`s a hidden one, builds `factory()`, stores it in `attr`, shows and raises it. `open_depth_plot` and `open_gaussian_fit` keep their re-targeting of an already visible dialog after the call; `open_surface_plot` keeps its `try/except` around the call.
- `close_tool_dialogs()` also closes every dialog in `self._region_property_dialogs.values()`.
- A dialog replaced and `deleteLater()`'d must still release its retired ROIs through `_drop_retired_rois` (M31): rely on Task 9's synchronous release in `tear_down()`, or process events before `deleteLater()`. Otherwise those ROIs are left to the collector, M31's crash pattern.

- [ ] **Step 1: Write the failing tests.** `test_multi_window.py::test_every_tool_dialog_closes_with_its_window` and `test_region_ui.py::test_reopening_region_list_does_not_multiply_refreshes` (both from R16). `test_multi_window.py::test_opening_a_tool_twice_reuses_it`, parametrized over every opener: the second call returns the same object and the viewer holds one ROI. `::test_reopening_a_closed_tool_leaves_one_instance`: 20 open/close cycles of the Horizontal Cut, then `qapp.processEvents()`; `len(window.findChildren(CutPlotDialog)) == 1`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement**, and route every opener through `_open_tool`. Remove the AGENTS.md exception note.
- [ ] **Step 4: Run the tests and the whole suite ten times** (`for i in $(seq 10); do QT_QPA_PLATFORM=offscreen uv run pytest -q tests/test_multi_window.py tests/test_region_ui.py || break; done`). `deleteLater()` on a dialog holding pyqtgraph items is the M18/M27 shape; ten clean runs is the bar those fixes used.
- [ ] **Step 5: Close-out** for R16. **End of Phase 3: stop and report the staged diff.**

---

## Phase 4 — Everything that displays or measures follows the viewer

### Task 11: Tools subscribe to the viewer (R14, R87)

**Files:**
- Modify: `pyql3/gui/tools/base_tool.py`, `statistics.py`, `photometry.py`, `fitting.py`, `strehl.py`, `cuts.py:92-93`, `:298-301`, `advanced_plots.py:29-30`, `:115-116`, `:120-164` (Contour `clear_contours` and `update_plot`, R87)
- Test: `tests/test_analysis_tools.py`, `tests/test_cuts.py`

**Interfaces:**
- Consumes: `plane_changed`, `view_rotation_changed`, `display_changed` (Task 7); `tear_down` (Task 9); `roi_plane_pixels` (Task 4).
- Produces: in `BaseToolDialog.__init__`, connections from the three viewer signals to private slots that call, only while `self.isVisible()` and not torn down, `on_plane_changed()`, `on_geometry_changed()` and `on_view_rotated(angle)`. Defaults: `on_plane_changed()` calls `self.on_roi_changed()` and is the recompute hook; `on_geometry_changed()` **does nothing**, because every `display_changed` is preceded by a `plane_changed` (Task 7's signal order), so a measuring tool has already recomputed when it arrives — calling `on_plane_changed()` from it would compute everything twice per flip, load or unit change; `on_view_rotated(angle)` does nothing. A tool whose output depends on the geometry and not on the plane overrides `on_geometry_changed()` (the Depth Plot, Task 12; the catalog, Task 13; the Rotate dialog, Task 16). `tear_down()` disconnects all three.
- Reconcile `MainWindow.update_tools_for_unit()` (`main_window.py`, called by `set_display_unit` after `refresh_display()`). Today it calls each visible tool's `update_plot()` / `update_stats()` on a unit change. The unit change already emits `plane_changed` (then `display_changed`), so for every tool subscribed through `BaseToolDialog` that call is a second recompute: remove it for them. Keep it only for what still needs a unit-change notice that the signals do not give — a dialog in `TOOL_DIALOG_ATTRS` that is not a `BaseToolDialog` (check each entry when implementing and list them in the report), and any label that depends on `disp_as_dn` but not on the plane. If no such case remains, delete the loop and say so in the docstring of `set_display_unit`; update AGENTS.md's `TOOL_DIALOG_ATTRS` paragraph, which names `update_tools_for_unit()` as one of the two walkers of the list.
- Cuts, Surface and Contour override `on_plane_changed()` to call `update_plot()`; their `imv.sigTimeChanged` connections are removed. Photometry and Strehl read `current_plane() * data_multiplier`, not `display_data[imv.currentIndex]`.

- [ ] **Step 1: Write the failing tests.** `test_analysis_tools.py::test_tools_follow_the_displayed_plane` (R14). `test_cuts.py::test_cuts_follow_boxcar_and_range`. `test_analysis_tools.py::test_a_slider_step_does_not_recompute_overlay_tools` (Review Focus 1): spies on `PlotCatalogDialog.update_plot` and on `DepthPlotDialog.update_plot` in Depth mode record no call across ten slider steps. `test_analysis_tools.py::test_each_change_recomputes_a_tool_once`, parametrized over a flip, a unit change through `MainWindow.set_display_unit`, a `set_data`, and **Apply after editing a Z field**: with focus in Z Min, type a new value and click Apply — focus leaving the field fires `editingFinished` and then the button's `clicked`, so `plane_changed` arrives twice (P, P; measured in the Phase 2 review). Add the same case for the Boxcar field and its Apply button, which are wired the same way (`txt_boxcar.editingFinished` and `btn_apply_slice.clicked` both run `apply_z_slice`). A spy on each open measuring tool's recompute (Statistics `update_stats`, Photometry, Peak Fit, Strehl, a Horizontal Cut's `update_plot`) records exactly one call per change. The Apply case fails until `apply_z_range` is not run twice for one edit (connect only one of the two, or skip a re-run whose range equals the one on screen); fix it in this task. `test_analysis_tools.py::test_contour_updates_retire_their_old_items` (R87): with contours shown, record the `IsocurveItem`s, trigger an update (a plane change), and check that the old items went to the dialog's retired list rather than being dropped, and that after the event loop runs (and after Escape) none of them is still valid (`shiboken6.isValid`).
- [ ] **Step 2: Run them and see them fail.** The last test keeps failing after this task: the default `on_plane_changed` reaches `DepthPlotDialog.on_roi_changed`, which recomputes. Mark it `xfail(strict=True)` here; Task 12 removes the mark. (The catalog half already passes: its `on_roi_changed` is the base no-op.)
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R14 and R87.

### Task 12: The Depth Plot follows the viewer (R7, R28)

**Files:**
- Modify: `pyql3/gui/tools/depth_plot.py` (`update_plot` `:1505-1510`, `_draw_depth_curves` `:1418-1503`, label code `:139-140`, `:1681-1682`, `:1707-1708`)
- Test: `tests/test_depth_plot.py`, `tests/test_analysis_tools.py` (remove Task 11's `xfail` mark)

**Interfaces:**
- Consumes: Task 11's hooks.
- Produces: `DepthPlotDialog.on_plane_changed()` recomputes only in the cut modes (a cut is taken through the plane on screen); `on_geometry_changed()` recomputes in Depth mode, because the Depth-mode spectrum depends on the geometry (which pixels the aperture covers, and the unit) and not on the plane. In a cut mode it does nothing: the `on_plane_changed()` that precedes every `display_changed` has already recomputed, and recomputing again would count each flip, load or unit change twice. So each flip, load or unit change recomputes the Depth Plot exactly once in either mode, and a slider step in Depth mode recomputes it not at all. Nothing else in this task may recompute on the same signal (no extra `display_changed` connection of its own). `DepthPlotDialog._set_intensity_label() -> None` sets the left-axis label from `image_viewer.disp_as_dn`; all three label writes go through it.

- [ ] **Step 1: Write the failing tests.** `::test_a_new_cube_replaces_the_plotted_spectrum`, `::test_a_flip_replaces_the_plotted_spectrum`, `::test_a_2d_load_clears_every_curve` (R7), `::test_cut_modes_follow_the_slider`, `::test_the_intensity_label_follows_the_display_unit` (R28).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** On non-3-D data `update_plot` clears `plot_data`, `plot_bg` and `plot_sub`, calls `sync_legend()`, and says why in `lbl_bg_info`.
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R7 and R28.

### Task 13: Catalog positions resolved once per geometry, as arrays (R8, R31, R61, R86)

**Files:**
- Modify: `pyql3/gui/tools/plot_catalog.py` (`update_plot` `:899-1000`, `_row_to_display` / world placement `:846-895`, `:957`, `_clear_text_items` `:1030-1033` and `tear_down` `:1283-1285` for R86)
- Test: `tests/test_plot_catalog.py`

**Interfaces:**
- Consumes: Task 11's hooks; `ImageViewer.display_axis_indices()`.
- Produces: `PlotCatalogDialog._resolve_orig_positions() -> np.ndarray` — shape `(N, 2)`, orig coordinates of every row, NaN where a row cannot be placed; computed with whole-column arrays (one `SkyCoord`, one `world_to_pixel_values` through the `world_axis_physical_types` mapping for **every** `naxis`, then `orig_to_display` element-wise) and cached until the catalog, the coordinate columns, the coordinate type or the WCS changes. `replot_markers() -> None` redraws markers, labels and the highlight from that cache without moving the view. `on_plane_changed()` does nothing; `on_geometry_changed()` calls `replot_markers()`; `on_view_rotated(angle)` re-places the visible labels. On every `set_data`, `view_rotation_changed(0.0)` follows P and D (Task 7's signal order), so the labels are re-placed once more after `replot_markers()`; keep that re-place cheap (visible labels only) rather than redrawing markers.

- [ ] **Step 1: Write the failing tests.** `::test_markers_follow_a_flip_and_a_new_image` (R8). `::test_markers_follow_a_view_rotation` (labels too). `::test_catalog_given_before_any_image_plots_when_one_arrives`. `::test_world_catalog_refresh_is_not_per_row` (R31; the 0.5 s bound for a marker-size change on 20,000 rows, positions equal to `wcs.world_to_pixel_values` for a sample). `::test_world_catalog_on_a_dec_first_2d_image` (R61). `test_plot_catalog.py::test_closing_the_catalog_retires_its_markers_and_labels` (R86): with markers and labels shown, record `scatter_item`, `highlight_item` and the label pool, press Escape, and check that each went through `_retired` and none is still valid (`shiboken6.isValid`).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Keep `_row_to_display` only for the single-row highlight, reading from the cache.
- [ ] **Step 4: Run the tests and `tests/test_plot_catalog.py`; they pass.** M28–M30's timing tests must still pass.
- [ ] **Step 5: Close-out** for R8, R31, R61 and R86.

### Task 14: Catalog selection recentres only when asked, in view coordinates (R29, R60)

**Files:**
- Modify: `pyql3/gui/tools/plot_catalog.py:1000`, `:1204-1221`
- Test: `tests/test_plot_catalog.py`

**Interfaces:**
- Consumes: `ImageViewer.item_to_view` (Task 7).
- Produces: `on_table_selection` splits into `_draw_highlight() -> None` and `_recentre_on_selection() -> None`. `update_plot` and `replot_markers` call only `_draw_highlight`; the recentre runs from `selectionChanged` and *Center on Source*, and maps the source through `item_to_view`.

- [ ] **Step 1: Write the failing tests.** `::test_selecting_a_row_centres_it_under_a_view_rotation` (R29), `::test_a_style_change_does_not_move_the_view` (R60).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R29 and R60.

### Task 15: Regions follow the plane and the view rotation (R36, R45, R63)

**Files:**
- Modify: `pyql3/gui/viewers/region_layer.py` (`:271-276` connections, `_current_channel` / `_in_channel_range` `:596-599`, `:679-683`, `_draw_bulk_labels` `:578-594`, label culling `:637-650`), `pyql3/gui/tools/region_list.py:266-282` (`zoom_to`)
- Test: `tests/test_region_layer.py`, `tests/test_region_ui.py`

**Interfaces:**
- Consumes: `plane_changed`, `view_rotation_changed`, `item_to_view` (Task 7).
- Produces: `RegionLayer.orig_to_view(x, y) -> tuple[float, float]` (orig → ImageItem via `_to_item`, then `item_to_view`), used by `zoom_to` and both label-culling paths. The layer **replaces** its existing `slider_slice.valueChanged` connection (`region_layer.py:276`) with `plane_changed` → `update_channel_visibility`, rather than adding a second connection (both would fire on every slider step); geometry re-placement stays on `display_changed` → `refresh`, which always follows a `plane_changed`, so `refresh` need not update channel visibility again. `view_rotation_changed` connects to a re-cull; on every `set_data` it arrives last, after P and D. `_current_channel()` returns `None` for non-3-D data and, in Collapse Region mode, the `(zmin, zmax)` range; `_in_channel_range` treats `None` as "show" and a range as "show if it overlaps".

- [ ] **Step 1: Write the failing tests.** `test_region_layer.py::test_channel_ranges_follow_a_collapse_range` (R36, overlap rule: `z_range=(0, 5)` hidden at 30–40, shown at 1–4 and at 4–9), `::test_a_channel_range_does_not_hide_a_region_on_a_2d_image` (R63, both render modes), `::test_labels_are_culled_in_view_coordinates_under_a_view_rotation` (R45, both modes). `test_region_ui.py::test_zoom_to_lands_on_the_region_under_a_view_rotation` (R45).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and the region test files; they pass.**
- [ ] **Step 5: Close-out** for R36, R45, R63.

### Task 16: The Rotate dialog follows reloads (R68)

**Files:**
- Modify: `pyql3/gui/tools/rotate.py:70`, `:95-116`
- Test: `tests/test_position_angle.py`

**Interfaces:**
- Consumes: Task 11's hooks (`RotateDialog` is a `BaseToolDialog`).
- Produces: `RotateDialog.on_view_rotated(angle)` sets its spin box to `angle` with signals blocked and calls `update_pa_info()`; `on_geometry_changed()` calls `update_pa_info()`. On every `set_data`, `view_rotation_changed(0.0)` arrives after P and D, so the dialog's final state comes from `on_view_rotated(0.0)`.

- [ ] **Step 1: Write the failing test.** `::test_rotate_dialog_follows_a_reload`: rotate 30°, `set_data` the same cube, assert the spin box reads 0.0 and the North label matches `north_east_display_angles()`.
- [ ] **Step 2: Run it and see it fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run it; it passes.**
- [ ] **Step 5: Close-out** for R68. **End of Phase 4: stop and report the staged diff.**

---

## Phase 5 — Loading files and reading headers

### Task 17: A failed load changes nothing (R12)

**Files:**
- Modify: `pyql3/core/fits_reader.py:44-100` (`load`), `pyql3/gui/main_window.py:607-650`
- Test: `tests/test_fits_reader.py`, `tests/test_main_window_and_poller.py`

**Interfaces:**
- Produces: `class NoImageData(ValueError)` in `fits_reader.py`. `FitsReader.load(filepath, ext=None, force=False, require_data=False)` is all-or-nothing: it opens a new HDUList (when one is needed) into a local, reads the chosen extension's `.data` before committing, and only then closes the old HDUList and assigns `hdul`, `filepath`, `data`, `header`, `current_ext`, `image_extensions` and `_file_signature`. On any exception the new HDUList is closed and the reader is exactly as before. With `require_data=True`, a file with no displayable 2-D or 3-D image raises `NoImageData` before committing.
- `MainWindow.load_fits` passes `require_data=True` and shows the existing "No valid data found in FITS file." for `NoImageData`. (Decision: see the S7 note at the top.)

- [ ] **Step 1: Write the failing tests.** `test_fits_reader.py::test_a_failed_load_leaves_the_reader_unchanged` (a good two-extension cube, then a truncated file; `filepath`, `current_ext`, `get_data()` and `hdul` are the cube's, and `hdul` is still open), `::test_require_data_refuses_a_table_only_file`. `test_main_window_and_poller.py::test_failed_load_leaves_the_reader_on_the_displayed_file` (R12, including the extension switch). Update `test_main_window_and_poller.py`'s S7 test, if it asserts the data-less header is published, to assert the window's reader is unchanged instead.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Keep B5's reuse of an unchanged, same-path HDUList (extension switches keep unsaved header edits).
- [ ] **Step 4: Run the tests and `tests/test_fits_reader.py`; they pass.** B5 and B18 tests must still pass.
- [ ] **Step 5: Close-out** for R12. Add one sentence under S7 in BUGS.md: "Superseded in part by R12: a data-less file no longer replaces the window's reader."

### Task 18: `set_data` validates before it changes anything, and survives an unreadable WCS (R13)

**Files:**
- Modify: `pyql3/core/header_info.py`, `pyql3/gui/viewers/image_viewer.py:1066-1090`
- Test: `tests/test_header_info.py`, `tests/test_image_viewer.py`

**Interfaces:**
- Produces: `header_info.build_wcs(header) -> tuple[WCS | None, str]` — never raises. Tries `WCS(header)` (warnings suppressed); on failure retries with a copy whose `CUNITn` values `micron` / `microns` (any case) are `um`; if that fails, returns `(None, "<exception message>")`. The second element is `""` on success.
- `set_data` checks `data.ndim` and builds the WCS **before** assigning `raw_data`, `header` or the rotation; a non-empty problem string shows as `WCS: unreadable (<message>)` in `lbl_wcs`.

- [ ] **Step 1: Write the failing tests.** `test_header_info.py::test_build_wcs_accepts_micron`, `::test_build_wcs_reports_an_unreadable_unit` (`CUNIT1 = 'parsec'` → `(None, msg)` with `msg` non-empty). `test_image_viewer.py::test_micron_cunit_loads_with_a_wavelength_axis` and `::test_unparseable_wcs_leaves_data_displayed_without_wcs` (R13). `::test_a_refused_array_leaves_the_viewer_unchanged`: `set_data` of a 4-D array raises and `raw_data` is still the previous cube.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests; they pass.** B6's tests must still pass.
- [ ] **Step 5: Close-out** for R13.

### Task 19: The poller keeps unsettled files across restarts, and ignores the window's own save (R43, R70)

**Files:**
- Modify: `pyql3/services/poller.py:160-215`, `pyql3/gui/dialogs/polling.py:121-125`, `pyql3/core/fits_reader.py`, `pyql3/gui/main_window.py` (`_attempt_auto_load`)
- Test: `tests/test_poller.py`, `tests/test_main_window_and_poller.py`

**Interfaces:**
- Produces: `start_polling(path)` on the directory it already watches keeps `_pending`, `_held_path`, `_held_count` and `_hold_ticks`; a takeover passes them from the previous owner to the new one. `PollingDialog` applies the interval on `editingFinished`.
- A restart on the directory already watched does not emit `polling_stopped` (it would cancel a pending retry for a file the restarted observer will not announce again). A takeover and a real stop still emit it.
- Produces: `FitsReader.holds_current_copy(path) -> bool` — `path == self.filepath` and the file's `(st_mtime_ns, st_size, st_ino)` equals `self._file_signature`. `_attempt_auto_load` returns without loading when it is true.

- [ ] **Step 1: Write the failing tests.** `test_poller.py::test_restarting_a_watch_keeps_unsettled_files` (R43, the interval case and the takeover case). `test_poller.py::test_changing_the_interval_keeps_a_pending_retry` (R43, the `polling_stopped` regression from Task 5: a queued auto-load retry still loads after `poller.interval` changes). `test_main_window_and_poller.py::test_saving_the_header_in_a_watched_directory_keeps_the_view` (R70: zoom to 5×5, save the header in place, run the poller's checks, the view range is unchanged).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and `tests/test_poller.py`; they pass.** S0, S2 and M26 tests must still pass.
- [ ] **Step 5: Close-out** for R43 and R70.

### Task 20: Axes found through the WCS, not by string (R24, R26, R52, R56, R81, R82, R88)

**Files:**
- Modify: `pyql3/core/header_info.py`, `pyql3/gui/viewers/image_viewer.py` (`apply_axis_mapping` `:1155-1199`, default X axis `:1117-1122`, `get_north_angle_base` `:1283-1347`, `mouse_moved` `:1655-1700`, `get_wavelength_for_slice`), `pyql3/gui/tools/depth_plot.py:1264` (and its wavelength-unit handling for the line list), `AGENTS.md` ("FITS axis / WCS conventions": replace "`set_data()` picks the default X-axis by sniffing whether `CTYPE1` contains `RA`" with the rule below)
- Test: `tests/test_header_info.py`, `tests/test_image_viewer.py`, `tests/test_position_angle.py`

**Interfaces:**
- Produces: `header_info.spectral_axis(wcs, header=None) -> int | None` (0-based) — `wcs.wcs.spec` when ≥ 0; else the axis whose `WAT<n>_001` card contains `axtype=wave`; else `DISPAXIS - 1` when present and not a celestial axis; else `None`. `apply_axis_mapping`, the default-axis choice and `depth_plot.py:1264` all use it.
- Produces: `header_info.spectral_unit(wcs, header, axis) -> astropy.units.Unit | None` — the unit from `CUNIT<n>`, else from `units=` in `WAT<n>_001`; `None` when neither states one. A `None` unit is never inferred: the axis is labelled "Wavelength (Unknown)" and shows the header's numbers as they are, and the line list is unavailable on it, saying "needs a known wavelength unit". Wavelengths shown and compared with line lists are converted to µm through the unit when it is known.
- Produces: `header_info.default_axes(wcs, header) -> tuple[int, int, int]` — 0-based FITS axes for (X, Y, Z), decided in this order (R81; the last rule is the user's decision of 2026-10-21): (1) celestial axes exist → X = `wcs.wcs.lng`, Y = `wcs.wcs.lat`, Z = the remaining axis; (2) `spectral_axis` finds one → Z = it, X and Y = the other two in FITS order; (3) `INSTRUME` or `CURRINST` is `OSIRIS` → (2, 1, 0); (4) otherwise (0, 1, 2) — FITS axes 1, 2, 3 are x, y, wavelength, as most IFUs write them. `set_data` uses it for the default AXIS combos. `mouse_moved` computes the sky readout from `display_axis_indices()` whether or not Z is spectral, and sets `lbl_wcs` to `WCS: N/A` on every path that produces no coordinate.
- Duplicate axis choice (R26): the other combo takes the axis the changed one had; `current_x_axis`, `current_y_axis`, `current_z_axis` are assigned once, after that.
- 2-D data branches on `raw_data.ndim`, not `wcs.naxis`; the 2-D path of `set_data` sets `wcs_z_idx = None`. The ROTPOSN fallback uses `theta_n_base = 90 - north_pa`.

- [ ] **Step 1: Write the failing tests.** `test_header_info.py::test_spectral_axis` (WAVE, AWAV, FREQ, none), `::test_spectral_axis_from_iraf_cards` and `::test_a_missing_spectral_unit_is_unknown` (R82), `::test_default_axes` (R81's five cases). `test_image_viewer.py::test_a_nifs_cube_opens_with_wavelength_on_z` (R81) and `::test_an_iraf_spectral_axis_has_wavelengths` (R82: without a unit, slice 0 is 19968.66 and the label reads "Wavelength (Unknown)"; with `units=angstroms` in `WAT3_001`, slice 0 is 1.99687 µm), `test_depth_plot.py::test_the_line_list_needs_a_known_wavelength_unit` — build the NIFS header synthetically as in R81's reproduction, since `example/` is not tracked. `test_image_viewer.py::test_awav_is_a_spectral_axis`, `::test_wcs_readout_never_shows_a_previous_position` (R24), `::test_duplicate_axis_choice_swaps_the_axes` (R26: set Y to X's axis; the combos are swapped and `display_axis_indices()` has two distinct entries matching them), `::test_2d_image_with_a_third_wcs_axis_reads_sky` (R52). `test_position_angle.py::test_2d_image_with_a_third_wcs_axis_has_the_right_compass` (R52), `::test_rotposn_fallback_matches_the_wcs` (R56).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests, `tests/test_image_viewer.py` and `tests/test_position_angle.py`; they pass.** B20's tests must still pass.
- [ ] **Step 5: Close-out** for R24, R26, R52, R56, R81, R82, R88.

### Task 21: An extension without its own WCS borrows the science header's (R25, R89)

**Files:**
- Modify: `pyql3/core/fits_reader.py`, `pyql3/gui/viewers/image_viewer.py` (`set_data` signature), `pyql3/gui/main_window.py:619-620`
- Test: `tests/test_fits_reader.py`, `tests/test_main_window_and_poller.py`

**Interfaces:**
- Consumes: `header_info.spectral_axis`, `total_dn_multiplier`, `build_wcs`.
- Produces: `FitsReader.reference_header() -> fits.Header | None` — the header to take WCS and integration time from when the current extension's own header lacks them: the first image extension with the same data shape whose header has a celestial or spectral WCS, else the primary header; `None` when the current header has both already.
- Produces: `ImageViewer.set_data(data, header=None, reference_header=None)` — WCS from `header` when it has celestial or spectral axes, else from `reference_header`, **but only from a reference header whose WCS has as many axes as the data** (a NIFS primary header carries a 2-axis `RA---TAN`/`DEC--TAN` WCS of the raw detector frame, which must not be applied to the 3-D cube); integration time and `BUNIT` from `header` when present, else from `reference_header` (NIFS keeps `EXPTIME` and `COADDS` in the primary header only). The default axes come from `header_info.default_axes` (Task 20) on whichever WCS was used.

- [ ] **Step 1: Write the failing tests.** `test_fits_reader.py::test_reference_header_for_an_error_extension`. `test_main_window_and_poller.py::test_error_extension_keeps_the_science_axis_layout` and `::test_noise_extension_uses_the_primary_integration_time` (R25). `::test_a_primary_wcs_with_fewer_axes_is_not_borrowed`: a NIFS-like file (primary with a 2-axis celestial WCS and `EXPTIME`, SCI as in R81) gets no celestial map, Z = AXIS 3, and the primary's `EXPTIME`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R25 and R89 (R89, found while implementing this task: the region layer's `_drop_retired` crashed in `gc.collect()` once the viewer held a `fits.Header`; fixed as M31 was, with the shared code in `pyql3/gui/scene_items.py`).

### Task 44: Total DN offered only for a declared count rate (R83)

(Numbered after the original 43 tasks; it runs here, after Task 21, because it reads the reference header Task 21 provides.)

**Files:**
- Modify: `pyql3/core/header_info.py`, `pyql3/gui/viewers/image_viewer.py` (`data_multiplier`, `update_colorbar_label`), `pyql3/gui/main_window.py` (Units menu `:223-229`, `set_display_unit`), every hard-coded "DN/s" / "Total DN" label in `pyql3/` (13 today, among them `DepthPlotDialog._set_intensity_label` from Task 12 and the cut tools), `AGENTS.md` (the `data_multiplier` sentence), `docs/tools.md` (the Units menu)
- Test: `tests/test_header_info.py`, `tests/test_menu_actions.py`, `tests/test_image_viewer.py`

**Interfaces:**
- Consumes: `total_dn_multiplier`, `integration_seconds` (Task 1); `reference_header` (Task 21).
- Produces: `header_info.DataUnit` (a small dataclass: `label: str`, `is_rate: bool | None`, `reason: str`) and `header_info.data_unit(header) -> DataUnit`. `BUNIT` matching OSIRIS's text (`... per second ...`, case-insensitive) → label `DN/s`, `is_rate=True`; a string `astropy.units` parses → its own label, `is_rate` True when the unit's decomposition has a negative power of seconds (`DN/s`, `electron/s`), False otherwise (`MJy/sr`); missing or unparseable → label `Unknown`, `is_rate=None`. `reason` is the sentence shown when Total DN is unavailable ("data are in MJy/sr", "the file does not state its unit", "no integration time in the header").
- `header_info.total_dn_multiplier(header)` falls back to `EXPTIME` (not multiplied by `COADDS`) when neither `TRUITIME` nor `ITIME` is usable, and returns `None` when no exposure keyword exists.
- Produces: `ImageViewer.data_unit` (set in `set_data`, from `header` else `reference_header`), `ImageViewer.total_dn_available -> bool` (`data_unit.is_rate is True` and a multiplier exists), `ImageViewer.value_unit_label() -> str` (`"Total DN"` when showing Total DN, else `data_unit.label`). Every unit label in the application goes through `value_unit_label()`.
- `MainWindow`: after each load, "As Total DN" is enabled only when `total_dn_available`; when disabled its tooltip is `data_unit.reason`. Loading a file that does not support Total DN while it is selected switches back to the native unit and says so in the status bar.
- That switch is made inside `set_data`, before `plane_changed` / `display_changed` are emitted: `disp_as_dn` is cleared there when the new file does not support Total DN. Switching after the load calls `refresh_display` a second time and recomputes every open tool twice per load.
- Strehl writes its unit label only when the computation succeeds, and labels its normalised profile "Intensity (DN/s)" today; fix both here (the label goes through `value_unit_label()`, and a normalised profile is not labelled with a data unit).

- [ ] **Step 1: Write the failing tests.** `test_header_info.py::test_data_unit` (OSIRIS text, `MJy/sr`, `DN/s`, `electron/s`, missing), `::test_exptime_is_the_last_fallback`. `test_menu_actions.py::test_total_dn_is_offered_only_for_a_count_rate` (an MJy/sr cube and a cube without `BUNIT` leave "As Total DN" disabled with the tooltip; `osiris_like_fits` enables it), `::test_loading_a_non_rate_cube_leaves_total_dn`. `test_image_viewer.py::test_labels_use_the_declared_unit` (an MJy/sr cube's colorbar reads "MJy/sr"; a cube without `BUNIT` reads "Unknown").
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests and the whole suite; they pass.** S5's tests (the DN multiplier in cut profiles) must still pass.
- [ ] **Step 5: Close-out** for R83. **End of Phase 5: stop and report the staged diff.**

---

## Phase 6 — Depth Plot details

### Task 22: Spectral photometry edge cases (R20, R21, R22)

**Files:**
- Modify: `pyql3/core/spectral_photometry.py:138-218`, `pyql3/gui/tools/depth_plot.py:1584-1592`, `:1614-1615`
- Test: `tests/test_spectral_photometry.py`, `tests/test_depth_plot.py`

**Interfaces:**
- Produces: `SpectrumResult.aperture_weight: np.ndarray` — the per-channel sum of finite aperture weights, beside the existing scalar `aperture_area`. Region-mode subtraction passes it to `subtract_background()`. Total is NaN where `aperture_weight == 0` (the rectangle path does the same after its `nansum`). `annulus_spectrum(...)` raises `ApertureError("the sky annulus starts inside the aperture")` when `r_in < r_ap`.

- [ ] **Step 1: Write the failing tests.** `test_depth_plot.py::test_a_region_background_uses_the_per_channel_area` (R20). In `test_spectral_photometry.py`, extend the existing all-NaN test with `np.isnan(result.source[1])` and `np.isnan(result.subtracted[1])` for `combine="Total"` (R21), and add `::test_an_annulus_inside_the_aperture_is_refused` (R22). `test_depth_plot.py::test_growing_the_aperture_past_the_sky_annulus_says_so` (R22, the status line).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests; they pass.** The Photometry cross-check must still agree.
- [ ] **Step 5: Close-out** for R20, R21, R22.

### Task 23: Depth Plot line lists, Log X, the rectangle spins, the off-image circle mask, and a user-chosen wavelength unit (R53, R54, R55, R80, R82 follow-up)

**Files:**
- Modify: `pyql3/gui/tools/depth_plot.py` (`PixelIndexAxis` `:87-101`, `mouse_moved` `:562-573`, `update_line_overlays` `:1269-1339`, line-list loading `:1096-1158`, `:1250-1259`, spins `:669-698`, `:749-770`, `:1355-1416`; R80: `_circular_mask` and its callers `_cut_region` and the background box in `update_plot`), `pyql3/gui/viewers/image_viewer.py` (the wavelength unit the slice label uses), `docs/tools.md` (the Depth Plot line-list section)
- Test: `tests/test_depth_plot.py`, `tests/test_image_viewer.py`

**Interfaces:**
- Produces: `PixelIndexAxis(log_x: bool)` state set from `chk_log_x`; with Log X checked, view x is converted with `10 ** x` for comparison and readout, and each line is placed at `log10(wavelength)`. `linelist_files` is keyed by full path and read with `combo_linelist.currentData()`; whitespace separates fields when a line has no comma; an empty parse sets `lbl_line_info` to `No lines could be read from <name>`. `DepthPlotDialog._sync_rect_spins() -> None`, called from `on_roi_changed`, `on_radius_changed` and `toggle_roi_shape`.
- Consumes: `header_info.spectral_unit` (Task 20).
- Produces (user-chosen wavelength unit): `ImageViewer.wavelength_unit_override: astropy.units.Unit | None` (one value per window, `None` by default) and two methods on `ImageViewer`. `wavelength_unit() -> astropy.units.Unit | None` keeps the signature Phase 5 gave it (its callers are `ImageViewer` and the Depth Plot) and returns the override when set, else the header's `spectral_unit`. A new `wavelength_unit_label() -> str` returns the label text: `"µm"` for a header unit, `"Å, set by you"` for an override, `"Unknown"` for neither. The slice label, the Depth Plot's x-axis label, its cursor readout and the line list all read the unit through `wavelength_unit()` and the label through `wavelength_unit_label()`. The override is kept across loads (so successive polled frames from the same instrument keep it) and cleared by a load whose header states its own unit, unless the user had overridden that same stated unit (a file stating a different unit clears it; ruled in the Phase 6 review, I2).
- Produces: a *Cube wavelength unit* combo box in the Plot Axes group (first planned beside the line-list controls; moved because it read as the line list's unit), entries `From header (<unit or Unknown>)`, `Å`, `nm`, `µm`. Choosing one of the last three sets `wavelength_unit_override`; `From header` clears it. With the unit Unknown and no override, the line list stays unavailable and `lbl_line_info` says `Set the cube's wavelength unit under Plot Axes to show the line list (the line list is in µm)`; the combo is the control that enables it.

- [ ] **Step 1: Write the failing tests.** `::test_the_line_list_survives_log_x` (R53), `::test_two_line_lists_with_one_name_load_their_own_files`, `::test_an_unreadable_line_list_says_so`, `::test_a_whitespace_separated_line_list_loads` (R54), `::test_the_rectangle_spins_follow_a_radius_edit` (R55), `::test_a_circle_off_the_image_masks_the_drawn_circle` (R80: a Circle background box half off the low edge keeps exactly the drawn circle's on-image pixels). `::test_choosing_a_wavelength_unit_enables_the_line_list` (the R81/R82 NIFS-like header with no unit: line list unavailable with the message; choose `Å` → lines are drawn, the axis reads `Wavelength (Å, set by you)` and channel 0 is 1.99687 µm), `::test_the_wavelength_unit_choice_survives_a_reload_without_a_unit`, `::test_a_header_unit_replaces_an_unused_override`. `test_image_viewer.py::test_the_slice_label_uses_the_chosen_wavelength_unit`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R53, R54, R55, R80. In BUGS.md R82, add under Fix a line that the user-chosen unit is implemented here. **End of Phase 6: stop and report the staged diff.**

---

## Phase 7 — Other tools and FITS I/O

### Task 24: Aperture Photometry sky at edges and NaNs (R18)

**Files:**
- Modify: `pyql3/gui/tools/photometry.py:118-160`
- Test: `tests/test_analysis_tools.py`

- [ ] **Step 1: Write the failing tests.** `::test_photometry_on_flat_sky_is_zero_everywhere` (centre, over the edge, at the corner, one NaN pixel — R18's values) and `::test_an_inverted_annulus_shows_a_message`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** annulus values from `mask.get_values(data)` kept where finite; `aperture_photometry(..., mask=~np.isfinite(data))`; sky scaled by the aperture area that overlapped finite data; `r_in >= r_out` writes "inner sky radius must be smaller than outer" to the labels and returns.
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R18.

### Task 25: Arithmetic precision, integer range and result headers (R23, R69, M4)

**Files:**
- Modify: `pyql3/gui/tools/arithmetic.py:40-42`, `:97-99`, `:150-152`, `:188-199`, `pyql3/core/fits_reader.py:102-114` (`load_from_memory`), `pyql3/gui/main_window.py:723`
- Test: `tests/test_analysis_tools.py`, `tests/test_fits_reader.py`

- [ ] **Step 1: Write the failing tests.** `test_analysis_tools.py::test_arithmetic_keeps_precision_and_range` (R23: 0.0012 reads back; Active + Active of a uint16 40000 frame is 80000; the result header has no `BZERO`/`BSCALE` and `BITPIX = -64`). `test_fits_reader.py::test_load_from_memory_keeps_one_header` (`get_header() is hdul[0].header`; a Header Editor edit survives `save`). `test_analysis_tools.py::test_arithmetic_result_title_is_the_expression` (R69: `(a001.fits / a002.fits)` is the window title's file part).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** `setDecimals(10)` on both spin boxes; `astype(float)` on both array operands; `load_from_memory` keeps `hdu.header` as `self.header`; `load_from_memory`'s title is used as given.
- [ ] **Step 4: Run the tests; they pass.**
- [ ] **Step 5: Close-out** for R23, R69, M4 (prefix M4's Issue cell with `✅ FIXED —`).

### Task 26: Header Editor writes cards by index, and saves keep the file's scaling (R42, R71)

**Files:**
- Modify: `pyql3/gui/dialogs/header_editor.py:49-143`, `pyql3/core/fits_reader.py:67`, `:195-207`
- Test: `tests/test_fits_reader.py`

**Interfaces:**
- Produces: each table row remembers its card index; `apply_table_edits` writes back by index and only rows whose text changed; COMMENT and HISTORY rows are written back by index too; `Undefined` stays `Undefined` when unchanged; edits are held per extension in the dialog and applied to the reader only on Save. `FitsReader.load` opens with `scale_back=True`.

- [ ] **Step 1: Write the failing tests.** `::test_header_editor_round_trip_is_identity` and `::test_cancel_after_an_extension_switch_keeps_the_header` (R42), `::test_header_only_save_keeps_the_scaled_integer_encoding` (R71: BITPIX 16, BSCALE 0.5, BZERO 100, BLANK survive).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** S4's structural-keyword guard stays.
- [ ] **Step 4: Run the tests and `tests/test_data_integrity.py`; they pass.**
- [ ] **Step 5: Close-out** for R42 and R71.

### Task 27: One angle convention for all three fit models (R72)

**Files:**
- Modify: `pyql3/gui/tools/fitting.py:12-15`, `docs/tools.md` (the Peak Fit "Angle" and "FWHM X" description)
- Test: `tests/test_analysis_tools.py`

- [ ] **Step 1: Write the failing test.** `::test_fit_models_agree_on_the_angle`: one elongated source with its major axis 30° CCW from +X; the Gaussian, Lorentzian and Moffat angles agree modulo 180° within 1°.
- [ ] **Step 2: Run it and see it fail.**
- [ ] **Step 3: Implement:** flip the sign of the Gaussian cross term; state in `docs/tools.md` that the angle is CCW from +X and which width FWHM X is.
- [ ] **Step 4: Run it; it passes.**
- [ ] **Step 5: Close-out** for R72. **End of Phase 7: stop and report the staged diff.**

---

## Phase 8 — Plot Catalog details

### Task 28: An explicit pixel origin for catalog positions (R30)

**Files:**
- Modify: `pyql3/gui/tools/plot_catalog.py:48-51`, `:858-866` (now inside `_resolve_orig_positions`), the coordinate-type controls, `docs/tools.md`
- Test: `tests/test_plot_catalog.py`

**Interfaces:**
- Consumes: `_resolve_orig_positions` (Task 13).
- Produces: a *Pixel origin* control (`0-based (array)` / `1-based (FITS)`) beside the coordinate type, shown for pixel types; 1-based applies `coords.fits_to_index` to both columns. It defaults to 1-based when the chosen columns are SExtractor `*_IMAGE` names and to 0-based otherwise. Regions sent from the Region menu stay 0-based.
- The pixel-origin choice must be part of `_resolve_orig_positions`'s cache key (the `settings` tuple), or changing it returns the positions resolved under the other origin.

- [ ] **Step 1: Write the failing test.** `::test_sextractor_image_columns_are_one_based` (R30: the FITS-pixel and World markers for one row agree within 0.01 px).
- [ ] **Step 2: Run it and see it fail.**
- [ ] **Step 3: Implement**, and document the control in `docs/tools.md`.
- [ ] **Step 4: Run it and `tests/test_plot_catalog.py`; they pass.**
- [ ] **Step 5: Close-out** for R30.

### Task 29: Catalog column detection (R32, R62)

**Files:**
- Modify: `pyql3/gui/tools/plot_catalog.py:44-47`, `:761-808`
- Test: `tests/test_plot_catalog.py`

- [ ] **Step 1: Write the failing tests.** `::test_vizier_coordinate_names_select_world`, parametrized over `RAJ2000/DEJ2000`, `RA_ICRS/DE_ICRS`, `_RAJ2000/_DEJ2000` and `RAdeg/DEdeg` (R32). `::test_a_skycoord_column_does_not_break_the_column_guess` (R62).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** add `ra_icrs`, `_raj2000` to the RA names and `dej2000`, `_dej2000`, `de_icrs`, `dedeg` to the Dec names, after the existing ones; `getattr(column, 'dtype', None)` in `_numeric_column_indices`; `try`/`finally` around the blocked section of `update_columns_for_type`.
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Close-out** for R32 and R62.

### Task 30: The catalog's label-ceiling message (R75)

R50 (labels returning with *Show All* off) was closed in Task 13, whose `_plot_markers` empties the
label positions before its *Show All* early return; `test_labels_stay_hidden_with_show_all_off_after_a_pan`
was added there.

**Files:**
- Modify: `pyql3/gui/tools/plot_catalog.py:925-929`, `:955`, `:986-987`, `:1083-1102`
- Test: `tests/test_plot_catalog.py`

- [ ] **Step 1: Write the failing test.** `::test_the_label_ceiling_message_survives_a_style_change` (R75, with the limit lowered as the existing ceiling tests do).
- [ ] **Step 2: Run it and see it fail.**
- [ ] **Step 3: Implement:** after writing its status, `update_plot` re-applies the ceiling message while `self._label_guard.suppressing`.
- [ ] **Step 4: Run it; it passes.**
- [ ] **Step 5: Close-out** for R75. **End of Phase 8: stop and report the staged diff.**

---

## Phase 9 — Region GUI details

### Task 31: The properties dialog writes only what the user changed (R33)

**Files:**
- Modify: `pyql3/gui/dialogs/region_properties.py:44`, `:193-242`
- Test: `tests/test_region_properties.py`

**Interfaces:**
- Produces: `apply()` writes only fields whose widget value differs from the value shown when the dialog opened (or was last refreshed). The dialog listens to `layer.regions_changed`; when its region's geometry changed, it refreshes the geometry widgets the user has not edited and retakes the snapshot `reject()` restores.

- [ ] **Step 1: Write the failing tests.** `::test_a_drag_while_the_dialog_is_open_survives_ok` (R33), `::test_a_drag_while_the_dialog_is_open_survives_cancel`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run `tests/test_region_properties.py`; it passes.**
- [ ] **Step 5: Close-out** for R33.

### Task 32: Region List selection by identity, and a confirmed Delete All (R34, R76)

**Files:**
- Modify: `pyql3/gui/tools/region_list.py:98-113`, `:173-179`, `:251-259`
- Test: `tests/test_region_ui.py`

- [ ] **Step 1: Write the failing tests.** `::test_the_selection_follows_the_region_when_another_is_deleted` (R34). `::test_region_list_delete_all_asks_first` (R76: `QMessageBox.question` stubbed to No leaves every region; Yes clears them).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** `update_plot` records the selected regions by identity before the rebuild and reselects the rows holding the same objects; `delete_all()` calls the window's `delete_all_regions()`.
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Close-out** for R34 and R76.

### Task 33: Region labels: aggregate redraws, dragged captions, the labels switch (R35, R44, R51)

**Files:**
- Modify: `pyql3/gui/viewers/region_layer.py` (`_on_view_range_changed` `:652-672`, `_item_edited` `:916-967`, `_apply_visibility` `:601-618`, `_build_items` `:866`, `_restyle_in_place` `:1017`)
- Test: `tests/test_region_layer.py`

- [ ] **Step 1: Write the failing tests.** `::test_overlay_labels_appear_after_panning_from_an_empty_area`, `::test_overlay_labels_return_after_zooming_in_below_the_ceiling` (R35), `::test_a_caption_follows_its_dragged_shape` (R44, circle / box / arrow), `::test_new_and_restyled_regions_obey_the_labels_toggle`, `::test_a_text_region_stays_visible_with_labels_off` (R51).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** in aggregate mode restart the label timer whenever labels are switched on and any visible region has `text`; `_item_edited` ends by calling `_place_label` under `_placing`. For R51: the Phase 4 review already made new and restyled regions obey the labels switch through `RegionLayer._show_one` (`_apply_visibility` now takes `(entry, channel, label_shown)`); what remains is the text-region half — exempt an entry whose label is its handle (`entry.label is entry.handle`) from the label rule in both decision points, the `labelled`/`show` computation in `update_channel_visibility` (which also counts text regions against the label ceiling) and `_show_one`, so a text region stays visible with labels off in both render modes. `::test_new_and_restyled_regions_obey_the_labels_toggle` passes already (a regression guard; Step 2 does not apply to it); `::test_a_text_region_stays_visible_with_labels_off` must fail first.
- [ ] **Step 4: Run the region test files; they pass.** M18 and M30 tests must still pass.
- [ ] **Step 5: Close-out** for R35, R44, R51.

### Task 34: Region drawing mode can be cancelled (R46)

**Files:**
- Modify: `pyql3/gui/viewers/region_layer.py:1030-1063`, `pyql3/gui/region_toolbar.py:131-140`, `pyql3/gui/main_window.py` (an Escape `QShortcut` on the viewer)
- Test: `tests/test_region_toolbar.py`, `tests/test_region_ui.py`

- [ ] **Step 1: Write the failing tests.** `test_region_toolbar.py::test_clicking_the_pressed_shape_again_disarms_it` and `test_region_ui.py::test_escape_cancels_drawing` (R46: `draw_kind is None`, `mouseEnabled == [True, True]`).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** toolbar group `ExclusionPolicy.ExclusiveOptional`, with `_start()` cancelling when the clicked kind is armed; an Escape shortcut on the viewer calling `region_layer.cancel_draw()`.
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Close-out** for R46. **End of Phase 9: stop and report the staged diff.**

---

## Phase 10 — Region model and ds9 I/O

### Task 35: The WCS's own celestial frame, everywhere (R10, R17)

**Files:**
- Modify: `pyql3/core/sky.py`, `pyql3/core/regions_model.py:134-180` (`SkyAnchor`), `pyql3/core/regions_io.py:185-245`, `:302-320`, `pyql3/core/ds9_regions.py:198-203`, `:409-413`, `pyql3/gui/main_window.py:1023-1032`, `main.py:132`, `AGENTS.md` (the Regions note on the `SkyAnchor`)
- Test: `tests/test_region_ui.py`, `tests/test_ds9_regions.py`, `tests/test_regions_model.py`

**Interfaces:**
- Produces: `CelestialMap.frame` — `astropy.wcs.utils.wcs_to_celestial_frame(self.wcs2d)`, or `None` when unusable. `CelestialMap.ds9_frame -> str | None` — its ds9 name, from the inverse of `ds9_regions._SKY_FRAMES` (`icrs`, `fk5`, `fk4`, `galactic`, `ecliptic`). `CelestialMap.skycoord(lon_deg, lat_deg) -> SkyCoord` in that frame.
- Produces: `SkyAnchor.frame: str | None` — the ds9 frame name the anchor's numbers are in; written in the YAML `sky` block, read back; absent means "the loading WCS's frame".
- `sky_anchor_for` records `mapping.ds9_frame`; `_from_sky_anchor` builds the anchor's `SkyCoord` in its own frame and places it with `from_skycoord`; angle conversion receives the `SkyCoord`. `placed_on_image` catches conversion exceptions and counts the region as stranded. The ds9 writer's arrow-only header line is `mapping.ds9_frame`. `MainWindow.load_regions_from` and the `--regions` startup path catch any exception from the loader into the existing error dialog / stderr message.

- [ ] **Step 1: Write the failing tests.** `test_region_ui.py::test_a_file_saved_on_a_galactic_image_loads_back`, `::test_an_fk4_image_keeps_a_box_angle`, `::test_an_fk5_anchor_lands_on_the_same_star_on_an_icrs_image` (R10). `test_ds9_regions.py::test_an_arrow_only_sky_export_declares_the_wcs_frame` (R17, ICRS / FK5 / FK4 / galactic). `test_regions_model.py::test_sky_anchor_frame_round_trips`. `test_region_ui.py::test_real_osiris_cube_has_an_fk5_frame(real_osiris_fits)` (Review Focus 3; skips without the cube).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the region test files; they pass.** M20 and M21 tests must still pass.
- [ ] **Step 5: Close-out** for R10 and R17.

### Task 36: Directions mapped as directions (R37, R38)

**Files:**
- Modify: `pyql3/core/sky.py`, `pyql3/core/ds9_regions.py:304-314`, `:371-413`, `:654`, `:770-774`, `pyql3/core/regions_io.py:226-245`, `:308-317`
- Test: `tests/test_ds9_regions.py`, `tests/test_coords.py`

**Interfaces:**
- Consumes: `CelestialMap.frame`, `skycoord` (Task 35).
- Produces: `CelestialMap.direction_to_sky(x, y, angle_deg) -> float` — the ds9 sky angle of a direction leaving orig `(x, y)` at `angle_deg` CCW from +X: `to_sky` of `(x, y)` and of a point 0.01 px along the direction, then `position_angle + 90°` (mod 360). `CelestialMap.direction_from_sky(coord, sky_angle_deg) -> float` — the inverse, via `coord.directional_offset_by(sky_angle_deg - 90°, small step)` and `from_skycoord`, then `atan2` in orig coordinates. `CelestialMap.length_to_sky(x, y, angle_deg, pixels) -> Angle` and `length_from_sky(coord, sky_angle_deg, angle) -> float` by the same step method. Both directions work in orig coordinates, so the swapped display needs no special case for arrows and anchor angles.
- Box geometry handed to the `regions` library for ds9 output converts its angle with `CelestialMap.to_wcs_angle(theta) -> float` (`(90 - theta) % 360` when `swapped`, else `theta`) and back with `from_wcs_angle`; width and height are unchanged (R38's correction).
- `pixel_angle_to_sky` / `sky_angle_to_pixel` become wrappers over the direction methods, or are removed with their callers updated.

- [ ] **Step 1: Write the failing tests.** `test_ds9_regions.py::test_an_arrow_points_the_same_way_on_the_sky_with_non_square_pixels` (R37, 1:2 WCS), `::test_a_swapped_display_writes_the_same_sky_regions` (R38). `test_coords.py::test_direction_round_trip_on_a_rotated_non_square_wcs` (0.01° and 1e-3 px over 0–360° in 15° steps).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the region test files; they pass.** The ds9-verified rotated-field behaviour (`check_rotated.fits` in the AGENTS.md ladder) must be unchanged on square pixels: `test_a_rotated_field_turns_a_sky_anchored_box_with_it` still passes.
- [ ] **Step 5: Close-out** for R37 and R38. Re-run `agent_tests/probes/make_ds9_check_ladder.py` and note in the task report that the ladder should be re-checked in ds9 by hand.

### Task 37: Anchor bookkeeping: angle wrap, per-region frames, stale anchors, the style block (R39, R40, R41, R77)

**Files:**
- Modify: `pyql3/core/regions_io.py:128-299`, `pyql3/core/regions_model.py` (`Region.frame` default), `pyql3/gui/viewers/region_layer.py` (geometry edits mark the anchor stale), `pyql3/gui/main_window.py:1035` (keep the loaded style)
- Test: `tests/test_region_ui.py`, `tests/test_regions_model.py`

**Interfaces:**
- `_same_geometry` compares `angle` fields with `abs((a - b + 180) % 360 - 180) <= SAME_PLACE`.
- `Region.frame` defaults to `None` ("not chosen"); `with_sky_anchors` turns `None` into `"sky"` when it records an anchor and keeps an explicit `"image"`. `placed_on_image`, with no hook, places each candidate by its own frame and asks the question only about `frame: sky` regions.
- Produces: `Region.anchor_geometry: tuple | None` (not serialised) — the geometry the stored anchor was paired with, set on load and on save. `with_sky_anchors` keeps a stored anchor that it cannot recompute only when the region's geometry still equals `anchor_geometry`; otherwise it drops the anchor and writes `frame: image`.
- Produces: `RegionLayer.style` — the loaded file's `style:` block, passed to `save_regions` as `RegionList.style`.

- [ ] **Step 1: Write the failing tests.** `test_region_ui.py::test_an_angle_zero_box_is_not_questioned_on_its_own_image` (R39, fields rotated −111°, −91°, −89°), `::test_a_frame_image_region_keeps_its_pixels_in_a_mixed_file`, `::test_resaving_keeps_frame_image` (R40), `::test_a_region_moved_on_an_image_without_a_wcs_keeps_its_new_pixels` (R41), `::test_resaving_keeps_the_style_block` (R77). The existing `test_a_sky_anchor_is_kept_when_this_image_cannot_make_a_new_one` must still pass.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run the region test files; they pass.**
- [ ] **Step 5: Close-out** for R39, R40, R41, R77.

### Task 38: The ds9 reader and writer for vectors, globals, headerless files and newlines (R47, R64, R78, R79)

**Files:**
- Modify: `pyql3/core/ds9_regions.py:63-77` (`_FRAME_RE`), `:330-360`, `:426-428`, `:578-661`, `:730-754`, `pyql3/core/regions_model.py:458-472` (`looks_like_ds9`)
- Test: `tests/test_ds9_regions.py`, `tests/test_regions_model.py`

- [ ] **Step 1: Write the failing tests.** `::test_a_vector_follows_a_frame_given_on_a_statement_line` (three forms), `::test_a_detector_frame_arrow_is_refused` (R47); `::test_a_vector_takes_the_global_line_like_a_shape`, `::test_an_exported_arrow_states_its_width` (R64); `test_regions_model.py::test_headerless_ds9_files_are_recognised`, parametrized over `fk5;circle(...)`, a `j2000` line and bare `circle(...)` (R78); `::test_a_newline_in_a_label_does_not_split_the_line` (R79: the file reads back with one region per label and the Report mentions the change).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** `_extract_vectors` splits on `;`, drops a trailing `#` comment that is not a `# vector(` / `# text(` statement, updates the frame from any frame-word segment, tracks the latest `global` line as the attribute base; `detector`, `amplifier`, `linear` join `_FRAME_RE` and are refused; image-frame arrows count toward the axis note; vector lines always carry `width=`; `looks_like_ds9` accepts any `_FRAME_RE` word, also inside `;`-separated statements, and a shape call in text that is not a YAML mapping; newlines and carriage returns in labels become spaces before writing.
- [ ] **Step 4: Run the ds9 and model tests; they pass.** Every ds9-verified rule in AGENTS.md ("ds9 `.reg` interop") must still hold.
- [ ] **Step 5: Close-out** for R47, R64, R78, R79.

### Task 39: ds9 conversion reports what it cannot carry (R48)

**Files:**
- Modify: `pyql3/core/ds9_regions.py:248-289`, `:317-384`, `:846-873`
- Test: `tests/test_ds9_regions.py`

- [ ] **Step 1: Write the failing tests.** `::test_attributes_ds9_cannot_carry_are_reported` (channel range, hidden region, label font size on a shape — each in `report.skipped`; a rotated label's `tag=` is written), `::test_an_excluded_region_is_reported`.
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Exclusion: report each `include = 0` region by index in `report.skipped` and import it as ordinary.
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Close-out** for R48. **End of Phase 10: stop and report the staged diff.**

---

## Phase 11 — Application-level items

### Task 40: Exit closes every window; Recent Files is shared properly (R49, R58)

**Files:**
- Modify: `pyql3/gui/main_window.py:139-146`, `:639-678`, `pyql3/services/config.py:83-111`
- Test: `tests/test_multi_window.py`, `tests/test_config.py`

**Interfaces:**
- Exit is connected to `QApplication.closeAllWindows()`.
- `ConfigManager.set`, `add_recent_file`, `remove_recent_file` and `clear_recent_files` re-read the file, apply the change to the fresh data, and save. `recent_menu.aboutToShow` rebuilds the menu.

- [ ] **Step 1: Write the failing tests.** `test_multi_window.py::test_exit_closes_every_window` (R49), `::test_recent_files_menu_is_current_in_every_window`; `test_config.py::test_two_managers_do_not_drop_each_others_recent_files` (R58).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** S1's quarantine of a corrupt file must still apply on every re-read.
- [ ] **Step 4: Run them and `tests/test_config.py`; they pass.**
- [ ] **Step 5: Close-out** for R49 and R58.

### Task 41: Files from Finder and `--poll-dir` (R59)

**Files:**
- Modify: `pyql3/gui/window_manager.py:121-133`, `main.py:84-91`
- Test: `tests/test_file_open.py`, `tests/test_main_window_and_poller.py`

**Interfaces:**
- `WindowManager.open_path` sends a path to a new window when the target window already received a path in the same event-loop pass (a flag cleared by `QTimer.singleShot(0, ...)`). `main.py` picks the initial `--poll-dir` file as the newest `os.listdir` entry for which `is_fits_path()` is true, and prints `pyql3: no directory at <path>` to stderr when the path is not a directory.

- [ ] **Step 1: Write the failing tests.** `test_file_open.py::test_several_finder_files_open_in_separate_windows`, `test_main_window_and_poller.py::test_poll_dir_opens_the_newest_compressed_frame`, `::test_a_missing_poll_dir_says_so` (R59).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.**
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Close-out** for R59.

### Task 42: The command-line installer refuses up front (R73, R74)

**Files:**
- Modify: `pyql3/services/cli_install.py:61-76`, `:196-259`
- Test: `tests/test_cli_install.py` (behind its existing module-level skip)

- [ ] **Step 1: Write the failing tests.** `::test_plan_refuses_a_foreign_launcher` (R73: `plan()` raises `CliInstallError` and no confirmation is built). `::test_plan_refuses_a_translocated_bundle` (R74: `macos_app_bundle` returns a path containing `/AppTranslocation/`; `plan()` raises with the "copy it to /Applications first" message).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement.** Keep `plan()` side-effect-free.
- [ ] **Step 4: Run `tests/test_cli_install.py`; it passes.**
- [ ] **Step 5: Close-out** for R73 and R74.

### Task 43: Colorbar values, 1:1 zoom, and unparsable Z fields (R65, R66, R84)

**Files:**
- Modify: `pyql3/gui/viewers/image_viewer.py:834-854`, `:1784-1794`, `apply_z_range` and the `txt_zmin` / `txt_zmax` / `txt_boxcar` construction
- Test: `tests/test_image_viewer.py`

- [ ] **Step 1: Write the failing tests.** `::test_colorbar_ticks_are_data_values_under_sqrt` (R65: with data 25–9825 under Sqrt, the colorbar's range is 25–9825 or its label names the scale). `::test_one_to_one_zoom_shows_one_data_pixel_per_screen_pixel` (R66: after 1:1 on a 300×200 image in a 900×900 view, `viewRect().width()` equals the ViewBox's width in screen pixels within 1). `::test_an_unparsable_z_field_does_not_leave_a_stale_plane` (R84: Z Range mode, Sum over 4-8, Z Min set to "abc" and the cube reloaded ×10; the screen must not show channel 0 at the old levels — either "abc" cannot be entered, or the full range is collapsed with a message — and `display_data` equals what is painted).
- [ ] **Step 2: Run them and see them fail.**
- [ ] **Step 3: Implement:** map the colorbar's ticks back through the inverse of the active scale (keep the label "Pixel Value (<unit>)"); set the 1:1 range from the ViewBox's screen size, centred on the current view centre; put a `QIntValidator` on Z Min, Z Max and Boxcar, or fall back to the full range with a message (R84).
- [ ] **Step 4: Run them; they pass.**
- [ ] **Step 5: Close-out** for R65, R66 and R84. **End of Phase 11: stop and report the staged diff.** All of R1–R89 should now be ticked in the checklist.

# Bug List — code review of 2026-07-28

Findings from a full read of `pyql3/` (~2,700 lines). Every item below was reproduced
headlessly with `QT_QPA_PLATFORM=offscreen`. The existing suite passes
(`27 passed, 1 skipped`), so none of these are covered by a test today.

Line numbers refer to the working tree at the time of review (commit `9b3624c`).

Suggested order of work: **B1 → B2 → B3 → B4 → B5 → B6 → B7 → B8 → B9**, then the
minor items. B1–B4 are hit during normal interactive use; B5/B6 corrupt viewer state.

---

## B0. Depth Plot / Gaussian Fit crash when opened from the menu bar

- **Status:** ✅ FIXED — `base_tool.py`, `main_window.py`, `depth_plot.py`, `fitting.py`,
  covered by `tests/test_menu_actions.py`
- **Severity:** high (two menu entries do nothing at all)
- **Pre-existing at HEAD `9b3624c`** — verified by firing the real menu actions against the
  unmodified tree; not introduced by the B1/B2/B10 work.
- **Files:** `pyql3/gui/main_window.py:174` and `:200` (the connections),
  `pyql3/gui/tools/depth_plot.py:290`, `pyql3/gui/tools/fitting.py:166`

### Symptom
**Plot → Depth Plot** and **Analysis → Gaussian Fit** never open. Qt catches the
exception in the slot, prints a traceback to the console and carries on, so the app
survives but the dialog silently never appears:
```
File "pyql3/gui/main_window.py", line 495, in open_depth_plot
    self._depth_plot_dialog = DepthPlotDialog(self, self.image_viewer, initial_center=initial_center)
File "pyql3/gui/tools/depth_plot.py", line 290, in __init__
    center_x, center_y = initial_center
TypeError: cannot unpack non-iterable bool object
```
The right-click context-menu route works, because that path emits a real `(x, y)` tuple.

### Root cause
`QAction.triggered` is declared `triggered(bool checked = false)`. PySide6 registers both
the `()` and `(bool)` signatures and picks the one matching the slot's arity — so a slot
declared `open_depth_plot(self, initial_center=None)` receives `checked=False`, not
`None`. `False is not None`, so the guard in both dialog constructors passes and the
unpack fails.

Verified directly:
```python
def slot(initial_center=None): seen.append(initial_center)
action.triggered.connect(slot); action.trigger()
# seen == [False]   type: bool
```
Openers that take no argument (`open_statistics`, `open_photometry`, …) are unaffected:
PySide6 then selects the zero-argument signature.

### Fix
Three layers, all applied:
1. `base_tool.as_center(value)` — single shared coercion returning `None` for `None`,
   any `bool`, or anything that is not a 2-element pair.
2. `main_window.open_depth_plot` / `open_gaussian_fit` normalise their argument through
   it, so *any* caller (menu, signal, future connection) is safe.
3. The two menu connections no longer leak the flag at all:
   `triggered.connect(lambda checked=False: self.open_depth_plot())`.
   Both dialog constructors and both `set_center` methods also coerce defensively.

### Why the B1/B2/B10 tests missed it
They constructed the dialogs directly (`DepthPlotDialog(None, viewer)`) and called
`open_depth_plot((10, 10))` with an explicit tuple — never through a `QAction`, which is
the only place the bool originates. `tests/test_menu_actions.py` now covers that path for
every tool opener, and pins the Qt behaviour itself in
`test_qaction_triggered_delivers_bool_to_one_arg_slot`.

### Testing notes learned here (worth keeping)
- `MainWindow` keeps **no Python reference** to its `QMenu` objects (they are locals in
  `create_menus`). Calling `menu_action.menu()` from a test creates a transient Python
  owner and PySide6 then deletes the C++ object — `RuntimeError: Internal C++ object
  already deleted`. Tests must not walk the menu bar; retain the menus first (wrap
  `addMenu`) or connect the bound methods to their own `QAction`s. See **M11**.
- `edit_header` and `open_polling_config` call `dialog.exec()`. A modal event loop hangs a
  headless run **forever**, and `signal.alarm` cannot break it (Python signal handlers
  cannot run while blocked inside Qt's C++ event loop). Patching `MainWindow.edit_header`
  afterwards does not help either: `create_menus` connected the *bound method* at
  construction time. Any sweep must exclude these two.

---

## B1. Diagonal Cut spinboxes raise `TypeError` and permanently freeze the plot

- **Status:** ✅ FIXED (with B10) — `cuts.py`, covered by `tests/test_cuts.py`
- **Severity:** high (crash + dead UI)
- **File:** `pyql3/gui/tools/cuts.py:108-119`

### Symptom
In **Plot → Diagonal Cut**, typing into any of the Point 1 / Point 2 coordinate
spinboxes throws a `TypeError` on the console, the line does not move, and from that
moment on *dragging* the ROI no longer updates the profile either. The dialog is dead
until reopened.

### Root cause
```python
handles[0].setPos(positions[0])          # cuts.py:115 — positions[0] is [x, y]
```
PySide6's `QGraphicsItem.setPos` accepts `QPointF`/`QPoint` or `(float, float)` — not a
Python list. The exception escapes `on_spin_changed` *before* line 118, so
`self.roi.blockSignals(False)` never executes and the ROI's signals stay blocked for the
lifetime of the dialog.

### Reproduction
```python
d = CutPlotDialog('diagonal', None, viewer)
d.spin_x0.setValue(10); d.on_spin_changed()
# TypeError: 'QGraphicsItem.setPos' called with wrong argument types: setPos(list)
d.roi.signalsBlocked()   # -> True   (stays True forever)
```

### Fix
Unpack the coordinates and make the unblock exception-safe:
```python
def on_spin_changed(self):
    if self._updating_spins or self.roi is None:
        return

    self.roi.blockSignals(True)
    try:
        if self.cut_type == 'horizontal':
            y0, y1 = self.spin_y0.value(), self.spin_y1.value()
            self.roi.setRegion([min(y0, y1), max(y0, y1)])
        elif self.cut_type == 'vertical':
            x0, x1 = self.spin_x0.value(), self.spin_x1.value()
            self.roi.setRegion([min(x0, x1), max(x0, x1)])
        elif self.cut_type == 'diagonal':
            self.set_diagonal_endpoints(
                (self.spin_x0.value(), self.spin_y0.value()),
                (self.spin_x1.value(), self.spin_y1.value()),
                width=self.spin_w.value(),
            )
    finally:
        self.roi.blockSignals(False)
    self.update_plot()
```
`set_diagonal_endpoints` is new — see **B10**, which has to be solved at the same time
because moving a `LineROI` by its handles is not the right primitive anyway. The minimal
standalone fix for the crash alone is `handles[0].setPos(*positions[0])`.

### Regression test
`tests/test_cuts.py`: build a `CutPlotDialog('diagonal', ...)`, set each spinbox, assert
no exception, assert `roi.signalsBlocked() is False`, and assert the plotted profile
changed.

---

## B2. Depth Plot background subtraction is wired up inside `set_center()`

- **Status:** ✅ FIXED — `depth_plot.py`, `main_window.py`, covered by `tests/test_depth_plot.py`
- **Severity:** high (feature silently does nothing on the main code path)
- **File:** `pyql3/gui/tools/depth_plot.py:308-323`

### Symptom
1. Opened via **Plot → Depth Plot**: ticking *Enable Background Subtraction* does
   nothing at all — no orange ROI appears, the background spinboxes and *Calc using*
   combo stay greyed out.
2. Opened via **right-click → Depth Plot...**: it works, but the signal connections are
   remade on every `set_center` call, so handlers accumulate and one checkbox click runs
   `toggle_background` two or more times.

### Root cause
An indentation slip put the tail of `__init__` inside `set_center`:
```python
def set_center(self, center):
    if center is None or self.roi is None:
        return                      # <-- early return skips everything below
    ...
    self.on_roi_changed()

    self.chk_enable_bg.stateChanged.connect(self.toggle_background)   # 317
    self.combo_bg_calc.currentIndexChanged.connect(self.update_plot)  # 318
    self._updating_spins = False
    self._updating_range_spins = False
    self.update_plot()                                               # 323
```
`main_window.open_depth_plot` only calls `set_center` when `initial_center is not None`,
and then calls it a *second* time after `show()` (`main_window.py:495` and `:499`).

### Reproduction
```python
d = DepthPlotDialog(None, viewer)        # no initial_center
d.chk_enable_bg.setChecked(True)
d.bg_roi                                  # -> None
d.spin_bg_x0.isEnabled()                  # -> False
```

### Fix
Move lines 317-323 to the end of `__init__` (after `self.bg_roi = None` on line 306) and
leave `set_center` as just the reposition + `on_roi_changed()`. Note the ordering
constraint: `self.bg_roi = None` must be assigned **before** the first `update_plot()`,
because `update_plot` reads `self.bg_roi` at line 925. Today it survives only because
`chk_enable_bg.isChecked()` short-circuits first — make that robust by initialising
`self.bg_roi = None` near the top of `__init__` instead.

While here, drop the redundant second `set_center` call in
`main_window.open_depth_plot:498-499` (the constructor already honours
`initial_center`); it is only needed when reusing an already-open dialog.

### Regression test
Construct the dialog both ways; assert `bg_roi` becomes a ROI on check and `None` on
uncheck, and that `plot_bg`/`plot_sub` receive data.

---

## B3. HistEq scaling renders a blank image for integer FITS data

- **Status:** ✅ FIXED — `image_viewer.py:1112-1141`, covered by `tests/test_image_viewer.py`
- **Severity:** high (blank display, looks like data loss)
- **File:** `pyql3/gui/viewers/image_viewer.py:1123-1132`

### Symptom
With **Display → Scaling → HistEq** (or the Scale combo) on any integer-typed FITS image
— i.e. `BITPIX = 16 / 32`, extremely common for raw frames — the image goes uniformly
flat. Float data is fine.

### Root cause
```python
render_data = np.zeros_like(self.display_data)                       # 1130 — int dtype
render_data[valid_mask] = np.searchsorted(...) / float(len(sorted_flat))
```
`zeros_like` inherits the integer dtype, so the normalized `[0, 1)` values truncate to 0
on assignment.

### Reproduction
```python
data = (np.arange(64*32, dtype=np.int32) % 500).reshape(64, 32)
viewer.set_data(data); viewer.combo_scale.setCurrentText("HistEq")
viewer.update_image_display()
viewer.imv.getImageItem().image.max()     # -> 0   (float32 input -> 0.998)
```

### Fix
Applied as the single cast at the top of the scaling block, which fixes HistEq *and* the
`Negative` unsigned wraparound at once. `float32` input is not needlessly widened to
`float64` (`np.result_type` keeps `int16`/`uint16`/`float32` at `float32`, promotes
`int32`/`int64` to `float64`), which matters for large cubes:
```python
base = self.display_data
if not np.issubdtype(base.dtype, np.floating):
    base = base.astype(np.result_type(base.dtype, np.float32), copy=False)
```
All six branches now read `base` instead of `self.display_data`. `Logarithmic`, `Sqrt`
and `AsinH` were already safe (the ufuncs promote), but routing them through `base` keeps
one definition of the render dtype.

Two related things found while fixing it:
- The HistEq percentile sample was assigned back over `valid_data`
  (`valid_data = np.random.choice(valid_data, 50000)`) and *then* used as the
  `searchsorted` haystack **and** its needles. For images above 50 000 valid pixels the
  needles were the 50 000-element sample rather than the full image, so `render_data`
  was assigned a wrongly-shaped array. Now kept as a separate `sample` variable.
- `float(len(sorted_flat))` is a divide-by-zero for an all-NaN plane; guarded with
  `if sorted_flat.size > 0`.

### Regression test
`tests/test_image_viewer.py` — parametrized over `int16`/`int32`/`uint16`/`float32`/
`float64` × all six scale modes (30 cases), asserting the rendered image is a floating
dtype and `max > min`; plus `test_negative_scale_does_not_wrap_for_unsigned` and
`test_histeq_handles_nans_without_dividing_by_zero`.

---

## B4. Dragging the timeline desyncs the slice slider, labels and pixel readout

- **Status:** ✅ FIXED — `image_viewer.py` (`_syncing_slice`, `on_imv_time_changed`,
  `current_z`, `update_image_display`), `depth_plot.py:1051`/`:1076`, covered by
  `tests/test_image_viewer.py`
- **Severity:** high (silently reports the wrong pixel value / wavelength)
- **Files:** `pyql3/gui/viewers/image_viewer.py:129-130`, `:147-192`, `:1194`, `:1240`

### Symptom
Dragging the yellow vertical line in the timeline strip under the image (the natural way
to page through a cube in pyqtgraph) changes the displayed slice, but:
- the *Z Slice* slider and its number label stay where they were,
- the `Slice: N` / wavelength label stays stale,
- the `Value:` readout and the WCS RA/Dec/λ readout report values from the **old**
  slice,
- every analysis tool (`statistics`, `photometry`, `fitting`, `cuts`,
  `advanced_plots`) uses `imv.currentIndex` and therefore disagrees with what the
  main window says.

### Root cause
`imv.timeLine.sigPositionChanged` is connected only to `update_slice_info`
(`image_viewer.py:130`), and `update_slice_info`/`mouse_moved` both read
`self.slider_slice.value()` rather than the actual displayed index. Nothing writes the
timeline position back to the slider. (`on_roi_plot_clicked` handles *clicks* on the
groove, so only dragging the handle is affected.)

### Reproduction
```python
viewer.set_data(cube)                     # (10, 12, 14)
viewer.imv.setCurrentIndex(7)             # == dragging the timeline
viewer.slider_slice.value()               # -> 0
viewer.lbl_slice_info.text()              # -> "Slice: 0"
# readout would report display_data[0, 3, 4] = -1.4448
# displayed pixel is actually display_data[7, 3, 4] = -0.8448
```

### Fix
Make the slider the single source of truth by syncing it from the ImageView, with a
re-entrancy guard (`on_slider_changed` already calls `imv.setCurrentIndex`):
```python
# __init__
self._syncing_slice = False
self.imv.sigTimeChanged.connect(self.on_imv_time_changed)

def on_imv_time_changed(self, ind, _time=None):
    if self._syncing_slice or self.transposed_data is None:
        return
    if self.transposed_data.ndim != 3 or not self.radio_slice.isChecked():
        return
    self._syncing_slice = True
    try:
        if self.slider_slice.value() != int(ind):
            self.slider_slice.setValue(int(ind))   # drives label + readout + tools
    finally:
        self._syncing_slice = False
```
and guard the reverse direction in `on_slider_changed` around `imv.setCurrentIndex`.

The sync version was applied. Three things came out of implementing it:

**1. `imv.setImage()` emits a spurious `sigTimeChanged(0)`.** Measured on the real
ImageView:
```
update_image_display(set_index=7)  ->  sigTimeChanged emitted [0, 7]
```
`setImage` resets the ImageView to index 0 *before* our `setCurrentIndex(7)` lands. A
naive `sigTimeChanged` → `slider.setValue()` connection therefore yanks the slider to 0 on
every redisplay. `_syncing_slice` is held for the whole `setImage`/`setCurrentIndex` block
in `update_image_display` and around `imv.setCurrentIndex` in `on_slider_changed`, so only
genuine user interaction reaches the slider. The flag is saved/restored (`prev_sync`)
rather than set to `False`, since these paths nest.

**2. The same desync exists in the reverse direction, and it is worse.** Because `setImage`
resets to 0 and nothing re-asserted the slider, *reloading a file* — i.e. the polling
auto-load path, the main live-observing workflow — displayed slice 0 while the slider, the
labels and every tool still said 7:
```python
viewer.slider_slice.setValue(7)      # imv 7, slider 7
viewer.set_data(cube + 1)            # imv 0, slider 7   <- display silently jumped
```
`set_data` deliberately preserves the slider value (it only clamps when the new cube is
shorter, `:823-825`), so the display was contradicting the code's own intent.
`update_image_display` now defaults `set_index` to the slider value for a 3D cube in slice
mode, which re-asserts the intended slice after every `setImage`.

**3. `imv.currentIndex` is unusable as a z index once a boxcar/range is drawn.** Those
paths render through `bypass_imv=True`, which never touches the ImageView's index, so it
stays at whatever the last single-slice display left:
```
boxcar 5, slider 9   ->  imv.currentIndex 2   (stale)
collapse 3-6         ->  imv.currentIndex 2   (stale)
```
There are **nine** such call sites. Seven are safe by accident — `advanced_plots:40`/`:145`,
`cuts:226`, `fitting:239`, `photometry:116`, `statistics:48` and `strehl:73` only index
when `display_data.ndim == 3`, and `display_data` is already a 2-D collapsed plane in
boxcar/collapse mode, so they analyse exactly what is on screen (verified: with channels
4-8 summed, the Statistics dialog reports 30.00 for a cube whose channel *k* holds the
constant *k*). The two Depth Plot cut branches (`depth_plot.py:1051`, `:1076`) index
`transposed_data`, which stays 3-D, so they *were* reading the stale index — both now call
the new `ImageViewer.current_z()`, which returns the slider value in slice/boxcar mode and
the middle of the range in collapse mode (matching what the WCS readout already reports),
clamped to the cube. That makes them deterministic, but see **B17**: cutting *any* single
channel is still the wrong data when a collapse is displayed.

### Regression test
`tests/test_image_viewer.py`, 7 tests: timeline drag → slider/labels/`current_z`; the
slider → timeline direction still works and does not recurse; reload preserves the
displayed slice; a shorter cube leaves the two agreeing; a simulated hover reports
`display_data[k, x, y]`; `current_z()` across slice/boxcar/range including reversed range
input; and a stray `sigTimeChanged` in range mode must *not* move the slider. Six of the
seven fail at the pre-fix tree.

Verified on the real cube (`s150531_a025002_Kn5_035.fits`, 465 slices): a drag to a
clamped index reports `Slice: 464, Wavelength: 2.4080 µm` with slider and `current_z()` in
agreement, and 300 alternating drag/slider/redisplay operations under
`setrecursionlimit(200)` leave `imv.currentIndex == slider_slice.value()` with no runaway
signal loop.

---

## B5. Reloading the same path serves stale cached data

- **Status:** ✅ FIXED (with B6) — `fits_reader.py` (`load`, `_stat_signature`),
  `main_window.py` (`load_fits`/`redisplay_image` gained `force`), covered by
  `tests/test_fits_reader.py` and `tests/test_main_window_and_poller.py`
- **Severity:** high for live observing (shows an old frame as if it were new)
- **File:** `pyql3/core/fits_reader.py:16-27`

### Symptom
**Display → Redisplay image** — and any polling event for a filename that already
loaded — redisplays the *previous* contents of the file. An instrument or DRP that
rewrites the same path produces a viewer that never updates.

### Root cause
```python
if self.filepath != filepath and self.hdul is not None:
    self.close()
...
if self.hdul is None:
    self.hdul = fits.open(filepath)
```
Same path ⇒ no close ⇒ the cached `HDUList` (and its memory map) is reused.

### Reproduction
```python
r = FitsReader(path)                     # mean 10.0
fits.PrimaryHDU(data + 1000).writeto(path, overwrite=True)
r.load(path)
r.get_data().mean()                      # -> 10.0   (on disk: 1010.0)
```

### Fix
The entry's fallback suggestion — key the cache on the file's stat signature — was taken
rather than "always reopen", because unconditional reopening has a real cost:
`on_extension_changed` routes through `load()`, so switching extensions would silently
discard header edits that the Header Editor has made but not saved yet.

```python
signature = self._stat_signature(filepath)         # (st_mtime_ns, st_size, st_ino)
stale = (self.hdul is None or self.filepath != filepath
         or signature is None or signature != self._file_signature)
if force or stale:
    self.close()
    self.hdul = fits.open(filepath, memmap=False)
    self._file_signature = signature
```

- `st_mtime_ns` (not `st_mtime`) so a rewrite inside the same second is still seen; plus
  `st_size` and `st_ino` so a size change or an atomic replace is caught even if the
  timestamp somehow matches.
- `memmap=False` as the entry says: data read through a mapping of a file the DRP is
  rewriting under us is undefined (and can `SIGBUS` if the file shrinks), and
  `writeto()` back to the same path can fail while the mapping is open. A test now pins
  the same-path `save()`.
- `force=True` added for an explicit re-read; **Display → Redisplay image** passes it, so
  that menu entry is a guaranteed reload rather than "reload if the stat changed".

Because `memmap` is off, the extension-discovery loop must no longer probe `hdu.data` —
that would pull *every* extension of a multi-extension file into memory. It now tests the
header only, which is also what B6 needed. A test asserts that after a load, exactly one
HDU has `_data_loaded` set.

Measured on the real cube (14.1 MB, 465 channels): first open + read 0.005 s, unchanged
reload 0.0001 s (cache hit), forced reopen 0.002 s, extension discovery 0.00002 s.

### Precedence, deliberately chosen
If the file changed on disk *and* there are unsaved header edits, the reload wins and the
edits are dropped — they describe data that is no longer there. Pinned by
`test_rewritten_file_wins_over_pending_header_edits`.

### Regression test
`tests/test_fits_reader.py`: rewrite-in-place is picked up; a size change is picked up; an
unchanged file reuses the handle; `force` reopens anyway; header edits survive an extension
switch; a rewrite discards them; same-path `save()` works. `tests/test_main_window_and_poller.py`
covers the GUI routes — the poller's `load_fits` and `redisplay_image`.

---

## B6. Extension dropdown lists non-image HDUs

- **Severity:** medium (silent state corruption)
- **File:** `pyql3/core/fits_reader.py:80-87`

### Symptom
For a file with a table extension (e.g. a `WAVETAB`/`BINTABLE`), the *Extension* combo
offers it. Selecting it leaves the previously displayed image on screen with no error,
while `fits_reader.data`/`header` switch to the table. **Save FITS As...**, the Header
Editor and Arithmetic then operate on an extension the viewer is not showing.

### Root cause
`get_image_extensions()` — the method `main_window.load_fits:316` uses to populate the
combo — checks only `hdu.data is not None`, omitting the `hdu.is_image` test that
`load()` itself applies at line 32. `ImageViewer.set_data` then falls through both the
`ndim == 2` and `ndim == 3` branches (a `FITS_rec` is 1-D) and returns silently.

### Reproduction
```python
r = FitsReader(path)
r.image_extensions          # [(0, 'PRIMARY'), (1, 'SCI')]
r.get_image_extensions()    # [(0, 'PRIMARY'), (1, 'SCI'), (2, 'WAVETAB')]
```

### Fix
Both changes applied:

1. `get_image_extensions()` is now the *only* implementation, and `load()` calls it
   (`self.image_extensions = self.get_image_extensions()`) instead of running its own loop.
   Displayability is decided by one static helper:
   ```python
   @staticmethod
   def _is_displayable(hdu):
       if not getattr(hdu, 'is_image', False):
           return False
       return int(hdu.header.get('NAXIS', 0)) > 0
   ```
   Header-only, not `hdu.data is not None` — partly because that is the wrong question (a
   `BINTABLE` answers yes), partly because with B5's `memmap=False` probing `.data` would
   read every extension into memory. `NAXIS > 0` also keeps excluding a header-only primary,
   and a test confirms it still *includes* a `CompImageHDU`, since fpacked products would
   otherwise vanish from the dropdown.

   The name-normalisation that only `load()` used to do (`PRIMARY` at a non-zero index
   becomes `EXT n`) now applies to the combo as well, since it is the same code.

   `load_from_memory` also refreshes `image_extensions`, so the attribute can never be a
   stale snapshot from the previous file — the Arithmetic → `load_from_memory` path
   populates the combo from it.

2. `ImageViewer.set_data` grew the missing `else` branch: it clears `transposed_data`,
   `display_data`, the WCS and the ImageView, disables the axis groups and puts
   `Cannot display N-D data` in the slice label. Reachable now only by an explicit
   `load_fits(path, ext=<table>)`, but it means a future mismatch is loud instead of leaving
   the previous extension on screen.

Verified unchanged on the real OSIRIS file — before and after both report
`[(0, 'PRIMARY'), (1, 'EXT 1'), (2, 'EXT 2')]`, so no real extension was dropped.

### Regression test
`tests/test_fits_reader.py`: a `BINTABLE` is excluded while `get_all_extensions()` still
lists it, the two definitions agree, a header-only primary is excluded, a `CompImageHDU` is
kept, and discovery reads no pixel data. `tests/test_main_window_and_poller.py`: the combo
has no `WAVETAB`, and force-selecting the table clears the view instead of leaving a stale
image.

---

## B7. Catalog markers survive closing the Plot Catalog dialog

- **Status:** ✅ FIXED — `plot_catalog.py` (`_remove_scene_item`, `_clear_text_items`),
  covered by `tests/test_plot_catalog.py`
- **Severity:** ~~medium (orphaned graphics, no way to clear them)~~ → **low, latent** —
  see the correction below; the described symptom is masked by refcounting in the common
  case, so the defect is fragility rather than visible orphaned markers.
- **File:** `pyql3/gui/tools/plot_catalog.py:662-674`

### Symptom as originally written
Closing **Plot → Plot Catalog** leaves the markers drawn on the image. They no longer
follow the image item (rotation/zoom/pan detach them) and nothing can remove them short
of reloading a file.

### Correction — measured while fixing it
That is **not** what normally happens. The markers are attached with
`setParentItem(img_item)` rather than `view.addItem()`, so PySide6 keeps *Python* ownership
of them. `closeEvent` detached the parent and then set `self.scatter_item = None`, dropping
the last reference — so CPython's refcounting destroyed the C++ objects immediately and the
scene came out clean by accident:

```
PRE-FIX, no external reference held:
  right after close   : 0 marker items in scene
  after gc.collect()  : 0

PRE-FIX, one reference held:
  after close + gc    : 1        <- leaked, still painted
  kept.scene() is None: False
```

So the real defect is that scene hygiene depended on garbage collection: any surviving
reference — a signal connection, a debugger frame, a future refactor that keeps a handle —
turns it into the visible orphan the entry describes, and between `setParentItem(None)` and
destruction the item is briefly a top-level scene item regardless. Worth fixing as
correctness, but it was not costing users stuck markers today.

### Root cause
`closeEvent` only calls `setParentItem(None)` on `scatter_item` and `highlight_item`.
In Qt that makes the item *top-level in the same scene* — it is not removed. The
equivalent code in `base_tool.remove_roi_from_viewer:41-48` gets it right by also
calling `view.removeItem(...)`, which in pyqtgraph (`ViewBox.removeItem`,
`ViewBox.py:441`) does `scene.removeItem(item)` + `setParentItem(None)`.

### Reproduction
```python
pc = PlotCatalogDialog(None, viewer); pc.load_catalog_file(cat)
scatter = pc.scatter_item; pc.close()
scatter.scene() is viewer.imv.getView().scene()   # -> True
scatter.isVisible()                                # -> True
```

### Fix
One removal helper, used for all three kinds of item so there is a single definition:
```python
def _remove_scene_item(self, item):
    if item is None:
        return
    if self.image_viewer is not None and hasattr(self.image_viewer, 'imv'):
        try:
            self.image_viewer.imv.getView().removeItem(item)
            return
        except Exception:
            pass
    try:                        # no viewer to ask, or it is already torn down
        scene = item.scene()
        if scene is not None:
            scene.removeItem(item)
        else:
            item.setParentItem(None)
    except Exception:
        pass
```
`ViewBox.removeItem` is preferred over a bare `scene.removeItem` because it also drops the
item from the ViewBox's `addedItems` list; that does not matter for the markers (they were
never in it) but it does for the text labels, which *are* added with `view.addItem`, and
routing everything through one helper means a future caller cannot get that wrong.

`closeEvent` becomes:
```python
for attr in ('scatter_item', 'highlight_item'):
    self._remove_scene_item(getattr(self, attr, None))
    setattr(self, attr, None)
self._clear_text_items()
```
The two other places that tore down text labels (`update_plot` and
`update_visible_text_labels`, which re-renders on every pan) now call `_clear_text_items()`
as well — they were already correct, just duplicated.

### Regression test
`tests/test_plot_catalog.py`, 8 tests. The two that actually distinguish fixed from unfixed
are the ones that defeat the refcounting mask: one holds its own references across `close()`,
the other closes with `gc.disable()` and asserts the scene is empty *before* the collector
can run. The rest guard the surrounding behaviour — no accumulation over three
open/close cycles, idempotent double `close()`, `image_viewer=None` and a torn-down viewer
both survive, reopening still draws, and a label refresh replaces rather than leaks.

---

## B8. Strehl tool never reads the instrument keywords

- **Status:** ⚠️ OPEN — **the fix written below is wrong for OSIRIS IFS data.** Superseded by
  `implementation_plan_strehl.md`, which was written after comparing against the KAI reference
  implementation. Read the corrections in that plan before touching this.
- **Severity:** medium → **high**. The stated symptom understates it: on the real Kn5 cube the
  tool reports `strehl = 2.0382` (physically impossible) with `star_fwhm = 0.0000"`, and with
  the *correct* header values it raises `TypeError` out of `fit_radial_profile`.
- **Files:** `pyql3/gui/tools/strehl.py:84-88`, `pyql3/analysis/strehl.py:160-161`

### Corrections to this entry (measured)
1. **`CAMNAME` and `EFFWAVE` do not exist in an OSIRIS spectrograph header.** They are
   NIRC2/imager keywords. The real cube has `INSTR='spec'`, `SFILTER='Kn5'`, `SSCALE='0.035'`
   (strings) and `WAVECNTR=2350.0` nm; `IFILTER` is populated with junk (`'Hn1 ? Kn1'`). So
   the fix below — read `CAMNAME`/`EFFWAVE` from the real header — reads the right *object*
   but the wrong *keywords*, and would leave the defaults in place.
2. **The PSF-centre table below has the 0.035 row wrong.** Measured shapes are 0.020 → 256²,
   0.035 → **256²** (not 512²), 0.050 → 512², 0.100 → 1024². The badly broken cameras are
   0.050 and 0.100. Separately, *every* camera's peak is at `(127,127)`, not `(128,128)`.
3. **A third defect in the same function:** `ap_radius = photrad / pscl` uses the detector
   plate scale but is applied to the PSF array, whose own scale is `pscl * rdfac / rpfac` —
   a factor of 2 and 4 out for 0.050 and 0.100.
4. **A fourth, independent defect:** `fit_radial_profile` masks to `r < 0.7 * fwhm0/pscl`,
   which is under one pixel for every IFS scale coarser than 0.020, so the 2-parameter fit
   gets a single data point and raises. Three of the four OSIRIS plate scales undersample the
   K-band diffraction core (1.42, 1.00 and 0.50 px per FWHM), so **no core-fitting approach
   can work here** — this is why the plan adopts KAI's peak-pixel statistic instead.

### Symptom
The Strehl ratio and theoretical FWHM are always computed for the 0.020"/pix camera at
2.1245 µm, regardless of `CAMNAME`/`EFFWAVE` in the header.

### Root cause
```python
header = self.image_viewer.wcs.to_header()
if 'CURRINST' in header:              # never true
    camname = header.get('CAMNAME', '0.020')
```
`WCS.to_header()` emits only WCS cards (`WCSAXES, CRPIX*, CDELT*, CUNIT*, CTYPE*,
CRVAL*, LONPOLE, LATPOLE, MJDREF, RADESYS`) — instrument keywords can never appear.

### Reproduction
```python
'CURRINST' in osiris_header          # True
'CURRINST' in viewer.wcs.to_header() # False
```

### Fix
Read the real header, which `ImageViewer.set_data` already stores:
```python
header = getattr(self.image_viewer, 'header', None) or {}
if 'CURRINST' in header:
    camname = str(header.get('CAMNAME', '0.020')).strip()
    effwave = float(header.get('EFFWAVE', 2.1245))
```

### Coupled bug — fixing B8 exposes it
`analysis/strehl.py:161` hardcodes `psf_center = (psfsz//2, psfsz//2) == (128, 128)`, but
`generate_psf` returns an array of side `npix * rpfac / rdfac`:

| camname | rpfac | rdfac | returned PSF | assumed centre |
|---------|-------|-------|--------------|----------------|
| 0.020   | 1     | 1     | 256×256      | (128, 128) ✓   |
| 0.035   | 2     | 1     | 512×512      | (128, 128) ✗   |
| 0.100   | 4     | 1     | 1024×1024    | (128, 128) ✗   |

So for any camera other than 0.020 the reference aperture and the fit box land far
off-centre. Fix by deriving the centre from the array actually returned:
```python
psf = generate_psf(...)
psf_center = (psf.shape[1] // 2, psf.shape[0] // 2)
```
and add an assertion/normalisation in `generate_psf` so the returned scale is documented
(the PSF pixel scale is `pscl1 * rdfac`, which must equal the detector `pscl` for the
`ap_radius = photrad / pscl` aperture to be meaningful).

### Regression test
`tests/test_analysis_tools.py`: call `calculate_strehl` on a synthetic Gaussian for each
`camname` and assert it returns a dict with `0 < strehl <= 1.5` instead of crashing or
returning `None`; separately assert the GUI picks `CAMNAME` up from the header.

---

## B9. Peak-fit surface is transposed for non-square ROIs

- **Severity:** medium (misleading 3D comparison; printed parameters are correct)
- **File:** `pyql3/gui/tools/fitting.py:250-304`

### Symptom
**Display Peak Fit** shows the model surface rotated 90° relative to the raw-data
surface whenever the fit box is not square.

### Root cause
```python
x, y = np.meshgrid(x, y, indexing='ij')        # 253 -> shape (nx, ny)
...
self.last_fit_data = func((x, y), *popt).reshape(ny, nx)   # 304 -> wrong order
```

### Reproduction
21×31 ROI on a Gaussian at `(30, 20)`:
```
raw ROI data shape: (21, 31)   fit surface shape: (31, 21)
argmax raw: (10, 15)           argmax fit: (15, 10)
```

### Fix
```python
self.last_fit_data = func((x, y), *popt).reshape(nx, ny)
```
Two nearby robustness items in the same method:
- `curve_fit` can raise `ValueError` (non-finite residuals, `p0` outside bounds) but only
  `RuntimeError` is caught at line 306 — widen to `except (RuntimeError, ValueError):`.
- `data.size < 9` returns at line 240 without resetting the labels, so the panel keeps
  showing the previous fit. Set them to `"N/A"` first.

Since Phase 1 of the 2026-10-21 review (R9), the fit samples the whole pixels the box
covers, clipped to the image, so a square Peak Fit box clipped at an image edge becomes
non-square and **Display Peak Fit** shows this transposed surface for any box at an edge,
not only for a box drawn non-square.

### Regression test
Assert `last_fit_data.shape == last_raw_data.shape` and that the argmax of the fit is
within a pixel of the argmax of the data, for a deliberately non-square ROI.

---

## B10. Diagonal Cut "Width" control does nothing

- **Status:** ✅ FIXED (with B1) — `cuts.py`, covered by `tests/test_cuts.py`
- **Severity:** medium
- **File:** `pyql3/gui/tools/cuts.py:117`

### Symptom
Changing *Width* in the Diagonal Cut dialog changes neither the drawn ROI nor the
extracted profile.

### Root cause
```python
self.roi.pen.setWidth(self.spin_w.value())
```
This targets the cosmetic pen, not the ROI geometry — and `getArrayRegion` samples the
ROI's rectangle, so the averaging width never changes. (In the run below even the pen
width was unchanged, since `ROI.pen` is not necessarily the live pen object.)

### Reproduction
```
width=5  -> 57 samples, sum 570.24, roi.size() = Point(56.57, 5.0)
width=25 -> 57 samples, sum 570.24, roi.size() = Point(56.57, 5.0)
```

### Fix
`pg.LineROI` is a rotated rectangle: its `size()` is `(length, width)` and its angle is
the line direction. Add one helper used by both `on_spin_changed` (**B1**) and the width
spinbox:
```python
import math

def set_diagonal_endpoints(self, p0, p1, width=None):
    if width is None:
        width = max(1, self.spin_w.value())
    dx, dy = p1[0] - p0[0], p1[1] - p0[1]
    length = max(1e-3, math.hypot(dx, dy))
    angle = math.degrees(math.atan2(dy, dx))
    self.roi.setSize([length, width])
    self.roi.setAngle(angle)
    # LineROI's origin is the corner of the rectangle, offset by half the width
    # perpendicular to the line:
    nx, ny = -dy / length, dx / length
    self.roi.setPos(p0[0] - nx * width / 2.0, p0[1] - ny * width / 2.0)
```
Verify against `sync_spins_and_plot` (`cuts.py:173-182`), which reads the handle
positions back — the round-trip spin → ROI → spin must be stable, otherwise the two
handlers will fight. Add that round-trip to the test.

---

## B11. Vertical Cut leaves a stale background curve on the Depth Plot

- **Severity:** low (misleading plot)
- **File:** `pyql3/gui/tools/depth_plot.py:1071-1092`

### Symptom
With background subtraction enabled, switching *Type* to **Vertical Cut** clears the red
"Subtracted" curve but leaves the orange "Background" spectrum drawn — now against a
Y-pixel x-axis, so it is nonsense.

### Root cause
The `Horizontal Cut` branch clears both (`:1068-1069`); the `Vertical Cut` branch clears
only `plot_sub` (`:1092`).

### Reproduction
```
depth plot   -> bg 30 pts, sub 30 pts
horiz cut    -> bg 0 pts
vertical cut -> bg 30 pts      # stale
```

### Fix
Add `self.plot_bg.setData([], [])` next to line 1092. Better: clear all three curves
once at the top of `update_plot` so a new branch can never forget.

---

## B12. Reversed / out-of-range collapse range silently blanks the image

- **Status:** ✅ FIXED (with B16/B17) — `image_viewer.py` (`clamp_z_range`,
  `z_range_from_fields`, `boxcar_range`, `collapse_plane`), covered by
  `tests/test_image_viewer.py`
- **Severity:** low (confusing, no diagnostic)
- **File:** `pyql3/gui/viewers/image_viewer.py` — `apply_z_range` (was `:581`, now `:647`)
  and `update_slice_info` (was `:147`, now `:208`); the original line numbers predate the
  B3/B4 work

### Symptom
Entering `Z Min 10`, `Z Max 3` (or a Z Min past the end of the cube) produces an empty
slice, so `np.nanmedian` returns an all-NaN plane: the display goes blank, a
`RuntimeWarning: Mean of empty slice` appears on the console, and the label cheerfully
reads `Collapsed: 10-3`.

### Root cause
```python
zmin = max(0, int(self.txt_zmin.text()))                          # no upper clamp
zmax = min(self.transposed_data.shape[0]-1, int(self.txt_zmax.text()))   # no lower clamp
subcube = self.transposed_data[zmin:zmax+1, :, :]                  # may be empty
```

### Fix
The clamping was extracted rather than repeated, because the half-clamped pattern occurred
in four places (`apply_z_range`, `update_slice_info`, `apply_z_slice`'s Boxcar branch and
`current_z`). `ImageViewer` grew a small set of z-range helpers that are now the single
definition:

| helper | purpose |
|---|---|
| `clamp_z_range(zmin, zmax, write_back=False)` | clamp both ends, order them, optionally correct the Z Min / Z Max boxes |
| `z_range_from_fields(write_back=False)` | parse + clamp the boxes; `None` if unparsable |
| `boxcar_width()` / `boxcar_range(z)` | the Boxcar setting and the window it averages |
| `collapse_plane(zmin, zmax, method=None)` | the collapse arithmetic, formerly inlined twice |
| `apply_spatial_transforms(arr)` | the flip/rot half of `apply_transforms`, as a pure function |

`setText()` does not re-emit `editingFinished`, so writing the corrected range back cannot
recurse into `apply_z_range`.

Behaviour now:
```
typed (10,  3) -> boxes (3, 10)   Collapsed: 3-10,  Wavelengths: 2.2015-2.2050 µm
typed (99,  3) -> boxes (3, 19)   Collapsed: 3-19
typed (-5,  4) -> boxes (0,  4)   Collapsed: 0-4
typed (abc, 4) -> boxes unchanged  Collapsed: Invalid Range
```
Unparsable text is reported and *not* rewritten — silently replacing what the user typed
would hide the typo — and the previous display is left alone rather than blanked. The
`RuntimeWarning` is gone because the subcube can no longer be empty; `collapse_plane` also
suppresses the `All-NaN slice` warning for genuinely dead regions, which is now handled
visibly by **B16** instead of on the console.

Verified on the real cube (`s150531_a025002_Kn5_035.fits`, 465 channels): typing
`Z Min 300, Z Max 100` yields `Collapsed: 100-300, Wavelengths: 2.3170-2.3670 µm` with the
boxes corrected and a fully finite render.

---

## B13. Depth Plot un-rotation uses one axis length for both axes

- **Status:** ✅ FIXED (with B14) — `depth_plot.py` now calls
  `ImageViewer.display_to_orig()`, covered by
  `tests/test_coords.py::test_the_old_depth_plot_un_rotation_was_wrong_for_non_square_planes`,
  which keeps the old arithmetic verbatim and asserts that it disagreed
- **Severity:** low (currently invisible)
- **File:** `pyql3/gui/tools/depth_plot.py:983-990`

### Root cause
```python
for _ in range((4 - k) % 4):
    cx, cy = cy, x_len - 1 - cx      # x_len for every step
```
Each 90° step swaps the array's x/y extents, so the loop must alternate `x_len`/`y_len`.
`plot_catalog.map_to_display:36-38` does this correctly and is the model to copy:
```python
for _ in range(k):
    curr_x, curr_y = orig_max_y - 1 - curr_y, curr_x
    orig_max_x, orig_max_y = orig_max_y, orig_max_x
```

### Impact
Wrong spatial pixel for rotated, non-square cubes — but it only feeds the WCS lookup for
the wavelength axis, which for all supported instruments does not depend on spatial
position. Fix for correctness, not for a visible symptom. Best done by extracting a
single shared `display_to_orig()` / `orig_to_display()` pair (see **B14**).

---

## B14. Coordinate-transform logic is duplicated three times

- **Status:** ✅ FIXED — extracted to `pyql3/core/coords.py` with
  `ImageViewer.orig_to_display()` / `.display_to_orig()` as adapters; all three call sites
  now go through them. Covered by `tests/test_coords.py` (181 cases)
- **Severity:** low (maintenance; source of B13)
- **Files:** `image_viewer.py:1204-1219`, `plot_catalog.py:18-40`, `depth_plot.py:983-990`

The display ↔ original pixel mapping for `rot_angle` + `flip` is implemented three
times, with three different levels of correctness. Extract
`ImageViewer.display_to_orig(x, y)` and `ImageViewer.orig_to_display(x, y)` (the
`plot_catalog` version is the correct one) and have all three call sites use them. Add a
round-trip property test over `rot_angle ∈ {0, 90, 180, 270} × flip ∈ {False, True}` on a
non-square array.

### Fix
`pyql3/core/coords.py` holds the arithmetic as pure functions, so it is tested against the
production transform itself: `ImageViewer.apply_spatial_transforms()` reads only
`self.rot_angle` and `self.flip`, so the tests call it unbound against a stub, transform a
labelled 5×3 array, and assert the mapper reports where each label actually went — every
pixel, all eight flip × rotation combinations. A round-trip test alone would not have caught
B13, since both directions can be wrong together.

The module also provides `orig_angle_to_display()` (needed for a box's position angle and an
arrow's heading, see `TODO_regions.md`) and named helpers for the FITS 1-based /
numpy 0-based / pyqtgraph centre-at-`i+0.5` conventions that meet in this code.

Writing the angle mapping is what turned up **B20**.

---

## B17. Depth Plot cuts sample one channel while a collapsed plane is displayed

- **Status:** ✅ FIXED (with B12/B16) — `image_viewer.py` (`current_plane`),
  `depth_plot.py:1044`/`:1074`, covered by `tests/test_image_viewer.py`
- **Severity:** medium (the plotted profile is not the data on screen)
- **File:** `pyql3/gui/tools/depth_plot.py:1049-1052` and `:1074-1077`
- Found by auditing whether **B4** also affected collapsed cubes. It did, through these
  two branches only; the other seven call sites were checked and are correct.

### Symptom
With the viewer in **Z Range** collapse mode (or **Boxcar** > 1), **Depth Plot → Type →
Horizontal Cut / Vertical Cut** extracts its profile from a *single* wavelength channel of
the cube instead of the collapsed plane being displayed. The plot silently disagrees with
the image, the Statistics dialog and the pixel readout.

### Reproduction
Cube where wavelength channel *k* holds the constant value *k*; collapse channels 4-8 with
**Sum**, so every displayed pixel is `4+5+6+7+8 = 30`:

| | value |
|---|---|
| on screen / Statistics dialog / hover readout | **30.0** |
| Depth Plot Horizontal & Vertical Cut, before B4 was fixed | 3.0 (stale `imv.currentIndex` — whatever slice was last displayed) |
| Depth Plot Horizontal & Vertical Cut, after B4 | 6.0 (`current_z()`, the middle of the range) |

`Median`/`Mean` collapse of a linearly varying cube happens to make the midpoint channel
agree numerically, which is why this hides easily — use `Sum`, or any cube that is not
linear in *z*, to see it.

### Root cause
`cube` (`depth_plot.py:885`) is `transposed_data` with the display flip/rotation applied
and is therefore always 3-D, so *any* `cube[z_idx]` is a single channel. The viewer's
collapse happens in `apply_z_range` / `apply_z_slice`, which write the 2-D result to
`display_data` — a state the Depth Plot never consults. Fixing B4 only changed *which*
channel is wrong.

### Fix
`ImageViewer.current_plane()` — a companion to `current_z()` — returns the 2-D plane
matching the screen, oriented like `display_data` but **without** the DN multiplier, since
the Depth Plot applies `data_multiplier` itself at plot time and `display_data` would
double-apply it. It dispatches on the z-mode (collapse range → `collapse_plane`, Boxcar →
median over `boxcar_range`, otherwise the single channel at `current_z()`) and reuses the
B12 helpers, so the collapse arithmetic exists once.

Both cut branches became:
```python
plane = self.image_viewer.current_plane()      # 2-D, already collapsed if applicable
if plane is None or plane.ndim != 2:
    return
region = plane[x0:x1, y0:y1]
```
The "Depth Plot" spectrum type is untouched — it integrates over z by definition and was
correct in every mode.

Two pieces of duplication went away with it: `apply_transforms` now delegates its flip/rot
half to `apply_spatial_transforms`, and the Depth Plot's own hand-rolled copy of that
transform (`depth_plot.py:884-890`) calls the same helper — which is what guarantees
`current_plane()` and the dialog's `cube` stay in the same orientation. A parametrized test
asserts `current_plane() == display_data[current_z()]` for `rot_angle ∈ {0, 90, 180, 270}`
× `flip`. (**B14** still stands for the *coordinate* transforms; this only unifies the
array transform.)

Result, same setup as the table above:

| | value |
|---|---|
| on screen / Statistics / hover readout | 30.0 |
| Depth Plot cuts, now | **30.0** |

### Regression test
`tests/test_image_viewer.py`: the constant-per-channel cube collapsed 4-8 over `Sum`,
`Mean` and `Median` (Sum is the case a midpoint coincidence cannot pass), Boxcar 5, plain
Z Slice mode as an unchanged-behaviour guard, plus the `current_plane()` multiplier and
orientation properties. Verified on the real cube too: with `Z Range 100-300 Sum`, the
Horizontal Cut mean matches the collapsed plane to 4 decimal places (-17.1690).

---

## B16. An all-NaN image raises out of `set_data` (found while fixing B3)

- **Status:** ✅ FIXED (with B12/B17) — `image_viewer.py` (`refresh_plane_validity`,
  `update_image_display`, `update_slice_info`), covered by `tests/test_image_viewer.py`
- **Severity:** medium (exception, not just a blank display)
- **File:** `pyql3/gui/viewers/image_viewer.py` (the `imv.setImage` / `autoRange` call at
  `:1145-1152`), via `pyqtgraph/imageview/ImageView.py:751`

### Symptom
Displaying a plane that is entirely NaN — a dead slice, a fully masked cube slice, or the
empty collapse range of **B12** — raises instead of showing an empty frame:
```
RuntimeWarning: All-NaN slice encountered   (ImageView.py:751, nanmin/nanmax)
Exception: Cannot set range [nan, nan]      (ViewBox.py:619)
```

### Root cause
Independent of the scale mode — it reproduces identically on `Linear` — so it is *not*
part of B3. pyqtgraph derives an autorange from `nanmin`/`nanmax` of the image, which are
NaN for an all-NaN array, and `ViewBox.setRange` rejects a non-finite range.

### Reproduction
```python
viewer.set_data(np.full((32, 16), np.nan, dtype=np.float32))
viewer.combo_scale.setCurrentText("Linear")
viewer.update_image_display()      # Exception: Cannot set range [nan, nan]
```

### Fix
Two independent pieces in `update_image_display`:

1. **Don't crash.** If nothing in `render_data` is finite, hand pyqtgraph zeros with fixed
   `(0, 1)` levels instead. The full scan is guarded by the cheap per-plane check below —
   if the displayed plane has any finite pixel the cube cannot be all-invalid, so the
   expensive path only runs in the rare case that matters.
2. **Say so.** `refresh_plane_validity()` sets `_plane_all_invalid` by probing *one* plane
   (`display_data[current_z()]`, never the whole cube), and `update_slice_info` appends
   `  —  no valid data` to the slice label. It has to be recomputed on every slice change,
   not only on redisplay: paging onto a dead channel does not redisplay the image, it only
   moves the ImageView's index, so a flag set in `update_image_display` alone goes stale.

The probe reads the *data*, not `render_data`: the HistEq branch substitutes zeros of its
own, which are finite and would mask the condition.

While here — `float("nan")` and `float("inf")` parse happily from the Min/Max boxes, and a
non-finite level poisons every scale mode into an all-NaN render, i.e. the same crash by
another route. `vmin`/`vmax` now fall back to `(0, 1)` when either is non-finite.

### Residual, not fixed
For a cube that *legitimately* contains an all-NaN channel, pyqtgraph's own histogram still
prints `RuntimeWarning: All-NaN slice encountered` (`ImageItem.py:984-985`) when that
channel is displayed. It is cosmetic, it comes from a paint/event callback inside pyqtgraph
rather than from our call, and a user drag of the timeline reaches `setCurrentIndex` without
passing through our code — so a local `catch_warnings` would only silence it inconsistently.
Suppressing it would need a global `warnings.filterwarnings(..., module="pyqtgraph.*")` at
startup, which would also hide unrelated numpy warnings from the library. Left alone
deliberately.

---

## B18. Saving a header over the open file fails on Windows

- **Status:** ✅ FIXED — `fits_reader.py` (`save`), covered by `tests/test_fits_reader.py`
- **Severity:** medium (a documented user action fails outright, on Windows only)
- **File:** `pyql3/core/fits_reader.py` — `save()`
- Found by the Windows CI job on the B5/B6 commit; **pre-existing**, not introduced by B5.
  The macOS and Ubuntu jobs passed.

### Symptom
**Header Editor → "Save header changes directly to file?" → Yes** raises, and the dialog
reports `Failed to save header: [WinError 32] The process cannot access the file because it
is being used by another process`. Windows only.

### Root cause
Windows opens files without `FILE_SHARE_DELETE`, so a path that any handle still has open
cannot be unlinked or replaced. astropy implements `writeto(..., overwrite=True)` as
`os.remove()` followed by a fresh create (`astropy/io/fits/file.py:557`), and `FitsReader`
holds the file open. So the remove is refused:

```
File "pyql3/core/fits_reader.py", in save
    self.hdul.writeto(save_path, overwrite=True)
  ...
    os.remove(self.name)
PermissionError: [WinError 32] ... 'live.fits'
```

Note this is orthogonal to `memmap`: the handle is held either way, so it was equally broken
before B5 switched to `memmap=False`. It only became *visible* because B5 added a test that
asserts saving over the open path works.

### Fix
Materialise every HDU, drop the OS handle, write to a sibling temp file, then swap it in with
`os.replace()` — atomic on both platforms, and it never leaves a half-written file where the
original was. Then reopen so reader state matches disk.

```python
for hdu in hdul:
    _ = hdu.data          # real arrays with memmap off; they survive close()
hdul.close()
fd, tmp_path = tempfile.mkstemp(prefix='.pyql3_save_', suffix='.fits', dir=directory)
os.close(fd)
hdul.writeto(tmp_path, overwrite=True)
os.replace(tmp_path, save_path)
```

Materialising *before* closing is the part that matters: lazily-loaded HDUs cannot be read
after the handle is gone, and `writeto` would then fail or write empty extensions.

### How it was verified without a Windows machine
A pytest plugin patches `os.remove`/`os.unlink`/`os.replace`/`os.rename` to raise
`PermissionError(32)` whenever the target is a FITS path some handle still has open, i.e. it
imposes Windows sharing semantics on macOS. It reproduces exactly the 5 CI failures against
the old tree, and the whole suite passes under it after this fix. Kept in the session
scratchpad; **worth promoting into `tests/` if Windows regressions recur** (see B19).

### Regression test
`test_save_to_the_same_path_succeeds_while_open` (the CI failure), plus
`test_save_leaves_no_temp_files_behind`, `test_save_as_a_new_path_keeps_the_original_open`
and `test_save_preserves_every_extension` — the last one guards the materialise-before-close
step, which would otherwise silently drop extension data.

### Testing note (worth keeping)
**Never simulate an external rewrite with `writeto(path, overwrite=True)` while anything
holds the file open** — it cannot work on Windows. Use the `rewrite_fits_in_place` fixture in
`tests/conftest.py`, which writes into the existing file with `r+b` and then bumps the mtime
explicitly. The bump matters too: the Windows clock ticks about every 15 ms, so a rewrite
microseconds after the original can land on an identical mtime and defeat the staleness check
in a way that depends on how fast the test ran.

---

## B19. Holding an open file handle can block an external writer on Windows

- **Severity:** unknown, potentially high for live observing on Windows — **needs a decision**
- **File:** `pyql3/core/fits_reader.py` — `load()` keeps the `HDUList` open between calls
- Identified while fixing B18. **Pre-existing** and not yet fixed.

### The problem
The same Windows rule behind B18 cuts the other way. While pyql3 has a file open, no other
process can unlink or rename over that path. A DRP or instrument that publishes frames
*atomically* — write `foo.tmp`, then rename onto `foo.fits`, which is the standard way to
avoid readers seeing a half-written file — would get `PermissionError` from its own rename
for as long as the viewer has that frame open.

In-place rewrites are unaffected, and B5's stat-signature check detects those correctly.
So the exposure depends entirely on how the writer publishes:

| writer style | Unix | Windows |
|---|---|---|
| rewrite in place | works, detected | works, detected |
| write temp + rename over | works, detected | **writer is blocked** |
| delete + recreate | works, detected | **writer is blocked** |

This is speculative until confirmed against a real Windows setup — I have not observed it,
only derived it from the sharing flags that produced B18.

### Options
1. **Do nothing**, document it. Zero risk, but if OSIRIS/KAI publishes by rename, Windows
   users at the telescope silently break the data-taking loop, which is the worst outcome.
2. **Do not hold the handle**: open → read the selected HDU and all headers → close. Costs a
   reopen per extension switch (measured 0.002 s for the 14 MB cube, so negligible) but
   loses the B5 property that unsaved Header Editor edits survive an extension switch,
   unless the edited headers are kept separately.
3. **Close the handle only while idle** — reopen on demand. More moving parts, keeps both
   properties.

Option 2 is the simplest defensible design and the reopen cost is measured to be trivial;
the question is whether preserving unsaved header edits across an extension switch is worth
the Windows exposure.

---

## B20. Position-angle compass mirrors the rotation instead of the array

- **Status:** ✅ FIXED — the three copies now call
  `ImageViewer.north_east_display_angles()`, covered by `tests/test_position_angle.py`
  (49 cases; nothing covered the compass before)
- **Severity:** medium (the N/E compass points the wrong way, and it is what a user checks
  orientation against)
- **Files:** `image_viewer.py:1229-1234` (`toggle_position_angle`),
  `rotate.py:109-111` (the "North Angle" readout), `rotate.py:147-149`
  (`on_north_up_clicked`) — the same three lines in three places

### Root cause
The display pipeline is: flip the array (`np.flip`), then rotate the array (`np.rot90`), then
rotate the *ImageItem* by `view_rotation` with a `QTransform`. The compass composes those in a
different order:

```python
theta_n_vis = (theta_n_base + self.rot_angle + self.view_rotation) % 360.0
if self.flip:
    theta_n_vis = (180.0 - theta_n_vis) % 360.0     # mirror applied last, to everything
```

A flip about X maps a direction `θ` to `180 - θ`, and each 90° step adds 90°, so the
data-consistent value is `(180 - θ) + 90k` — the mirror belongs *before* the rotation terms,
not around them. Mirroring the whole sum also mirrors `view_rotation`, which is applied to the
already-flipped array by a transform on the ImageItem and so must not be mirrored at all. One
ordering error, two symptoms. Note the arrows are added with `getView().addItem()` rather than
parented to the ImageItem, so including `view_rotation` in the angle is itself correct.

With `flip` off every combination agrees, which is why this has gone unnoticed.

### Symptom
North for `theta_n_base = 90°`, comparing `coords.orig_angle_to_display` (verified against
`apply_spatial_transforms` in `tests/test_coords.py`) with what the compass draws:

| flip | rot_angle | view_rotation | correct | drawn |
|------|-----------|---------------|---------|-------|
| off  | any       | any           | —       | agrees |
| on   | 0         | 0             | 90°     | 90° |
| on   | 90        | 0             | 180°    | **0°** |
| on   | 270       | 0             | 0°      | **180°** |
| on   | 0         | 30            | 120°    | **60°** |
| on   | 180       | 30            | 300°    | **240°** |

So with a horizontal flip and a 90°/270° rotation the N and E arrows point exactly backwards,
and with a flip and any view rotation they rotate the wrong way. **North Up** inherits it:
`on_north_up_clicked` solves for `view_rot` with the same expression, so with flip on it
settles on an orientation that is not north up.

### Fix
`ImageViewer.north_east_display_angles(include_view_rotation=True)` is now the only place the
composition happens: `coords.orig_angle_to_display()` for the array transforms, then
`view_rotation` added outside the mirror. `toggle_position_angle` and both `rotate.py` sites
call it; `on_north_up_clicked` passes `include_view_rotation=False`, which is the only reason
it needed its own expression before.

Tested geometrically rather than by restating the formula: North is a direction in orig space,
so a step from the centre toward North is two orig points, and the arrows must follow where
those points land on screen (`coords.orig_to_display`, itself pinned against
`apply_spatial_transforms` in `tests/test_coords.py`). An independent anchor asserts that with
no transforms at all the drawn angle is simply the base angle.

The tests were checked against the bug: restoring the old expression fails 19 of them, and
**every failure is a `flip=True` case** — with `view_rotation=0` at `rot_angle` 0 and 180 still
passing, matching the table above. `test_a_flip_with_a_quarter_turn_used_to_point_backwards`
and `test_a_flip_with_a_view_rotation_used_to_turn_the_wrong_way` keep the old expression
verbatim and assert it was 180° (respectively twice the view rotation) out, so the two symptoms
are recorded rather than described.

---

## M12. Right-clicking a region raised in the terminal

- **Status:** ✅ FIXED — `region_layer.py` (`RegionItemInteraction`), `main_window.py`
  (`build_region_menu` / `show_region_menu`), covered by
  `tests/test_region_layer.py` and `tests/test_region_properties.py`
- **Severity:** medium (every right-click on a region printed a traceback and showed no menu)
- **Reported from use**, not from a review.
- **File:** the interaction between `pyqtgraph.ROI.raiseContextMenu` and our parenting choice

### Root cause
`pg.ROI.raiseContextMenu` calls `self.scene().addParentContextMenus(self, menu, ev)`, which walks
up the parent chain asking each item for extra menus. Region items are parented to the **ImageItem**
so they inherit the view rotation (`apply_view_rotation` is a `QTransform` on that item), and
`ImageItem.getContextMenus()` is:

```python
def getContextMenus(self, event):
    return [self.getMenu()] if hasattr(self, "getMenu") else []
```

`ImageItem.getMenu()` returns **None** unless the image is removable, so this returns `[None]` —
truthy, so pyqtgraph's `or []` does not save it — and the loop below reaches
`raise Exception(f"Cannot add object {menuOrAct} ...")`.

A region is the first ROI in this application constructed with `removable=True`, and
`contextMenuEnabled()` is `self.removable`, so no earlier ROI could reach `raiseContextMenu` at
all. The tool dialogs' ROIs are parented to the same ImageItem and were never affected.

### Fix
`RegionItemInteraction` overrides `mouseClickEvent` and `raiseContextMenu` on the region item
classes: a right-click asks the layer for a menu (`region_menu_requested`) and a double-click asks
for the properties editor (`region_activated`), so pyqtgraph's parent walk is never entered.
`tests/test_region_layer.py::test_the_pyqtgraph_menu_path_would_still_raise` asserts the inherited
path *does* still raise, so the override cannot be quietly removed.

`MainWindow.build_region_menu()` is split from `show_region_menu()` because `QMenu.exec` is modal
and blocks until dismissed — a test that shows one hangs the suite, the same way `install_cli_tool`
does without a stubbed `confirm_cli_install`.

---

## M13. An arrow region's head pointed the wrong way

- **Status:** ✅ FIXED — `region_layer.py` (`_arrow_head_angle`), covered by
  `tests/test_region_layer.py::test_the_arrow_head_points_along_its_own_line` and
  `::test_the_arrow_head_follows_the_line_through_every_transform`
- **Severity:** medium (the arrow drew a head that disagreed with its own line)
- **Reported from use.**
- **File:** `pyql3/gui/viewers/region_layer.py`, `_place_arrow_head`

### Symptom
Every arrow except a horizontal one drew its head at the wrong angle — mirrored about the
horizontal, so an arrow drawn up-and-right had a head pointing down-and-right.

### Root cause
Two conversions, one of them missed:

- `pg.ArrowItem` points **opposite** its `angle` option (measured: `head = angle + 180`).
- The head is created with `pxMode=True`, which sets `ItemIgnoresTransformations`, so it is painted
  in raw **screen** coordinates where y increases *downward* — while the view, and therefore the
  line's angle, has y increasing upward.

The code used `direction + 180`, which handles only the first. The two expressions agree at 0° and
180°, so a horizontal arrow looked right and nothing else did.

The convention was copied from the PA compass (`toggle_position_angle`), where `angle + 180` is
correct — because those arrows are `pxMode=False` and so *are* transformed with the view. Copying an
angle convention between a transformed and an untransformed item is the whole bug.

### Fix
`_arrow_head_angle(direction)` returns `(180 - direction) % 360`, with the reasoning recorded at the
call site. Tested by measuring the direction of the head's painted path rather than by restating the
formula, over nine angles and all eight flip × rotation combinations; reverting the fix fails 16 of
the 19 cases, and the 3 that still pass are the horizontal ones. The aggregate overlay draws its own
arrowheads as polyline barbs in data coordinates — a separate implementation, now also checked for
barbs that fall behind the tip and straddle the line.

---

## M14. The test suite wrote to the developer's own settings file

- **Status:** ✅ FIXED — `tests/conftest.py` (`isolated_settings`)
- **Severity:** low for the code, annoying for the developer
- **Found while checking that the region toolbar defaults to off.**

### Symptom
`MainWindow` takes the process-wide `ConfigManager`, so every test that built a window wrote to the
real `~/.pyql3/config.json`. A full run left the **Recent Files** list holding ten pytest temp paths
instead of the developer's own files, and any preference a test toggled stayed toggled — a window
could then open with a region toolbar nobody had asked for.

It also made the suite depend on the developer's saved settings: a stored `polling_interval` was
being read by tests that build a window.

### Fix
An autouse, session-scoped fixture points `pyql3.services.config._shared_config` at a file under
`tmp_path_factory` for the duration of the run, and restores it afterwards. Autouse so no test can
opt out by forgetting. Verified by diffing the real settings file across a full run: unchanged.

---

## M15. A right-click near the edge was clipped to the wrong pixel

- **Status:** ✅ FIXED — `image_viewer.py` (`on_view_clicked`), covered by
  `tests/test_coords.py::test_a_right_click_is_clipped_to_the_displayed_plane`
- **Severity:** low before regions could be spawned by right-clicking, medium after
- **Found while adding the region entries to the viewer's context menu.**

### Root cause
```python
nx = self.transposed_data.shape[-1]   # this is the *y* extent
ny = self.transposed_data.shape[-2]   # ...and this is x
x = int(np.clip(pt.x(), 0, nx - 1))
```
`transposed_data` is `(z, x, y)`, so the two extents were the wrong way round; and they are the
extents of the *stored* plane, while `pt` is a coordinate on the *displayed* one, which has them
swapped again under a 90° rotation.

The stored position feeds **Depth Plot...** and **Gaussian Fit...** from the right-click menu, and
now also spawns a region there, so a click near an edge put the tool on the wrong pixel. Every
fixture was square until recently, which hid it completely.

### Fix
`coords.display_dims(*self.orig_spatial_dims(), self.rot_angle)` — the extents of what is actually
on screen. Tested on a deliberately non-square plane, before and after a quarter turn.

---

## M16. Clicking a text region printed a traceback

- **Status:** ✅ FIXED — `region_layer.py` (`RegionItemInteraction.mouseClickEvent`), covered by
  `tests/test_region_layer.py::test_an_ordinary_click_on_any_region_does_not_raise`
- **Severity:** medium (every ordinary click on a text region)
- **Reported from use.**

### Symptom
```
AttributeError: 'super' object has no attribute 'mouseClickEvent'
```
printed by pyqtgraph from inside `GraphicsScene.sendClickEvent`, which catches the exception, so the
application carried on and only the terminal showed it.

### Root cause
`RegionItemInteraction` ends by delegating anything it does not handle to `super()`. That holds for
`RegionCircleROI`, `RegionRectROI` and `RegionLineROI`, since `pg.ROI` defines `mouseClickEvent` —
but a text region's label is a `RegionTextItem`, and **`pg.TextItem` has no such method**, so any
single left click on the text raised.

The tests missed it because the single-click case was only exercised on a circle.

### Fix
The fall-through looks the method up before calling it, and ignores the event when the base class
has none. Tested across all four shapes and both non-handled buttons; reverting the fix fails
exactly the text cases.

---

## M17. Dialogs wider than the main window opened off-screen

- **Status:** ✅ FIXED — `base_tool.py` (`keep_on_screen`), covered by `tests/test_region_ui.py`
- **Severity:** low (Qt recovered, but the dialog appeared somewhere unrelated)
- **Reported from use.**

### Symptom
```
qt.qpa.window: Window position QRect(-40,313 760x320) outside any known screen, using primary screen
```
The Region List is 760 px wide and the main window is 600 px, so centring the dialog on its parent
put it 80 px to the left of the window — off the screen edge when the window sits near it. Qt then
moved it to the primary screen on its own, which is why it appeared somewhere unexpected rather than
vanishing.

### Fix
`BaseToolDialog.showEvent` nudges the dialog inside `availableGeometry()` the first time it opens —
only the first time, since moving it afterwards is the user's business. Every tool dialog inherits
it; `RegionPropertiesDialog`, which is a plain `QDialog`, calls the same helper.

---

## M18. Replacing the region set segfaulted, two runs in three

- **Status:** ✅ FIXED — `region_layer.py` (`_destroy_items`, `_clear_bulk_items`,
  `_drop_retired`), covered by
  `tests/test_region_layer.py::test_replacing_regions_never_releases_items_mid_rebuild`
- **Severity:** high — a crash, in the ordinary path of loading a second region file
- **Found while measuring the test suite**, not from a failing test: the suite passes as a whole,
  and the crash appeared only when a particular subset of files ran together.

### Symptom
```
Fatal Python error: Segmentation fault
Current thread ... (most recent call first):
  Garbage-collecting
  File ".../pyqtgraph/graphicsItems/GraphicsObject.py", line 18 in itemChange
  File ".../pyqtgraph/graphicsItems/ROI.py", line 618 in addHandle
  File ".../pyqtgraph/graphicsItems/ROI.py", line 559 in addRotateHandle
  File ".../pyql3/gui/viewers/region_layer.py", line 834 in _build_items
  File ".../pyql3/gui/viewers/region_layer.py", line 371 in set_regions
```

Reproduced 2 runs in 3 with:

```bash
QT_QPA_PLATFORM=offscreen uv run pytest tests/test_region_layer.py tests/test_region_ui.py \
  tests/test_region_properties.py tests/test_region_toolbar.py tests/test_coords.py \
  tests/test_regions_model.py tests/test_ds9_regions.py -q -p no:randomly
```

Neither file crashes alone, and the full suite passed twice — collecting a different set of
modules is enough to move the collector's threshold, which is all this needs.

### Root cause

This is the same hazard the `_retired` graveyard was added to fix, defeated by its own
implementation. Both `_destroy_items` and `_clear_bulk_items` began with `self._drop_retired()`,
so:

```python
def clear(self, notify=True):
    for entry in self._entries:
        self._destroy_items(entry)     # entry 2's flush releases entry 1's items
```

`clear()` destroys entries in a loop, and each call released the batch retired by the previous
one. `set_regions()` then calls `render()` immediately, so the collector was handed a pile of
QGraphicsItem cycles microseconds before `addRotateHandle` allocated — and it ran there. Only the
*last* entry's items ever reached the timer, which is why the deferral looked like it was working.

### Fix

The destroy paths only ever append; `QTimer.singleShot(0, ...)` owns the release, so the graveyard
spans one turn of the event loop. `_drop_retired()` now also calls `gc.collect()` explicitly:
a QGraphicsItem sits in a reference cycle with its children and its scene, so dropping the list
frees nothing by refcount alone — forcing the collection at that known-quiet point is the whole
purpose of deferring it. Three clean runs of the repro above, where the previous code crashed
twice.

The regression test asserts a structural property rather than trying to catch a crash: after a
rebuild the graveyard must hold **every** destroyed item, not just the last entry's. Verified by
mutation — restoring the leading `_drop_retired()` fails it.

## M19. A closed window kept its cube in memory for the life of the process

- **Status:** ✅ FIXED — `image_viewer.py` (`release_data`), called from
  `main_window.py::closeEvent`, covered by
  `tests/test_multi_window.py::test_closing_a_window_releases_its_cube`
- **Severity:** medium — memory only, but unbounded and invisible
- **Found while investigating why the test suite got slower as it ran.**

### Symptom

Five open-and-close cycles on an 8 MB cube left **five `ImageViewer`s alive holding five cubes**,
and RSS up 168 MB. Nothing recovers it; the memory is gone until the application exits. On a real
OSIRIS cube the leak is roughly the size of the cube per closed window — three arrays, of which
`transposed_data` is usually a view.

### Root cause

`closeEvent` released everything that was obviously per-window — the poller thread, the tool
dialogs, the FITS handle — on the assumption that Qt would take the widget itself. It does not.
`close()` hides a window; destroying it needs an explicit `deleteLater()` or the owner dropping
the last reference, and there is no last reference to drop: every `lambda: self.x()` connected to
a QAction is held by that action → the menu → the widget, a cycle through C++ that Python's
collector cannot break. **415 widgets survive every `MainWindow`, permanently**, `gc.collect()`
included, and the arrays go with them.

### Fix

`ImageViewer.release_data()` drops `raw_data`, `transposed_data`, `display_data`, the header and
the WCS, and clears the ImageView; `closeEvent` calls it. The widget shell still survives — that
is a few hundred kB and not worth the risk of destroying a window Qt may still deliver events to.
`deleteLater()` was measured as an alternative and rejected: 11 MB saved of 193 across ten
windows, because it destroys the C++ object while the Python wrapper, and so the cube, stays.

The test asserts through a `weakref` to the array rather than on the attributes being `None` —
setting an attribute to `None` proves nothing if the reader, the ImageItem or a tool still holds
the data.

### The same leak in the test suite

Every leaked window also costs *time*: `ViewBox.__init__` calls `ViewBox.updateAllViewLists()`,
which walks every live ViewBox, so window construction is O(live views) — 0.037 s at ten windows,
0.129 s at 150, which is where the suite's 0.24 s → 0.67 s fixture creep came from.
`tests/conftest.py::destroy_leftover_windows` destroys leftovers with `shiboken6.delete`:
**150 s → 60 s** for the full suite, same 937 tests.

## M20. A ds9 region file in the `physical` frame loaded as nothing at all

- **Status:** ✅ FIXED — `ds9_regions.py` (`_normalize_pixel_frames`, `_parse_shapes`,
  `_report_annotations`), covered by `tests/test_ds9_regions.py` (`test_a_physical_frame_reads_as_
  image_coordinates` and the three tests beside it)
- **Severity:** high — every region in the file was lost, and the GUI said the file was fine
- **Reported by a user**, opening `example/mag06maylgs1_wide_kp_GCOWS_2012a.reg`.

### Symptom

Thirty-odd `AstropyUserWarning`s on the terminal — `A coordinate frame was not found for region:
"box(...)", skipping` — and an empty image. The dialog said `Loaded 0 region(s).` with no
explanation, because the `Report` was empty: nothing in *our* code had skipped anything.

### Root cause

Two failures, one behind the other.

`regions` 0.12 lists `physical` among its **unsupported frames**, and its parser does not treat
that as a local failure: `read.py` sets `frame = None`, and every shape after that line is then
dropped for having no frame. One word on line 4 cost all 29 regions. ds9's two pixel frames differ
only through IRAF's `LTV`/`LTM` section keywords — absent from every reduced cube and mosaic we
handle — so `physical` and `image` are the same numbers here.

The loss was invisible because the library reports through `warnings.warn`. A GUI user never sees
the terminal, and the default warning filter shows a given message once per process, so the second
attempt at the same file printed nothing at all. This module exists to make ds9 conversion losses
explicit; a whole category of them was bypassing the `Report`.

### Fix

`_normalize_pixel_frames()` rewrites `physical` frame lines to `image` before parsing and leaves a
note saying so, including that `LTV`/`LTM` are not applied (the reader is handed a WCS, not a
header). `_parse_shapes()` wraps `Regions.parse` in `warnings.catch_warnings(record=True)` and
folds what it catches into `report.skipped`, deduplicated; anything that is not a `UserWarning` is
re-emitted rather than swallowed. `_report_annotations()` covers the remaining silent hole — a
`#`-prefixed `compass`/`ruler`/`projection` is a *comment* to `regions`, so it produces no region
and no warning either; that same file had a compass in it. `Report.summary()` now counts items
past ten instead of listing them, since a file that fails uniformly fails once per region.

## M21. Arrows in every sky frame but ICRS landed in the wrong place

- **Status:** ✅ FIXED — `ds9_regions.py` (`_sky_tail`, `_length_in_pixels`, `_arrow_from_match`)
  and `sky.py` (`CelestialMap.from_skycoord`), covered by the `sky frames` block of
  `tests/test_ds9_regions.py`
- **Severity:** high — a region silently drawn somewhere it is not
- **Found while answering "which ds9 constructs do we not support?"**, by round-tripping one
  known pixel through every frame instead of reading the code.

### Symptom

A circle and an arrow written on the *same coordinates*, in the same file:

| frame | circle | arrow |
|-------|--------|-------|
| `icrs` | exact | exact |
| `fk5` | exact | 0.09 px off |
| `fk4` | exact | **6954 px off** |
| `galactic` | exact | dropped as "outside this image" |
| `ecliptic` | exact | 248,000 px off |

### Root cause

Shapes go through `regions`, which reads the frame line and hands astropy a `SkyCoord` in that
frame. Arrows are hand-parsed — the library drops `vector(...)` — and the hand-written path took
the two numbers as ICRS RA/Dec degrees no matter what the file declared. `fk4` is B1950, ~0.7°
away; galactic longitude is not RA at all.

Two more conventions were wrong for the same reason. ds9 writes equatorial positions in
**sexagesimal**, where the longitude is in *hours* — `_VECTOR_RE` matched decimal digits only, so
such a line was not recognised as a vector, fell through to `regions`, which treats `# vector(`
as a comment, and vanished **with no warning from anything**. And ds9 puts the unit inside the
length field (`15"`), which the old regex matched and discarded before multiplying by 3600: a
15-arcsecond arrow was drawn 3600 times too long.

### Fix

`CelestialMap.from_skycoord()` takes a `SkyCoord` in any frame and goes through
`astropy.wcs.utils.skycoord_to_pixel`, which transforms into the WCS's own celestial frame first
— the same call `regions` makes for a shape, so the two paths cannot drift. `_sky_tail()` builds
that coordinate, choosing hours or degrees for a sexagesimal longitude by frame;
`_length_in_pixels()` reads `"`, `'`, `d`, `p`, `i` and the bare case (degrees in a sky frame,
pixels in a pixel frame). `_SKY_FRAMES` now maps all seven ds9 names to astropy frames, mirroring
the library's `ds9_frame_map`, with a test asserting the two tables stay equal.

A vector line that does not parse is now reported instead of silently dropped, and an arrow in a
frame that cannot be placed (`wcsa`, `detector`) is refused rather than read as pixels.

## M22. The Windows release build hung for its full six-hour limit

- **Status:** ✅ FIXED — `tests/test_file_open.py` (the `shell_launcher_only` marker and
  `test_on_windows_the_action_explains_itself_instead_of_prompting`) and
  `.github/workflows/release.yml` (`timeout-minutes`)
- **Severity:** high — no Windows binary could be released at all, and the failure took six
  hours to report
- **Reported by CI**, on the Windows leg of the release workflow.

### Symptom

`Run Unit Tests (Non-Linux)` stopped on
`tests/test_file_open.py::test_the_menu_action_installs_nothing_until_the_user_agrees` and
produced no further output. GitHub cancelled the job at its six-hour default
(`Error: The operation was canceled.`). macOS and Linux passed the same test in
milliseconds.

### Root cause

`cli_install.plan()` refuses on Windows — there is no shell launcher to write — and it
refuses *first*, before anything else in the function:

```python
if sys.platform.startswith('win'):
    raise CliInstallError("Installing a shell launcher is only supported on macOS and Linux.")
```

`MainWindow.install_cli_tool()` catches that and calls `QMessageBox.warning(...)`, which is
modal. The test stubbed `confirm_cli_install` — as `AGENTS.md` requires — but that hook is
reached only *after* a successful plan, so on Windows it was never called and the real
warning dialog opened with nothing to dismiss it. Offscreen makes no difference: a modal
`exec` blocks whether or not anything is drawn. Reproduced locally by forcing
`cli_install.sys.platform = 'win32'`.

Three more tests in the same file had the same shape: two hung identically, and
`test_a_write_failure_after_confirmation_is_reported` would have failed on the message text.
None had ever run on Windows, because the suite never got past the first hang.
`tests/test_cli_install.py` was already skipped whole on Windows for exactly this reason —
the GUI half of the feature was simply missed.

The six hours are a separate fault: `release.yml` set no `timeout-minutes`, so any hang runs
to GitHub's default.

### Fix

The four tests that reach the real `plan()` now carry a `shell_launcher_only` skip marker,
matching the module-level skip in `tests/test_cli_install.py`. Windows behaviour is covered
rather than merely skipped: a new test patches `cli_install.sys.platform`, stubs
`QMessageBox.warning`, and asserts the action explains itself and never asks for
confirmation — so it runs on every platform, which is what would have caught this.

`timeout-minutes: 45` on the build job and `20` on each test step cap the blast radius: a
full build is well under fifteen minutes, so the next hang of any kind is reported in a
quarter of an hour instead of six hours.

## M23. A NaN world coordinate was formatted, flooding stderr on Linux

- **Status:** ✅ FIXED — `gui/viewers/image_viewer.py` (`_finite()`, used in `mouse_moved`),
  covered by three tests in `tests/test_image_viewer.py`
- **Severity:** medium — a wrong readout on every platform, and unusable terminal output on
  Linux
- **Reported by a user**, while dragging the Depth Plot aperture across the image.

### Symptom

Dragging a tool's ROI printed, over and over:

```
/…/numpy/lib/_function_base_impl.py:2610: RuntimeWarning: invalid value encountered in do_format (vectorized)
  outputs = ufunc(*args, out=...)
```

Nothing in the message names our code, and it never appeared on macOS.

### Root cause

Three things had to line up.

`do_format` exists in exactly one place in the dependency tree:
`astropy/coordinates/angles/core.py:375`, the inner function `Angle.to_string()` runs through
`np.vectorize`. The warning therefore means **a non-finite angle is being formatted**, and
`mouse_moved()` formats whatever the WCS hands back with no check. A WCS that has lost
keywords, or that is asked about a pixel its projection cannot reach, returns NaN rather than
raising, so the `except Exception` around that block never saw it: the readout displayed the
literal `WCS: RA: nan  |  DEC: nan`. Reproduced by stubbing `pixel_to_world_values`.

The drag is not special. `mouse_moved` is wired to the scene's mouse-move proxy, so the
readout recomputes on *every* mouse event; a drag is simply the longest stream of them a user
generates. A probe driving the aperture across the frame counts one `Angle.to_string()` call
per event.

It is Linux-only because numpy reports this by reading the FPU invalid flag at the ufunc
boundary. x86-64 sets that flag on a quiet-NaN compare; arm64 does not. Confirmed directly —
a `np.vectorize`d function doing `float(nan) > 0` warns on x86 and is silent on an arm64 Mac.
**The Mac is not unaffected, only quiet**; the wrong label is on both.

Why it repeated rather than printing once, as Python's once-per-location rule promises:
`warnings.catch_warnings()` bumps the filter version on exit, which clears the registry that
implements the rule. There are eight of those blocks in the codebase.

### Fix

`_finite()` guards both branches of the readout — the 2D `SkyCoord` path and the 3D
per-axis path — and a non-finite coordinate now reads `WCS: N/A`, the same thing a failed
transform already showed. Nothing non-finite reaches a formatter, so the warning has no
source.

`agent_tests/probes/warn_trace.py` (scratch) prints a full caller stack per warning; the bare
numpy message names only where numpy noticed, never who asked.

## M28. Loading a second catalog froze the window for minutes

- **Status:** ✅ FIXED — `gui/tools/plot_catalog.py` (`populate_table`, and the column-guessing
  helpers beside it), covered by the reload and column-guess tests at the end of
  `tests/test_plot_catalog.py`
- **Severity:** high — the application was unusable, with no indication it would ever return
- **Reported by a user**, loading `jw01939001001_02103_00001_nrcb3_cal_priors_cat_FINAL.fits`
  (4704 rows x 31 columns) onto `jw01939003001_02101_00001_nrcb3_cal.fits`.

### Symptom

The *first* catalog loaded in 0.7 s. Loading a second one — or reloading the same file —
beachballed the window and drove resident memory to several GB. Nothing was logged, and the
first load being fine is what made it look like a data problem rather than a code one.

### Root cause

`populate_table` ended by setting the table header's resize mode:

```python
self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeToContents)
```

`ResizeToContents` is a **persistent mode, not a one-shot sizing**. On the first load the
header is still `Interactive` while the cells go in, so the 4704 x 31 = 145,824 `setItem()`
calls are cheap and the measurement happens once at the end. On every load after that the mode
is *already* `ResizeToContents`, so each `setItem()` invalidates its column and Qt re-measures
all 4704 cells of it before the next insert — about **7 x 10⁸ text measurements on the GUI
thread**.

Measured, real Cocoa platform: first load 0.72 s; second load still running when killed at
144 s. An isolated `QTableWidget` refill of the same dimensions reproduces it with no pyql3
code involved, which is what confirmed the mechanism.

The multi-GB memory did not reproduce in a script (RSS held at 0.40 GB through a 2.5-minute
freeze, and the label items neither leak nor accumulate), so it is most likely queued event
and paint state piling up against a main thread that never returns — a consequence of the
freeze rather than a second defect. Noted rather than proven.

### Fix

Size the columns once, after the cells are in, and leave the header `Interactive` — which also
makes the columns draggable, as `ResizeToContents` did not. Every load is now 0.59 s with flat
memory. The fill is wrapped in `setUpdatesEnabled(False)`, and `update_plot` is suspended while
the columns are assigned, so a load costs one O(rows) plotting pass instead of the three it was
spending.

Two neighbouring faults surfaced from the same report and are fixed with it:

- **The guess could not read the catalog's column names.** `X_COLUMN_NAMES` knew `xcentroid`
  and `x_image` but not photutils' PSF-photometry spelling `x_fit`, so the fallback picked "the
  first two numeric columns" — `id` and `group_id`, because the old numeric test called
  `float()` on row 0 and a string column of digits passes it. 2744 of 4704 sources plotted out
  of bounds. Names are now ranked (`coord_column_rank`), a column is also recognised from its
  axis letter when a separator or a coordinate word follows it, the two axes are kept on one
  spelling (`x_fit` pairs with `y_fit`, never `y_init`), and the numeric fallback tests dtype.
  The same catalog now plots 4703 of 4704.
- **A reload silently discarded the user's own column choice.** `set_catalog_table` called
  `auto_assign_columns` unconditionally, so hand-setting X to `x_fit` and reloading landed back
  on the guess. The X/Y/name/type choice now survives a reload whenever the new table still has
  those columns, and only falls back to guessing when it cannot be honoured.

## M29. A 66k-row catalog took 7.7 s and 1.1 GB to display 30 visible rows

- **Status:** ✅ FIXED — `gui/tools/plot_catalog.py` (`CatalogTableModel`, `column_texts`,
  `populate_table`, `filter_table`, `delete_row`), covered by the model-backed tests at the end
  of `tests/test_plot_catalog.py`
- **Severity:** medium — the tool worked, but a large catalog took ten seconds to appear and
  cost a gigabyte for the rest of the session
- **Reported by a user**, loading `jw01939003001_02101_00001_nrcb3_cal_priors_cat_FINAL.fits`
  (66196 rows x 34 columns, 18 MB).

### Symptom

About 10 s to load, against 0.6 s for a 4704-row catalog of the same shape, and resident memory
up by ~1.1 GB. Found immediately after `M28`, and asked as "is this an astropy problem?"

### Root cause

Not astropy's reader: `read_fits_table` takes **0.02 s** for the whole 18 MB. Two costs, both
per-cell, over 66196 x 34 = 2.25 M cells:

- **`QTableWidget` needs one `QTableWidgetItem` per cell.** 2.25 M objects: 2.85 s and
  **1027 MB**, at about 480 bytes each. That is essentially all of the memory.
- **Reading cells through `Table.Row` costs ~1.7 us each.** `row[col]` goes through numpy's
  masked-array machinery — `MaskedArray.view` -> `__array_finalize__` -> `_update_from` ->
  `_check_fill_value` — building a fresh masked scalar per cell: **3.89 s**. Reading the same
  values column-wise as whole numpy arrays produces byte-identical strings in **0.24 s**.

All of it was spent so that the ~30 rows which fit on screen could be drawn.

### Fix

`CatalogTableModel`, a `QAbstractTableModel` behind a `QTableView`. A cell is formatted when Qt
asks for it, so a load costs nothing per row. **Every row is still present and scrollable** —
what changed is when the work happens, not how much of the catalog is shown; a test asserts the
count *and* reads the last row, since a count check alone passes for a truncating
implementation.

Measured on the reported file: **0.58 s and +62 MB**, from 7.7 s and +1100 MB. The 4704-row
catalog went from 0.6 s to 0.13 s. Search went from 1.59 s per keystroke to 0.04 s.

Three decisions worth keeping:

- **Rows are hidden on the view, not filtered through a `QSortFilterProxyModel`.** A view row is
  then always the catalog row, and both `on_table_selection` and `delete_row` index
  `catalog_data` with it; through a proxy each needs `mapToSource`, and getting that wrong
  deletes a source the user never selected. The proxy is also slower — 1.47 s against 0.02 s.
  `test_a_row_selected_while_filtered_highlights_that_same_source` pins the invariant.
- **`filter_table` brackets its pass in `setUpdatesEnabled(False)`**: `setRowHidden` re-lays the
  view out on every call otherwise, 1.26 s against 0.02 s over 66k rows. Blocking the vertical
  header's signals as well looks like the same optimisation and is **wrong** — no faster, and
  the scroll bar keeps its unfiltered range (1985301 instead of 155691), so the view scrolls
  through empty space. `test_filtering_updates_the_scroll_range` catches it.
- **Column widths sample `COLUMN_WIDTH_SAMPLE_ROWS` (100) rows.** Every sampled cell is a Python
  call into the model, so Qt's default of 1000 costs 0.76 s on this catalog against 0.06 s.

Still outstanding: with 66196 sources in view, ticking **Labels** builds one `pg.TextItem` per
visible source and rebuilds them all 200 ms after every pan. Measured 77 MB for 1954 items,
which extrapolates to ~2.6 GB here. `region_layer.INTERACTIVE_LIMIT` is the pattern to copy —
deliberately left for a follow-up.

## M30. Catalog labels were rebuilt on every pan, with no ceiling

- **Status:** ✅ FIXED — new `gui/label_policy.py` (shared with `region_layer`), and
  `gui/tools/plot_catalog.py` (`update_visible_text_labels`, `_trim_label_pool`,
  `_on_labels_suppressed`), covered by `tests/test_label_policy.py` and the label-density tests at
  the end of `tests/test_plot_catalog.py`
- **Severity:** high — ticking *Labels* on a large catalog locked the window up indefinitely
- **Found while fixing `M29`**, on the same 66196-row catalog, and confirmed by measurement rather
  than by hitting it: the ceiling was added before anyone had to sit through it.

### Symptom

With *Labels* ticked and a wide view, one pan of a 20,000-source catalog took **23.9 s**, and the
redraw repeats 200 ms after every pan. At 66,196 sources it would not finish in any useful time.

### Root cause

`update_visible_text_labels` destroyed every `pg.TextItem` and built a fresh one for each source in
view, on every redraw, with **no upper bound on the count**. Two things made that worse than it
looks:

- **The per-label cost is superlinear** in the items already in the scene — 0.22 ms at 1000 labels,
  0.29 ms at 5000, 0.85 ms at 20,000 — because each `view.addItem` walks the ViewBox and the
  scene's item index degrades as it fills. The same O(live items) effect `AGENTS.md` records for
  `ViewBox.updateAllViewLists`.
- **The region layer had already solved this and the catalog tool had not.** `region_layer` hides
  labels while panning, culls to the visible rect *grown by* `LABEL_CULL_MARGIN`, refuses more than
  `LABEL_SAFETY_LIMIT` at once and says so through `labels_suppressed`, and rebuilds nothing in its
  interactive path. `plot_catalog` had only the first of those four, and its culling used a bare
  `rect.contains()`, so edge labels flickered in and out while panning.

### Fix

The policy — redraw delay, cull margin, density ceiling — moved into **`pyql3/gui/label_policy.py`**
and both overlays now import it. It holds no Qt import, so it is unit-tested without a display. The
ceiling is passed to `LabelDensityGuard.allows` rather than held by it, which keeps each overlay's
limit its own and keeps `region_layer`'s existing "monkeypatch the module constant" test working.

Item *creation* stays per-tool, and the lifecycles differ deliberately: a region's label is built
once with its region and toggled, because the region count is whatever the user drew; a catalog is
asked for one per row, so `plot_catalog` keeps a **pool sized to what is in view** and re-texts it,
trimmed back through the `_retired` + timer + `gc.collect()` pattern from `M18`/`M27`.

Measured, catalog labels, first build against redraw after a pan:

| labels | first build | pan redraw before | pan redraw after |
|--------|-------------|-------------------|------------------|
| 1,000  | 0.22 s      | 0.25 s            | **0.01 s** |
| 5,000  | 1.44 s      | 1.86 s            | **0.03 s** |
| 10,000 | 4.80 s      | —                 | **0.11 s** |
| 20,000 | 20.87 s     | 23.91 s           | **0.12 s** |

`CATALOG_LABEL_LIMIT = 5000` because the first column is superlinear and 5000 is a 1.4 s one-off —
the same value the region overlay settled on for a comparable ~1 s. On the reported catalog, ticking
*Labels* with all 66,196 in view now takes 0.52 s and says *"66,196 labels in view — more than the
5,000 that can be drawn at once. Zoom in to label fewer sources"*; zooming to 991 labels draws them
in 0.35 s, and five pans after that cost 0.54 s in total. Memory across the whole sequence went
435 -> 527 MB, against the ~2.6 GB the old path extrapolated to.

Two smaller things fixed with it: the recovery path used to call `update_plot` from inside
`update_visible_text_labels`, which re-entered the method that called it — it now just restores the
status line the plot last wrote; and `update_plot` no longer destroys the label pool on every
refresh.

## B15. Minor items

| # | File | Issue | Fix |
|---|------|-------|-----|
| M1 | `photometry.py:72` and `:92` | Two `closeEvent` definitions; the second silently overrides the guarded, `try/except`-wrapped first one. Currently latent (pyqtgraph's `removeItem` does remove the annuli), but the surviving version will raise if `roi_inner` was never created. | Delete the second; keep the guarded one and have it also `setParentItem(None)`. |
| M2 | `poller.py:34-45` | `self.watch_path = path` is assigned *before* the `os.path.exists` check, so a nonexistent directory is still persisted to `~/.pyql3/config.json` by `open_polling_config` (`main_window.py:585`). | Validate first, assign only on success. |
| M3 | `poller.py:14`, `:23` | Watches `.fits`/`.fit` only, while the Open dialog accepts `.fits.gz`. | Add `.gz` handling (check the full suffix chain). |
| M4 | `arithmetic.py:150-163` | The Active-Image operand is not cast to float, unlike the file operand (`:161`). ~~Not currently reachable for integer wraparound (the other operand is always float)~~ **Correction (2026-10-21): reachable.** Both operands offer "Active Image", so Active + Active on a uint16 frame adds in uint16: 40000 + 40000 gave 14464. See R23. | `return self.image_viewer.raw_data.astype(float), ...`. |
| M5 | `arithmetic.py:199-201` | `final_header = h1 if h1 is not None else ...` mutates the operand's header in place via `add_history`. For the file operand that object is fresh, but for `Active Image` it is a `.copy()` — fine today, fragile if the copy is ever dropped. | `final_header = (h1 or h2 or fits.Header()).copy()`. |
| M6 | ✅ FIXED — `main.py` | `--catalog` was not passed through `os.path.expanduser`, so `~/cat.csv` was silently ignored (unlike `--poll-dir`). | Done: expanded, and a missing file now says so on stderr instead of being ignored. `--regions` was added the same way, and `tests/test_region_ui.py` asserts every command-line path expands `~`. |
| M7 | `advanced_plots.py:44` | Downsample factor uses `img_data.shape[0]` only, so a 100×4096 array is not downsampled. | `ds = max(1, max(img_data.shape) // 256)`. |
| M8 | `advanced_plots.py:48`, `fitting.py:243` | `np.nan_to_num(data, nan=np.nanmedian(data))` warns and yields NaN for an all-NaN region. | Guard with `np.all(np.isnan(...))` and substitute 0. |
| M9 | `advanced_plots.py:134-138` | `ContourDialog.update_plot` returns early when *Show Contours* is unchecked, before `clear_contours()`, so contours from a previous slice can linger. | Clear first, then return. |
| M11 | ✅ FIXED — `main_window.py` | `create_menus` kept its `QMenu` objects in locals only, so nothing held a Python reference. Harmless while unwrapped, but any code calling `action.menu()` becomes a transient Python owner and PySide6 deletes the C++ menu **and every QAction in it** when that wrapper is collected. Confirmed while writing the Phase 4 region-menu tests: a helper that walked `menuBar().actions()` and called `.menu()` got back an action that was already dead. | Done: every menu is now stored on `self` (`self.file_menu`, `self.display_menu`, `self.scaling_menu`, `self.colormap_menu`, `self.units_menu`, `self.plot_menu`, `self.analysis_menu`, `self.region_menu`, `self.help_menu`, joining `self.window_menu` and `self.recent_menu`). **Reach a menu through its attribute, never through `action.menu()`** — see `tests/test_region_ui.py::menu_action`. |
| M27 | ✅ FIXED — `base_tool.py` (`remove_roi_from_viewer`, `_drop_retired_rois`) | `remove_roi_from_viewer` ended with `self.roi = None`, dropping the last Python reference to a `QGraphicsItem` — and every caller removes an ROI *in order to build its replacement*. The collector therefore ran inside `ROI.addScaleHandle` → `GraphicsObject.__init__` and freed the old item mid-construction: `Fatal Python error: Segmentation fault ... Garbage-collecting`, from `toggle_roi_shape`. Latent for as long as the shape combo has existed; a test that switches shape twice made it reproducible. Exactly `M18`'s failure in a second place. | Done: removed ROIs go to `_retired_rois` and are released from a `QTimer.singleShot(0, ...)` with an explicit `gc.collect()`, since these items are in reference cycles. Nine consecutive clean runs of the file and the full suite. |
| M31 | ✅ FIXED — `base_tool.py` (`_drop_retired_rois`), covered by `tests/test_analysis_tools.py` running to the end | Follow-up to M27. After Region Statistics and Peak Fit moved to `roi_plane_pixels`, `tests/test_analysis_tools.py` died with `Fatal Python error: Segmentation fault` in `gc.collect()` (`_drop_retired_rois`, reached from the `destroy_leftover_windows` fixture) at the teardown of `test_gaussian_fit_tool` after `test_cuts_tool`; lldb showed `Shiboken::callCppDestructor<QGraphicsObject>` on a bad pointer. Reproduction: those two tests in that order with any extra object created in `GaussianFitDialog.__init__` (a parentless `QLabel` was enough); the code before the change survived `gc.set_threshold` from 20 to 500, so the order in which the collector frees the ROI, handle and menu cycle is what matters. **Residual exposure (found in review):** pyqtgraph's `GraphicsScene` keeps strong references (`dragItem`, `lastHoverEvent.clickItems()`), so a ROI removed while it is being dragged or hovered is deleted in C++ while the scene still points at it, and the next scene click can raise `RuntimeError: Internal C++ object already deleted` (a traceback, not a crash). Checked cheaply: after removing a ROI set as `scene.dragItem`, the scene still holds it and `shiboken6.isValid` is False; a stubbed drag-finish event did not raise, so the traceback itself was not reproduced. Not fixed. | Done: `_drop_retired_rois` calls `shiboken6.delete` on each still-valid retired ROI before `gc.collect()`, which invalidates the wrappers so the collector skips them. The file and the full suite pass. `region_layer._drop_retired` is unchanged. |
| M26 | ✅ FIXED — `tests/test_poller.py`, `poller.py` (`_check_pending`) | `test_burst_of_files_displays_only_the_newest` spaced its five files **one nanosecond** apart with `os.utime`. NTFS timestamps resolve to 100 ns, so on Windows all five were the same age, `max(candidates, key=_mtime)` returned the first maximal element, and the burst announced `burst0` instead of `burst4`. Failed the Windows release build; passed everywhere else because APFS and ext4 resolve to 1 ns. | Done: the test spaces them a second apart and *asserts the premise* — that `os.stat` really recorded five increasing ages — so a coarser filesystem says so rather than looking like a poller bug. Separately, `_check_pending` now breaks ties by name (`key=lambda p: (self._mtime(p), p)`), because timestamps genuinely collide in this poller's deployment: an NFS share can report whole seconds, and a DRP burst lands several frames in one tick. |
| M23 | ✅ FIXED — `.github/workflows/release.yml`, `build_app.sh` | The packaged-asset check globbed `dist/**/regions/_geometry/*`, which the `__init__.py` and the `tests/` directory collected alongside the extensions already satisfy — so it passed on a build containing **no compiled `_geometry` extension at all**, which is the only thing `collect_all('regions')` is there for. Verified by running the step against a `dist/` tree holding just `_geometry/__init__.py`: it reported success. Found while auditing the Windows build (`M22`), where the extensions are `.pyd`. | Done: both copies now filter on `importlib.machinery.EXTENSION_SUFFIXES`, so only a real `.so`/`.pyd` counts. Re-verified against fake `dist/` trees with and without one. |
| M24 | ✅ FIXED — `poller.py` (`_watch_key`) | The canonical watch key was `realpath(abspath(expanduser(path)))` with no `normcase`. On Windows `C:\Data\OSIRIS` and `c:\data\osiris` are one directory but two keys, so two windows could each start a `PollingObserver` over it — the double scan traffic and double load that `watcher_of` exists to prevent. | Done: `os.path.normcase` on the result, a no-op on POSIX. `tests/test_multi_window.py::test_watch_ownership_compares_canonical_paths` asserts the case fold where `normcase` actually folds. |
| M25 | ✅ FIXED — `build_app.bat` | The Windows build script ran `uv add --dev pyinstaller pillow`, which re-resolves and rewrites `uv.lock` — the exact hazard removed from `release.yml`, still present for anyone building on Windows. Both packages are already in the locked `dev` group. | Done: `uv sync --frozen`. |
| M10 | `header_editor.py:104-136` | `apply_table_edits` writes back *every* row, including structural cards (`SIMPLE`, `BITPIX`, `NAXIS*`), and coerces types by string-parsing, so `'2.0'` string values silently become floats. | Only write rows whose text differs from the original card, and skip the structural keywords. |

---

## Working checklist

- [x] B0 Depth Plot / Gaussian Fit crash when opened from the menu bar
- [x] B1 Diagonal Cut spinbox `TypeError` (+ frozen ROI signals)
- [x] B2 Depth Plot background connections moved out of `set_center`
- [x] B3 HistEq on integer data (+ `Negative` unsigned wraparound, HistEq sample/needle mixup)
- [x] B4 Timeline ↔ slider/readout sync (+ reverse desync on reload, `current_z()`)
- [x] B5 Stale `FitsReader` cache on same-path reload (stat-keyed, memmap off, `force`)
- [x] B6 Non-image extensions in the dropdown (+ loud `set_data` for other ndim)
- [x] B7 Catalog markers not removed on close (GC-masked; one removal helper)
- [ ] B8 Strehl tool — see implementation_plan_strehl.md (entry's own fix is wrong; 4 defects)
- [ ] B9 Peak-fit surface transposed
- [x] B10 Diagonal Cut width control
- [ ] B11 Vertical Cut stale background curve
- [x] B12 Collapse-range clamping (single-definition z-range helpers)
- [ ] B13 Depth Plot un-rotation axis lengths
- [ ] B14 Deduplicate the coordinate transforms
- [ ] B15 Minor items M1–M10
- [x] B16 All-NaN plane raises out of `set_data` (+ non-finite manual levels)
- [x] B17 Depth Plot cuts sample one channel while a collapse is displayed
- [x] B18 Header save over the open file fails on Windows (Windows CI)
- [ ] B19 Open file handle can block an external writer on Windows — needs a decision

---

# Security & Data-Integrity Review — 2026-08-01

A structured review of `pyql3/`, `main.py`, `QuickLook3.spec`, `pyproject.toml` and
`.github/workflows/release.yml`, run in six phases against the threat model for what this
application actually is: a local desktop viewer with no server, no auth and no network
client. `S`-numbered to distinguish these from the `B` series above. Every finding below was
reproduced before being written up, and every fix is covered by a test.

**Baseline (Phase 0).** `pip-audit`: no known vulnerabilities. `bandit`: 0 HIGH, 0 MEDIUM.
`ruff`: 47 findings, all style/dead-code, now cleared.

**Two hypotheses were disproved and are recorded so nobody re-investigates them:**

- *A partial read of a file still being written could be displayed as science.* It cannot.
  Truncation always raises — `OSError` mid-header, `ValueError: cannot reshape` mid-data —
  because `memmap=False` forces an eager read that fails rather than serving padded garbage.
- *A header declaring enormous `NAXIS*` could exhaust memory.* It cannot. A 2,880-byte file
  claiming a 5000³ float32 cube (~500 GB) fails in 0.01 s with no allocation attempted.

## S0. Directory polling delivered no events at all over NFS

- **Status:** ✅ FIXED — `services/poller.py` (rewritten), `main_window.py`, `dialogs/polling.py`,
  covered by `tests/test_poller.py`
- **Severity:** high (the feature silently did nothing in the deployment it exists for)
- **Files:** `pyql3/services/poller.py`, `pyql3/gui/main_window.py:on_file_detected`

### Symptom
`watchdog.observers.Observer` resolves to a kernel backend — `FSEventsObserver` on macOS,
`InotifyObserver` on Linux. Both report only changes made through the *local* kernel. The
OSIRIS DRP frequently writes **from another host onto an NFS share**, which generates no
local event, so `on_created` never fired. The feature appeared to work in testing purely
because test files are created locally.

A second, independent defect: when events *did* arrive, `on_file_detected` scheduled exactly
one load attempt 500 ms later and there was no `on_modified` handler. A file still being
written at that moment failed to parse, showed a raw astropy error dialog, and was **never
retried** — the frame was lost even though it completed a second later.

### Reproduction
Header written at t+0, write completed at t+2.0 s; at t+4.8 s the application displayed
nothing and had raised one `Error: cannot reshape array of size 0` dialog.

### Fix
`PollingObserver` (scans directory snapshots, so remote writes are seen), configurable
interval defaulting to 2 s and persisted to config. A file is announced only once
`(st_size, st_mtime_ns)` holds steady across consecutive scans; `on_modified` is handled so a
late-completing file is revisited. Because NFS attribute caches can report a stale size, a
failed parse is treated as "not ready yet" and retried with backoff (0/1/2/4/8 s), warning
only once retries are exhausted. Bursts are coalesced — a 12-file bulk copy now produces one
load, of the newest file, instead of flashing each frame in turn. `.fits.gz` was also being
silently ignored, because `os.path.splitext` returns `.gz`.

## S1. A corrupt config file stopped the application from starting

- **Status:** ✅ FIXED — `services/config.py`, covered by `tests/test_config.py`
- **Severity:** medium (unrecoverable from the GUI, trivially reachable)
- **File:** `pyql3/services/config.py` — `load()`

### Symptom
`load()` caught only `json.JSONDecodeError`. Anything else propagated out of `__init__`,
which `MainWindow` calls at line 29 — so the window never opened, with nothing on screen to
explain why. The only fix available to a user was deleting the file from a terminal.

### Reproduction
```
~/.pyql3/config.json containing non-UTF-8 bytes -> UnicodeDecodeError
~/.pyql3/config.json existing as a directory     -> IsADirectoryError
~/.pyql3/config.json containing "[1, 2, 3]"      -> parses, then AttributeError on .get()
```
The third was found while fixing the first two: valid JSON of the wrong shape loaded cleanly
and broke every later config read.

### Fix
Broadened to `(OSError, ValueError, UnicodeDecodeError)` plus a `dict` type check. A damaged
file is moved to `config.json.corrupt` — preserved, not deleted, since it may be the only
record of a recent-files list worth recovering — the reason is logged, and defaults are used.
`save()` is now atomic (temp file, `fsync`, `os.replace`) so an interrupted write cannot
truncate a good config into the state `load()` then has to quarantine.

## S2. The poller matched `FitsReader`'s own save temp file

- **Status:** ✅ FIXED — `services/poller.py`, covered by `tests/test_poller.py`
- **Severity:** low (timing-dependent, no corruption observed)
- **File:** `pyql3/services/poller.py` — `is_fits_path()`

### Symptom
`save()` creates `.pyql3_save_*.fits` in the same directory as its target, so saving into a
watched directory offered our own half-written temp file to the poller as a new frame.
Benign in most timings — `os.replace` is quick and settling needs two stable scans — but a
slow save over NFS could leave it visible long enough to settle, racing the save. Newly
reachable as a consequence of the S0 rewrite.

### Fix
`is_fits_path()` rejects dotfiles; no legitimate instrument product is hidden. The check also
moved into `_add_candidate()`, so the invariant holds at the single point candidates enter
rather than depending on every caller.

## S3. Saving a file stripped its group and world permissions

- **Status:** ✅ FIXED — `core/fits_reader.py`, covered by `tests/test_fits_reader.py`
- **Severity:** medium (silent, delayed, affects collaborators rather than the user)
- **File:** `pyql3/core/fits_reader.py` — `save()`

### Symptom
```
mode before save : 0664   (group-writable, as on a shared reduction directory)
mode after  save : 0600
```
`tempfile.mkstemp` creates 0600 by design, and `os.replace` carries the temp file's mode onto
the destination. Editing a header and saving therefore locked collaborators out of a shared
Keck reduction directory — with no warning, and only discovered later when someone hit
permission denied.

### Fix
The target's mode is captured before the replace and restored after. A path that does not yet
exist (Save As) gets the umask default instead, since inheriting 0600 would make a newly
exported file unreadable to the group for no reason.

**Not fixed:** if the file is owned by another user, `os.replace` makes the saved copy ours.
Restoring ownership requires privileges the application does not have.

## S4. The Header Editor could make a science file unreadable

- **Status:** ✅ FIXED — `core/fits_reader.py`, `dialogs/header_editor.py`, covered by
  `tests/test_fits_reader.py`
- **Severity:** medium (one edit, permanent, on a file being overwritten in place)
- **Files:** `pyql3/core/fits_reader.py` — `update_header_card()`;
  `pyql3/gui/dialogs/header_editor.py`

### Symptom
Setting `SIMPLE = F` and saving left the primary HDU unrecognised as an image
(`get_data()` → `None`), and `save()` overwrites in place. `NAXIS`/`NAXISn`/`BITPIX` turned
out to be harmless because astropy regenerates them from the data array on write — but that
means editing them silently reverts, which merely confuses.

### Fix
`FitsReader.PROTECTED_KEYWORDS` and `is_protected_keyword()`; `update_header_card()` now
returns a bool. The editor reports refusals, but only for keywords whose value actually
differs from the header — its table lists every card including the structural ones, so
reporting unconditionally would have nagged on every save.

## S5. Every cut profile was wrong in Total DN mode

- **Status:** ✅ FIXED — `gui/tools/cuts.py`, covered by `tests/test_cuts.py`
- **Severity:** **high (wrong science numbers presented as correct)**
- **File:** `pyql3/gui/tools/cuts.py` — `update_plot()`

### Symptom
A flat 7.0 image with `ITIME*COADDS = 10` plotted **700 instead of 70**. `update_plot()` took
its pixels from `display_data`, which `apply_transforms()` has already multiplied, then
multiplied by `data_multiplier` again — squaring the DN/s to Total DN conversion. Horizontal,
vertical and diagonal cuts were all affected. This is exactly the double-application hazard
`docs/developer.md` warns about; `depth_plot.py` pairs `current_plane()` with an explicit
multiply and was already correct.

The same two lines carried a second documented violation: indexing `display_data` by
`imv.currentIndex`, which goes stale in Boxcar and Z Range modes where the screen shows a
collapsed plane belonging to no single channel (see B17).

### Fix
Both resolved by reading `current_plane()`, which returns the displayed plane without the
multiplier folded in.

## S6. Release binaries were not built from the committed lockfile

- **Status:** ✅ FIXED — `.github/workflows/release.yml`
- **Severity:** medium (supply chain; unreviewed code reaching users)
- **File:** `.github/workflows/release.yml`

### Symptom
The workflow ran `uv add --dev pyinstaller pillow` after `uv sync`. That was redundant —
both are already in the locked `dev` group, which plain `uv sync` installs — and harmful:
`uv add` re-resolves and rewrites `uv.lock`, so published binaries could be built against
dependency versions nobody had reviewed.

### Fix
`uv sync --frozen`, which builds from `uv.lock` exactly and fails if the lock is stale.
Hardened alongside it: all six Actions pinned to commit SHAs (a floating `@v4` is mutable by
whoever owns the action repository and runs in a job holding a token); a top-level
`permissions: contents: read` with only the `release` job escalating to `contents: write`;
and `SHA256SUMS.txt` published with every release, since the bundles are unsigned and
un-notarized and users had nothing to verify a download against.

## S7. A file with no image data was displayed as a black square

- **Status:** ✅ FIXED — `core/fits_reader.py`, covered by `tests/test_fits_reader.py` and
  `tests/test_main_window_and_poller.py`
- **Severity:** **high (fabricated data indistinguishable from a real observation)**
- **File:** `pyql3/core/fits_reader.py` — `load()`

### Symptom
`load()` ended by substituting `np.zeros((10, 10))` and an empty `Header` whenever no
displayable image extension was found. Because that made `data` never `None`, it silently
disabled every `if data is None` guard in the application — including the one in
`load_fits()`, whose "No valid data found in FITS file." branch was unreachable dead code.

A structurally valid FITS carrying no image HDU (header-only, or table-only) was therefore
**displayed as a black 10×10 square**, with the window title set to the filename and the file
added to Recent Files. Nothing distinguished it from a real observation of an empty field.
Reachable both from File → Open and from the polling auto-load.

### Fix
The fallback is removed; `data` stays `None` and the existing guards do what they were
written to do. All three consumers already handled `None` correctly, which is why this was a
deletion rather than a rewrite. The primary header is still published, so a file worth
inspecting but not displaying can still be opened in the Header Editor. Loading a data-less
file now also leaves the currently displayed cube untouched.

## S8. Developer paths disclosed in a public repository

- **Status:** ✅ FIXED — `AGENTS.md`, `mkdocs.yml`, `gh-pages` history purged
- **Severity:** low (username and directory layout only; no credentials)

### Symptom
Two absolute home-directory paths in `AGENTS.md` and one in `implementation_plan_strehl.md`.
Separately, `docs/hooks.py` lives inside `docs_dir`, so importing it left a
`docs/__pycache__/*.pyc` that mkdocs copied verbatim into the built site — and a `.pyc`
embeds the absolute path of the machine that compiled it. `gh-deploy` published it, bypassing
the root `.gitignore`. It was live at
`https://astrodatalab.github.io/pyql3/__pycache__/hooks.cpython-312.pyc` (HTTP 200).

### Fix
Paths replaced with the `PYQL3_QL2_REF` / `PYQL3_TEST_CUBE` environment variables. `mkdocs.yml`
gained `exclude_docs` for `__pycache__/` and `*.pyc`, and the `gh-pages` branch history was
purged and redeployed. No absolute path had ever been committed to `main`, so no rewrite of
the main branch was needed.

## Working checklist — security review

- [x] S0 Directory polling delivered no events over NFS (+ lost frames, no coalescing)
- [x] S1 Corrupt config stopped the application from starting
- [x] S2 Poller matched `FitsReader`'s own save temp file
- [x] S3 Saving stripped group/world permissions
- [x] S4 Header Editor could make a file unreadable via `SIMPLE = F`
- [x] S5 Cut profiles double-applied the DN multiplier
- [x] S6 Release binaries not built from the committed lockfile
- [x] S7 Data-less FITS displayed as a fabricated black square
- [x] S8 Developer paths disclosed in a public repository
- [x] Data-integrity round-trip guard added (`tests/test_data_integrity.py`)
- [x] `ruff` configured and the 47-finding backlog cleared
- [ ] Phase 1 residue (low): hostile header strings in the UI, duplicated/contradictory
      `CTYPE`, NaN/Inf through every tool, `output_verify` policy on load and save
- [ ] `on_moved` trusts `event.dest_path`; a FIFO named `*.fits` would block `fits.open`
      on the GUI thread. Reasoned, not tested — testing it hangs the harness.
- [ ] Remote-write detection unverified on real hardware. `PollingObserver` is confirmed
      active, but nobody has yet watched an actual NFS share while another host writes.

---

# Code review — 2026-10-21

A review of `pyql3/` and `main.py`, split into seven areas: the ImageViewer; MainWindow, the
poller, config and command line; the Depth Plot; the other tools and FITS I/O; the Plot Catalog;
the region GUI; and the region model with its YAML and ds9 I/O. Each area was read against
`AGENTS.md` and against every entry above, so nothing here repeats a known bug; where an entry
resembles an earlier one, its **Related** line says how it differs. Every entry was reproduced by
running code unless its Status says otherwise, and each carries its reproduction.

`R`-numbered to distinguish these from the `B`, `M` and `S` series, and ordered by severity:
**R1–R10 high, R11–R47 medium, R48–R64 low**, and R65–R80 in the minor-items table at the end.
R81–R83 (medium) were added afterwards from a NIFS reference cube; they sit just before the table.
R84 (minor) was added in the Phase 2 review and is the last row of the table.
The implementation plan is `specs/2026-10-21-code-review-fixes-plan.md`.

**Baseline.** `uv run pytest -q -n auto --dist loadfile`: 1081 passed, 6 skipped (the two
real-cube tests, with `PYQL3_TEST_CUBE` unset, and four deliberate skips). The suite catches
none of the bugs below.

**Why the suite misses them.** The fixtures are a square 40×40 cube in ICRS with `ITIME = 10` s,
square pixels and the default axis mapping, and no test sets a view rotation. Most entries here
fail on one of: a non-square cube, an FK5 header (which every OSIRIS cube has), `ITIME` in
milliseconds, a rotated or non-square-pixel WCS, swapped display axes, or a view rotation. The
aperture cross-check between the Depth Plot and Aperture Photometry passes because both tools
receive the same, wrong, centre (R2).

**Causes shared by many entries.**

1. **Most code never hears that the displayed plane changed.** `ImageViewer.display_changed`
   exists, but only `RegionLayer` listens to it. `apply_view_rotation` emits nothing, and neither
   do z-mode or z-range changes. The tools that do refresh rely on `imv.sigTimeChanged`, which the
   collapse and boxcar paths bypass. → R7, R8, R14, R36, R45, R68.
2. **Three coordinate systems are mixed.** A numpy index has the pixel centre at *i*, an
   ImageItem coordinate has it at *i* + 0.5, and a ViewBox coordinate differs from the ImageItem
   coordinate whenever a view rotation is set. `coords.item_to_index` exists, but the aperture
   tools do not call it, and nothing provides an ImageItem → view mapping. → R2, R6, R29, R45.
3. **Two redisplay paths.** `refresh_display` always shows the full cube; only `apply_z_range`
   and `apply_z_slice` know about collapse and boxcar. → R5, R27, R57.
4. **No single tool lifecycle.** Some openers reuse a visible dialog and others build a new one
   on every click, and Escape (`QDialog.reject`) hides a dialog without running `closeEvent`.
   → R15, R16.
5. **Headers are matched by substring or position, not through the WCS.** `'WAVE' in ctype`,
   `'RA' in CTYPE1`, `SkyCoord(...)` defaulting to ICRS, RA/Dec passed by position, and `ITIME`
   assumed to be in seconds. → R1, R10, R17, R24, R25, R61.

**Documentation corrected with this review** (the code was right and the text was wrong):
`quicklook3 cube.fits` does not reach a running copy of the application, because the launcher
`exec`s a new process — corrected in `AGENTS.md`, `README.md`, `docs/cli.md`, `TODO.md`, the
`window_manager.py` docstring and the `tests/test_multi_window.py` docstring. `docs/tools.md` no
longer says catalog search filters the markers. M4 above now records that its integer path is
reachable. `AGENTS.md` notes the openers that currently break the `TOOL_DIALOG_ATTRS` reuse rule
(R16). Where the documentation describes the intended behaviour and the code is what is wrong
(`AWAV` in R24, same-named line lists in R54, per-region `frame: image` in R40), the text is
unchanged and the entry is the fix.

## R1. "As Total DN" multiplies by ITIME, which is in milliseconds for current OSIRIS data, so Total DN is 1000 times too large

- **Status:** ✅ FIXED — `pyql3/core/header_info.py`, `pyql3/gui/viewers/image_viewer.py`, covered by `test_header_info.py::test_truitime_wins_over_millisecond_itime`, `::test_itime_alone_is_seconds`, `::test_zero_itime_falls_back_to_truitime`, `::test_no_exposure_keywords_means_one`, `::test_coadds_multiply`, `test_image_viewer.py::test_total_dn_uses_truitime_when_itime_is_milliseconds`, `::test_real_osiris_cube_total_dn_uses_truitime`
- **Severity:** high — every Total DN number in the application is wrong by a factor of about 1000 for post-upgrade OSIRIS cubes, with nothing on screen to indicate it
- **Files:** `pyql3/gui/viewers/image_viewer.py:1082-1086`, `pyql3/gui/viewers/image_viewer.py:1227-1231`, `tests/conftest.py:114`
- **Related:** S5, which squared this same multiplier in the cut tool. This entry is about the value of the multiplier itself.

### Symptom
`example/s180722_a005002_Kn3_035.fits` (a 2018 OSIRIS cube) has `ITIME = 900000` and
`TRUITIME = 899.92` ("Actual integratime time of each coadd", in seconds), with
`BUNIT = 'ADU per second per coadd'`. `data_multiplier` comes out as **900000**, where the
integration time is 899.92 s. **Display → As Total DN** therefore shows values about 1000 times too
large. The tools review measured the same scaling in every place that uses `data_multiplier`: each
cut, fit and photometry value, the pixel readout, and the colorbar.

The current QL2 in the OSIRIS DRP reads `TRUITIME` by default, with a comment that the H2RG upgrade
changed what `ITIME` and `COADDS` mean. Cubes from before the upgrade may still carry `ITIME` in
seconds.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.io import fits
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

# The integration-time cards of a 2018 OSIRIS cube (example/s180722_a005002_Kn3_035.fits)
h = fits.Header({'ITIME': 900000, 'TRUITIME': 899.92, 'COADDS': 1,
                 'BUNIT': 'ADU per second per coadd'})
v = ImageViewer()
v.set_data(np.ones((8, 6, 5), np.float32), h)
v.disp_as_dn = True
print("data_multiplier", v.data_multiplier, "| TRUITIME * COADDS", 899.92 * 1)
```
```
data_multiplier 900000 | TRUITIME * COADDS 899.92
```

### Root cause
`set_data` (`image_viewer.py:1082-1086`) takes `ITIME` and reads `TRUITIME` only when `ITIME == 0`.
No unit is applied. Nothing in the suite can catch this, because the only fixture with an
integration time sets `ITIME = 10.0` (`tests/conftest.py:114`).

### Fix (proposed)
In `set_data`, prefer `TRUITIME` when it is present and finite, as the current QL2 does, and fall
back to `ITIME` otherwise. Keep the choice in one small helper so the rule is stated once.

### Regression test (proposed)
`tests/test_image_viewer.py::test_total_dn_uses_truitime_when_itime_is_milliseconds`: a header with
`ITIME = 900000, TRUITIME = 899.92, COADDS = 1`, asserting `data_multiplier == pytest.approx(899.92)`.
A second case with only `ITIME = 10` keeps the current behaviour for headers without `TRUITIME`.

---

## R2. Circular apertures are measured half a pixel away from the circle drawn on the image

- **Status:** ✅ FIXED — `pyql3/gui/tools/depth_plot.py`, `photometry.py`, `strehl.py`, covered by `test_a_circle_drawn_on_a_pixel_measures_that_pixel` (`tests/test_depth_plot.py`), `test_photometry_circle_drawn_on_a_pixel_measures_that_pixel` and `test_strehl_aperture_is_centred_on_the_drawn_circle` (`tests/test_analysis_tools.py`)
- **Severity:** high — every circular measurement in three tools comes from a different
  place than the one the user drew, and small apertures lose up to a sixth of a star's flux
- **Files:** `pyql3/gui/tools/depth_plot.py:729-741`, `:1557-1568`, `:540-548`, `:865-876`;
  `pyql3/gui/tools/photometry.py:113-133`; `pyql3/gui/tools/strehl.py:76-79`, `:94`;
  `pyql3/gui/viewers/image_viewer.py:1608-1611`
- **Related:** B13/B14 (coordinate arithmetic done outside `coords.py`). This one is not a
  flip or rotation error but a mix-up between two of the three pixel conventions in the
  `coords.py` module docstring.

### Symptom
A circle drawn centred on a pixel does not measure a circle centred on that pixel. The
measured circle is offset by +0.5 px in x and in y, and the sky rings are offset with it.
- Depth Plot: a single 100-count pixel inside an r=1 circle drawn on it gives Total
  78.54, not 100.
- Depth Plot: a FWHM 1.6 px star with the circle drawn on its peak pixel gives -16.2% at
  r=1.5, -5.2% at r=2 and -0.2% at r=3. The Aperture Photometry tool reports the same
  wrong 741.0 at r=1.5.
- Aperture Photometry and Strehl tools: an image whose value equals x gives a mean x of
  40.50 inside an aperture centred on x=40. A FWHM 2.5 px star gives -15.5% at r=1.5,
  -6.4% at r=2 and -1.9% at r=3. A single 1000-count pixel inside an r=1 circle reports
  785.
- **Plot Depth...** from the right-click menu passes the clicked pixel's index, which
  draws the circle on that pixel's lower-left corner while measuring at its centre. The
  drawing and the measurement still disagree, in the other direction.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from photutils.aperture import CircularAperture
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

data = np.zeros((31, 21, 2))      # (x, y, z): with no header, display x is FITS axis 3
data[12, 8, :] = 100.0            # one 100-count pixel at display pixel (12, 8)
v = ImageViewer(); v.set_data(data, None)
d = DepthPlotDialog(image_viewer=v)
d.spin_radius.setValue(1.0)
d.set_center((12.5, 8.5))         # the ImageItem draws pixel 12 over [12, 13)
print("image rect:", v.imv.getImageItem().boundingRect())
print("Depth Plot Total, circle drawn on the pixel:", d.plot_data.getData()[1][0])
img = v.transposed_data[0].T      # photutils wants (row, col) == (y, x)
print("photutils, same circle in index coordinates:",
      CircularAperture((12, 8), r=1.0).do_photometry(img)[0][0])
```
```
image rect: PySide6.QtCore.QRectF(0.000000, 0.000000, 31.000000, 21.000000)
Depth Plot Total, circle drawn on the pixel: 78.53981633974485
photutils, same circle in index coordinates: 100.0
```

### Root cause
- A pyqtgraph ImageItem draws pixel `i` over `[i, i+1)`, so its centre is at `i + 0.5`.
  photutils puts pixel `i`'s centre at `i`.
- `DepthPlotDialog.aperture_geometry()` (`depth_plot.py:738-741`) returns the ROI centre in
  ImageItem coordinates, and `update_plot()` passes it unchanged to `annulus_spectrum()`
  (`:1562-1568`). `photometry.py:113-116` and `strehl.py:76-79` read `roi.pos() +
  size/2` the same way and pass it to photutils (`photometry.py:131-133`) and to
  `calculate_strehl` (`strehl.py:94`).
- `coords.index_to_item()` / `item_to_index()` (`pyql3/core/coords.py:138-153`) exist
  for exactly this conversion and are not used by any of the three tools.
- The right-click position is stored as an integer index (`image_viewer.py:1608-1611`),
  and `set_center()` (`depth_plot.py:540-548`) places the ROI's centre at that number as if
  it were an ImageItem coordinate.

The existing tests cannot see this, for three reasons:
- `tests/test_spectral_photometry.py::test_the_cross_check_against_the_photometry_tool`
  passes the same `(cx, cy)` numbers to both implementations and never involves an ROI. It
  checks that the two agree with each other, and they do, because both read the ROI centre
  without converting it.
- `tests/test_depth_plot.py::test_the_spectrum_does_not_depend_on_how_the_view_is_oriented`
  places the centre with `orig_to_display()`, which works in index coordinates. The measured
  circle therefore lands on the same data pixel in every orientation. Only the drawn circle
  is off, and the test never looks at it.
- `test_the_aperture_lands_on_x_y_not_y_x` calls `spectral_photometry` directly with index
  coordinates.

### Fix (proposed)
- Convert in one place in each tool. `aperture_geometry()` should return
  `coords.item_to_index()` of the ROI centre (the radius is unchanged).
- Use `coords.index_to_item()` wherever a centre becomes an ROI position: `set_center()`,
  `sync_annulus_rings()`, `on_radius_changed()` and the default centre at
  `depth_plot.py:448-458`. The right-click index and the Center X/Y spins then mean "this
  pixel's centre".
- Apply the same conversion in `photometry.py:113-116` and `strehl.py:76-79`.

### Regression test (proposed)
- `tests/test_depth_plot.py::test_a_circle_drawn_on_a_pixel_measures_that_pixel`: one
  100-count pixel, with an r=1 `CircleROI` whose drawn centre is the pixel's ImageItem
  centre. Assert Total == 100 to 1e-9. It currently gives 78.54.
- Write the same test against `PhotometryDialog`.

## R3. Save FITS As writes only the displayed extension, and replaces the target by deleting it first

- **Status:** ✅ FIXED — `pyql3/gui/main_window.py` (`save_file_as` calls `FitsReader.save()` and keeps any suffix `is_fits_path` accepts; the dialog filter is now `FITS_FILE_FILTER`), covered by `test_save_as_keeps_every_extension_and_the_mode`, `test_save_as_onto_the_open_file_keeps_its_extensions`, `test_save_as_keeps_a_fits_suffix_it_recognises` in `tests/test_data_integrity.py`
- **Severity:** high — saving onto the open file permanently deletes every other extension and the primary header
- **Files:** `pyql3/gui/main_window.py:700-717`, `pyql3/core/fits_reader.py:209-274`
- **Related:** S3 (permissions) and B18 (Windows open-handle save). Both were fixed in `FitsReader.save()`, which this path does not call.

### Symptom
`save_file_as()` writes `fits.PrimaryHDU(current data, current header)` and nothing else. The consequences:

- For a file of PRIMARY (no data) + SCI + ERR + DQ, the output is one HDU, `[('SCI', (3, 4))]`. ERR, DQ and the primary header (`TELESCOP`, `ITIME`) are gone.
- Choosing the open file as the target rewrites the original to `['SCI']`. Nothing asks for confirmation beyond the file dialog's overwrite prompt, and there is no backup.
- `writeto(overwrite=True)` removes the target and then creates it, so a write that fails partway has already deleted the original.
- The file mode is not kept. With umask 022 a 0664 file comes back 0644 (`same inode: False`), while `FitsReader.save()` keeps 0664.
- The suffix check is `endswith('.fits')`: `frame.fit` is written as `frame.fit.fits`, `frame.FITS` as `frame.FITS.fits` and `frame.fits.gz` as an uncompressed `frame.fits.gz.fits`.
- Not run, reasoned from B18: on Windows, saving over the open file should fail with `PermissionError`, because astropy's remove-then-create cannot delete a file the reader still holds open.

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import os, tempfile, numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication, QMessageBox, QFileDialog
import pyql3.services.config as cfg
app = QApplication([]); d = tempfile.mkdtemp()
cfg._shared_config = cfg.ConfigManager(d + "/config.json")
QMessageBox.information = staticmethod(lambda *a, **k: None)
from pyql3.gui.main_window import MainWindow
src = d + "/jwst_like.fits"
p = fits.PrimaryHDU(); p.header["TELESCOP"] = "JWST"; p.header["ITIME"] = 30.0
fits.HDUList([p] + [fits.ImageHDU(np.ones((3, 4), "f4"), name=n)
                    for n in ("SCI", "ERR", "DQ")]).writeto(src)
os.umask(0o022); os.chmod(src, 0o664)
win = MainWindow(); win.load_fits(src)
QFileDialog.getSaveFileName = staticmethod(lambda *a, **k: (src, ""))
win.save_file_as()                                   # File -> Save FITS As..., the open file
with fits.open(src) as h:
    print([x.name for x in h], "TELESCOP" in h[0].header, oct(os.stat(src).st_mode & 0o777))
# -> ['SCI'] False 0o644
```

### Root cause
`main_window.py:713-714` builds a new single-HDU file from `fits_reader.get_data()` and `get_header()`, which are only the current extension (`fits_reader.py:77-86`). It then writes with astropy's `overwrite=True`. `FitsReader.save()` (`fits_reader.py:209-274`) already writes the whole HDUList to a sibling temp file, restores the mode and swaps it in with `os.replace`. Save As bypasses all of that.

### Fix (proposed)
Call `self.fits_reader.save(filepath)` from `save_file_as()`. It writes every HDU atomically, keeps the mode, and reopens the original afterwards. Accept any name for which `poller.is_fits_path()` is true, and append `.fits` only otherwise.

### Regression test (proposed)
`tests/test_data_integrity.py::test_save_as_keeps_every_extension_and_the_mode`: save a PRIMARY+SCI+ERR+DQ file onto itself through `MainWindow.save_file_as` with the dialog stubbed. Assert that the HDU names, the primary header's `TELESCOP` and the 0664 mode all survive.

---

## R4. A frame auto-loaded while Edit FITS Header is open is overwritten with the previous frame's header

- **Status:** ✅ FIXED — `pyql3/gui/main_window.py` (`edit_header` runs the editor inside `auto_load_held()`; while it is active, a new detection or a queued retry that comes due stores its path, and the newest is loaded when it exits), `pyql3/gui/dialogs/header_editor.py` (the editor records the reader's `filepath` and `hdul` when it opens; `apply_table_edits` and `save_header` warn and write nothing if either has changed), covered by `test_auto_load_during_header_edit_does_not_rewrite_the_new_frame`, `test_an_auto_load_held_by_the_header_editor_runs_after_it_closes`, `test_a_file_opened_by_hand_cancels_a_held_auto_load` and `test_header_editor_refuses_to_save_after_its_file_changed` (`tests/test_main_window_and_poller.py`), and `test_a_retry_queued_before_the_header_editor_opens_waits_for_it` (`tests/test_poller.py`)
- **Severity:** high — the new frame's OBJECT, DATE-OBS and WCS on disk are replaced with the previous frame's, and the user's edit to the previous frame is lost
- **Files:** `pyql3/gui/main_window.py:794-800`, `pyql3/gui/main_window.py:1354-1355`, `pyql3/gui/dialogs/header_editor.py:104-143`, `pyql3/gui/dialogs/header_editor.py:145-180`
- **Related:** M10 (`apply_table_edits` writes back every row). This is the same write-every-row mechanism with a different trigger: the reader changes file while the editor is open.

### Symptom
The user has polling on, opens File → Edit FITS Header on frame 1 and edits a comment. Frame 2 arrives and is displayed while the editor is still open. The user clicks Save Header and answers Yes.

- Frame 2 on disk then has `OBJECT GC_field_A`, `DATE-OBS 2025-10-21T08:00:00` and `CRVAL2 266.4`, all copied from frame 1.
- Frame 1 is unchanged and the user's edit is not in it.
- The confirm prompt names frame 2's path, but the table still lists frame 1's cards, so nothing on screen shows that the target changed.

This was measured with a real `PollingObserver` at a 0.3 s interval. The snippet below calls the poller's slot directly.

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import tempfile, numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication, QMessageBox
import pyql3.services.config as cfg
app = QApplication([]); d = tempfile.mkdtemp()
cfg._shared_config = cfg.ConfigManager(d + "/config.json")
QMessageBox.question = staticmethod(lambda *a, **k: QMessageBox.StandardButton.Yes)
QMessageBox.information = staticmethod(lambda *a, **k: None)
from pyql3.gui.main_window import MainWindow
from pyql3.gui.dialogs.header_editor import HeaderEditorDialog
def frame(path, obj, crval):
    h = fits.Header(); h["OBJECT"] = obj; h["CRVAL2"] = crval
    fits.PrimaryHDU(np.ones((8, 6, 6), "f4"), header=h).writeto(path)
f1, f2 = d + "/s251021_a001001.fits", d + "/s251021_a002001.fits"
frame(f1, "GC_field_A", 266.40)
win = MainWindow(); win.load_fits(f1)
editor = HeaderEditorDialog(win.fits_reader, win)    # File -> Edit FITS Header (frame 1)
frame(f2, "GC_field_B", 266.55)
win.on_file_detected(f2)          # the poller announces frame 2 while the editor is open
editor.save_header()              # Save Header -> Yes
h2 = fits.getheader(f2)
print(h2["OBJECT"], h2["CRVAL2"])
# -> GC_field_A 266.4
```

### Root cause
`edit_header()` runs the dialog with `exec()`, a nested event loop, so the poller's timer and queued signals keep running during it. `on_file_detected` → `load_fits` → `FitsReader.load()` replaces the reader's HDUList with frame 2 (`fits_reader.py:62-70`). The dialog holds the reader object, not the HDUList it was opened on. `apply_table_edits` therefore writes every row of frame 1's table into `fits_reader.get_header(ext)`, which is now frame 2's header (`header_editor.py:104-143`), and `save_header` saves it to frame 2's path (`header_editor.py:176`).

### Fix (proposed)
While the header editor is open, `MainWindow` should queue auto-loads, keeping only the newest path, and run the newest after the dialog closes. As a second guard, `HeaderEditorDialog` should record `fits_reader.filepath` and the `hdul` object when it opens, and refuse to apply or save if either has changed.

### Regression test (proposed)
`tests/test_main_window_and_poller.py::test_auto_load_during_header_edit_does_not_rewrite_the_new_frame`: the snippet above as a test. Assert that frame 2's `OBJECT` and `CRVAL2` on disk are unchanged after `save_header()`.

---

## R5. Flip, rotate, unit change, axis change and reload replace a collapsed or boxcar plane with a single channel

- **Status:** ✅ FIXED — `pyql3/gui/viewers/image_viewer.py` (`show_current_plane`, which `refresh_display` now calls: `apply_z_range()` in Z Range mode, `apply_z_slice()` otherwise, and for a collapsed plane `_give_imageview_the_cube()` first so the timeline matches the new cube, and which puts the view rotation back after `imv.setImage` resets the ImageItem's transform; `apply_axis_mapping` writes Z Max only when the z length changed), `docs/developer.md`, covered by `test_redisplay_keeps_the_collapsed_plane`, `test_redisplay_keeps_the_boxcar_plane`, `test_reload_keeps_zmax_when_the_length_is_unchanged`, `test_a_collapse_keeps_the_timeline_of_the_new_cube` and `test_an_invalid_z_range_keeps_the_view_rotation` (`tests/test_image_viewer.py`)
- **Severity:** high — the image is a different plane from the one the label and the tools describe, and this happens in the live-observing reload path
- **Files:** `pyql3/gui/viewers/image_viewer.py:1216-1225`, `pyql3/gui/viewers/image_viewer.py:1212`, `pyql3/gui/viewers/image_viewer.py:1565-1567`, `pyql3/gui/tools/rotate.py:121`, `pyql3/gui/tools/rotate.py:127`, `pyql3/gui/main_window.py:779`
- **Related:** B4, which made a reload re-assert the slider's slice. That fix applies in Z Slice mode only. B17 made the tools read the collapsed plane, so the tools and the screen now disagree in the opposite direction.

### Symptom
With **Collapse Region** active (Sum over channels 4-8 of a cube where channel *k* holds *k*, so the
displayed value is 30), any of the following replaces the image with channel 0 of the cube and shows
the timeline again:

- **Rotate Image → Flip Horizontal** or a 90° step
- **Display → As Total DN** / **As DN/s**
- changing an AXIS combo box
- **Display → Redisplay image**, or a poller auto-load

The label keeps reading "Collapsed: 4-8". A reload also rewrites **Z Max** to the last channel, so
the label changes to "Collapsed: 4-19", and `current_plane()`, which the tools use, sums channels
4-19 (184.0). In **Boxcar** 5 at channel 10, a flip shows channel 10 alone (0.284 in a test with a
different value per channel), while the label and `current_plane()` still give the boxcar median
over 8-12 (2.941).

The tools review measured the same thing separately: Z Range 2-4 with Sum showed 9.0, and after
Rotate 90° it showed 0.0 while the label still read "Collapsed: 2-4". After a reload in range mode,
three things each reported a different plane: the screen, the readout (the slider's channel) and
the tools that read `imv.currentIndex` (0).

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.wcs import WCS
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

w = WCS(naxis=3); w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
w.wcs.crval = [2.2, 34.0, 242.0]; w.wcs.cdelt = [0.0005, 1e-4, 1e-4]; w.wcs.cunit = ['um', 'deg', 'deg']
cube = np.zeros((16, 12, 20), np.float32); cube[:] = np.arange(20)   # channel k holds k
v = ImageViewer(); v.set_data(cube, w.to_header())
v.combo_collapse.setCurrentText("Sum"); v.txt_zmin.setText("4"); v.txt_zmax.setText("8")
v.radio_range.setChecked(True)
shown = lambda: float(np.nanmean(v.imv.getImageItem().image))
print("collapsed      :", shown(), "|", v.lbl_slice_info.text())
v.flip = True; v.refresh_display()          # what Rotate Image > Flip Horizontal does
print("after flip     :", shown(), "|", v.lbl_slice_info.text())
v.flip = False; v.apply_z_range()
v.set_data(cube, w.to_header())             # what Redisplay and a poller auto-load do
print("after reload   :", shown(), "| Z Min/Max", v.txt_zmin.text(), v.txt_zmax.text(),
      "|", v.lbl_slice_info.text(), "| current_plane", float(v.current_plane().mean()))
```
```
collapsed      : 30.0 | Collapsed: 4-8, Wavelengths: 2.2025-2.2045 µm
after flip     : 0.0 | Collapsed: 4-8, Wavelengths: 2.2025-2.2045 µm
after reload   : 0.0 | Z Min/Max 4 19 | Collapsed: 4-19, Wavelengths: 2.2025-2.2100 µm | current_plane 184.0
```
Going through `MainWindow.load_fits` followed by `redisplay_image()` gives the same numbers.

### Root cause
`refresh_display()` (`image_viewer.py:1216-1225`) always sets `display_data` to a copy of the full
cube and redisplays it through `imv.setImage`. It never consults the z mode. These all call it:

- `RotateDialog.on_rotate_changed` and `on_flip_changed` (`rotate.py:121`, `:127`)
- `MainWindow.set_display_unit` (`main_window.py:779`)
- `apply_axis_mapping`, and so every `set_data`

`update_image_display` re-asserts the slider's index only in Z Slice mode (`:1565-1567`), so in
range mode the ImageView stays at index 0. Separately, `apply_axis_mapping` writes `zs-1` into
`txt_zmax` on every call (`:1212`).

### Fix (proposed)
At the end of `refresh_display`, dispatch on the z mode: call `apply_z_range()` when
`radio_range` is checked, `apply_z_slice()` when `boxcar_width() > 1`, and keep the current path
otherwise. In `apply_axis_mapping`, write `txt_zmax` only when the cube's z length has changed.

### Regression test (proposed)
`tests/test_image_viewer.py::test_redisplay_keeps_the_collapsed_plane`, parametrized over flip, a
90° rotation, `disp_as_dn`, an axis change and `set_data` with the same cube. Each case asserts that
the rendered image equals `current_plane()` (30.0 in the channel cube), and that Z Min / Z Max are
unchanged. Add a boxcar variant using a cube with a different, non-monotonic value per channel.

---

## R6. The hover readout ignores the view rotation, so X, Y, Value and RA/Dec belong to a different pixel

- **Status:** ✅ FIXED — `pyql3/gui/viewers/image_viewer.py` (`mouse_moved` maps through the ImageItem and `coords.item_to_pixel`), covered by `test_readout_follows_the_view_rotation`, `test_readout_pixel_and_sky_position_agree_under_flip_and_rotation` and `test_readout_left_of_the_image_is_out_of_bounds` (`tests/test_image_viewer.py`)
- **Severity:** high — wrong numbers presented as correct whenever **North Up** or any view angle is set
- **Files:** `pyql3/gui/viewers/image_viewer.py:1631-1632`
- **Related:** M15. The right-click path (`on_view_clicked`, `:1608`) and the tool ROIs (`base_tool.py:191`, `:205`) map through the ImageItem; the hover readout is the one path that does not.

### Symptom
On a 40×30 image whose value is `1000*x + y`, hovering over pixel (5, 20) reports:

| view rotation | readout |
|---|---|
| 0° | X: 5, Y: 20, Value: 5020 |
| 90° | X: 13, Y: 10, Value: 13010 |
| 37° | X: 6, Y: 14, Value: 6014 |

The WCS line is computed from the same wrong pixel. Separately, a cursor at x = -0.6, which is
outside the image, reads pixel 0.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

nx, ny = 40, 30
img = (1000 * np.arange(nx)[None, :] + np.arange(ny)[:, None]).astype(np.float32)  # value = 1000*x + y
v = ImageViewer(); v.resize(800, 800); v.show(); app.processEvents()
v.set_data(img, None); app.processEvents()
item = v.imv.getImageItem()

def hover(ix, iy):   # put the cursor over the item coordinate (ix, iy)
    v.mouse_moved((item.mapToScene(QPointF(ix, iy)),))
    return v.lbl_x.text(), v.lbl_y.text(), v.lbl_val.text()

print("no rotation, pixel (5, 20):", hover(5.5, 20.5))
for angle in (90.0, 37.0):
    v.apply_view_rotation(angle); app.processEvents()
    print(f"view rotation {angle}, pixel (5, 20):", hover(5.5, 20.5))
v.apply_view_rotation(0.0); app.processEvents()
print("cursor at x = -0.6, outside the image:", hover(-0.6, 5.5))
```
```
no rotation, pixel (5, 20): ('X: 5', 'Y: 20', 'Value: 5020 DN/s')
view rotation 90.0, pixel (5, 20): ('X: 13', 'Y: 10', 'Value: 13010 DN/s')
view rotation 37.0, pixel (5, 20): ('X: 6', 'Y: 14', 'Value: 6014 DN/s')
cursor at x = -0.6, outside the image: ('X: 0', 'Y: 5', 'Value: 5 DN/s')
```

### Root cause
`mouse_moved` converts the cursor with `getView().mapSceneToView(pos)` (`:1631`), which gives
ViewBox coordinates. `apply_view_rotation` puts its rotation in a `QTransform` on the ImageItem, so
ViewBox coordinates are not pixel coordinates once a rotation is set. `int()` at `:1632` rounds
toward zero, so every x in (-1, 0) becomes 0 and passes the bounds check.

### Fix (proposed)
Map with `self.imv.getImageItem().mapFromScene(pos)` and convert with `coords.item_to_pixel()`,
which uses `floor`. This is the mapping the right-click and the tools already use.

### Regression test (proposed)
`tests/test_image_viewer.py::test_readout_follows_the_view_rotation`: the encoded image above,
parametrized over view rotations 0, 37, 90 and 180, hovering over the scene position of pixel
(5, 20) and asserting `Value: 5020`. Add one case asserting that a cursor at item x = -0.6 reports
"Out of Bounds".

---

## R7. The Depth Plot is not recomputed when the viewer's data or orientation changes

- **Status:** open
- **Severity:** high — after the poller loads a new frame, the plot keeps showing the
  previous frame's spectrum, with nothing to say it is stale
- **Files:** `pyql3/gui/main_window.py:607-650`, `pyql3/gui/viewers/image_viewer.py:59`,
  `:1098`, `:1225`, `pyql3/gui/tools/depth_plot.py:1505-1510`, `pyql3/gui/tools/cuts.py:93`
- **Related:** B17 fixed *which* plane the cut types read. This entry is about *when*
  anything is read. B11 is a stale curve after a type switch; here the trigger is a change
  in the viewer.

### Symptom
With the dialog open, the plotted curve is left unchanged by each of the following, until
the ROI is next moved:
- **Loading a new cube**, from File > Open or from a polling auto-load. A cube whose
  channel z holds z, replaced by one holding 50 + z, still plots `[0 1 2 3 4]`.
- **A flip, a 90° rotation or an AXIS 1/2/3 remap.** On a cube whose value equals x, an
  aperture at x=5 plots 5.0 before and after a flip; a recompute gives 24.0. After
  remapping X to FITS axis 1, the cube has 30 channels but the plot still shows 3 points.
- **Moving the slice slider in Horizontal or Vertical Cut mode.** The cut stays at the old
  slice.
- **Loading a 2-D image.** The previous cube's spectrum stays drawn, and an explicit
  `update_plot()` does not clear it.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

cube = lambda base: np.broadcast_to(base + np.arange(5.0), (30, 20, 5)).copy()  # channel z = base + z
v = ImageViewer(); v.set_data(cube(0.0), None)
d = DepthPlotDialog(image_viewer=v)
d.combo_calc.setCurrentText("Average")
print("cube A:              ", d.plot_data.getData()[1])
v.set_data(cube(50.0), None)      # what MainWindow.load_fits (and the poller) does
print("after loading cube B:", d.plot_data.getData()[1])
d.combo_type.setCurrentText("Horizontal Cut")
v.slider_slice.setValue(1); d.update_plot()
print("cut at slice 1:", d.plot_data.getData()[1][0])
v.slider_slice.setValue(4); app.processEvents()
print("slider moved to", v.current_z(), "-> cut still", d.plot_data.getData()[1][0])
d.combo_type.setCurrentText("Depth Plot")
v.set_data(np.full((40, 40), 7.0), None)   # a 2-D image
d.update_plot()
print("after a 2-D load:", d.plot_data.getData()[1])
```
```
cube A:               [0. 1. 2. 3. 4.]
after loading cube B: [0. 1. 2. 3. 4.]
cut at slice 1: 51.0
slider moved to 4 -> cut still 51.0
after a 2-D load: [50. 51. 52. 53. 54.]
```

### Root cause
- `MainWindow.load_fits()` (`main_window.py:607-650`) calls `ImageViewer.set_data()` and
  notifies no tool dialog. `update_tools_for_unit()` is the only thing that calls a tool's
  `update_plot()`, and only on a unit change.
- The viewer announces geometry changes through `display_changed` (`image_viewer.py:59`,
  emitted at `:1098` for a 2-D load and at `:1225` from `refresh_display()`). Its only
  listener is `region_layer.py:272`.
- The Cut Plot tool connects `imv.sigTimeChanged` (`cuts.py:93`). The Depth Plot's cut
  types do not.
- `update_plot()` returns at `depth_plot.py:1509-1510` for non-3-D data without clearing
  the three curves.

### Fix (proposed)
- Connect `image_viewer.display_changed` to `update_plot` in `DepthPlotDialog.__init__`,
  and disconnect it in `closeEvent`.
- For the cut types, connect the same slice signal `cuts.py:93` uses.
- In the non-3-D early return, clear `plot_data`, `plot_bg` and `plot_sub`, call
  `sync_legend()`, and say in `lbl_bg_info` why nothing is drawn.

### Regression test (proposed)
- `tests/test_depth_plot.py::test_a_new_cube_replaces_the_plotted_spectrum`: open the
  dialog on cube A, call `viewer.set_data(cube B)`, and assert that the plotted values are
  B's without calling `update_plot()`.
- Add the same test for a flip (on a value = x cube) and for a 2-D load (assert all three
  curves are empty).

## R8. Catalog markers, labels and the highlight stay where they were drawn when the displayed image changes

- **Status:** open
- **Severity:** high — every marker is drawn at the wrong position, and nothing on screen says so
- **Files:** `pyql3/gui/tools/plot_catalog.py:899-1000`, `pyql3/gui/tools/plot_catalog.py:1063-1123`,
  `pyql3/gui/viewers/image_viewer.py:59`, `pyql3/gui/viewers/image_viewer.py:1098`,
  `pyql3/gui/viewers/image_viewer.py:1225`, `pyql3/gui/viewers/image_viewer.py:1380-1400`, `main.py:117-126`

### Symptom
Marker positions are computed once, in display coordinates, when `update_plot` runs. They are not
recomputed when the image under them changes. Every one of these leaves the markers, the selection
highlight and the labels where they were:

- a flip or a 90° rotation in the Rotate tool. A source at orig (5, 7) on a 60 x 40 image stays at
  (5.5, 7.5) after a flip, where it should move to (54.5, 7.5);
- a change to the AXIS 1/2/3 mapping, a change of extension, opening another file, and a frame
  auto-loaded by the poller. In World mode the markers keep the previous frame's pixel positions,
  so on the next dither they mark the wrong stars. The status line also goes stale: after
  switching to a 20 x 20 image it still reads "2 plotted | 0 out of bounds" for a source at (50, 30);
- a catalog loaded before any image (`--catalog` with no filename, or `--poll-dir` on an empty
  directory). An RA/Dec catalog reports "1 unusable coordinates" and still plots nothing after an
  image with a WCS is opened;
- a view rotation. The markers are children of the ImageItem and rotate with it, but the labels are
  added to the ViewBox at positions mapped once, so they stay behind: label at (5.5, 7.5), marker at
  (41.5, 15.5). The labels move to the right place only on the next pan or zoom.

Changing any catalog control (marker size, for example) re-runs `update_plot` and puts the markers
back in the right place, which makes the fault look intermittent.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.table import Table
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
viewer = ImageViewer()
viewer.set_data(np.zeros((40, 60), np.float32))     # orig dims (60, 40)
dlg = PlotCatalogDialog(None, viewer)
dlg.set_catalog_table(Table({"name": ["a", "b"], "x": [5.0, 50.0], "y": [7.0, 30.0]}), "t")
first = lambda: [float(v[0]) for v in dlg.scatter_item.getData()]
print("loaded  ", first())

viewer.flip = True                                  # what the Rotate tool's Flip does
viewer.refresh_display()
print("flipped ", first(), "expected", [c + 0.5 for c in viewer.orig_to_display(5, 7)])

viewer.flip = False
viewer.set_data(np.zeros((20, 20), np.float32))     # a new file; source b is now off the image
print("new file", first(), "|", dlg.lbl_status.text())
```
```
loaded   [5.5, 7.5]
flipped  [5.5, 7.5] expected [54.5, 7.5]
new file [5.5, 7.5] | Loaded: 2 sources | 2 plotted | 0 out of bounds
```

### Root cause
`ImageViewer.display_changed` (`image_viewer.py:59`) exists for this purpose. It is emitted from
`set_data` for a 2-D image (`:1098`) and from `refresh_display` (`:1225`), which every flip, rotation,
axis change and new cube goes through. `RegionLayer` connects to it (`region_layer.py:272`) and
`PlotCatalogDialog` does not. The only viewer signal the catalog listens to is `sigRangeChanged`,
and only for its labels. `apply_view_rotation` (`image_viewer.py:1380-1400`) sets a `QTransform` and
emits nothing, and the labels are positioned through `img_item.mapToParent` (`plot_catalog.py:1098`),
so they follow a view rotation only when a pan or zoom triggers the next redraw.

### Fix (proposed)
Connect `image_viewer.display_changed` in `PlotCatalogDialog.__init__` to a slot that re-runs the
marker, label and highlight pass without recentring the view (see R60), and disconnect it in
`closeEvent`. Have `apply_view_rotation` emit a signal (or `display_changed`) so that the labels are
re-placed after a view rotation too.

### Regression test (proposed)
`test_markers_follow_a_flip_and_a_new_image` in `tests/test_plot_catalog.py`. Load a catalog, set
`viewer.flip = True`, call `refresh_display()`, and assert that each scatter point equals
`viewer.orig_to_display(x, y)` plus 0.5. Then `set_data` a smaller image and assert that the status
line counts the source that now lies off the image as out of bounds.

## R9. Region Statistics and Peak Fit measure interpolated samples, and count the area off the image as zeros

- **Status:** ✅ FIXED — `pyql3/core/coords.py` (`box_index_slices`), `pyql3/gui/tools/base_tool.py` (`roi_plane_pixels`), `statistics.py`, `fitting.py`, covered by `test_box_index_slices` (`tests/test_coords.py`), `test_statistics_reads_whole_pixels`, `test_statistics_says_when_the_box_misses_the_image`, `test_statistics_reports_the_collapsed_plane`, `test_peak_fit_fwhm_does_not_depend_on_box_phase`, `test_peak_fit_background_ignores_the_part_of_the_box_off_the_image`, `test_peak_fit_says_when_the_box_misses_the_image` and `test_peak_fit_box_is_drawn_over_the_pixels_it_measures` (`tests/test_analysis_tools.py`). Peak Fit now reports its centre as a numpy index, and `set_center` places the box with `coords.index_to_item`
- **Severity:** high — Min, Max, Mean, StdDev, FWHM and background are wrong whenever the box is at a fractional position, which is where a mouse drag always leaves it
- **Files:** `pyql3/gui/tools/statistics.py:50-54`, `pyql3/gui/tools/fitting.py:241-259`
- **Related:** B4 notes that these tools read the right *plane*; this is about how they read the pixels of that plane. B10 is the other `getArrayRegion` entry, about the diagonal cut's width.

### Symptom
Flat 100 image with a single 1100 pixel at display (40, 30), 10×10 Statistics box:

| box position | Min | Max | Mean | Total Pixels |
|---|---|---|---|---|
| (35, 25) | 100 | **1100** | 110 | 100 |
| (35.5, 25.5), same pixel inside | 100 | **350** | 110 | 100 |
| (-5, 25), half off the left edge | **0** | 100 | **50** | **100** |

Peak Fit of a round star with FWHM 2.50 px on a sky of 10, 20×20 box: FWHM 2.50 at (30, 20) and
**2.79** at (30.5, 20.5). With the star at x = 3 and the box over the edge, Background is
**6.23**.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from astropy.io import fits
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.statistics import StatisticsDialog
from pyql3.gui.tools.fitting import GaussianFitDialog
img = np.full((60, 80), 100.0); img[30, 40] = 1100.0       # one hot pixel at display (40, 30)
v = ImageViewer(); v.set_data(img, fits.Header())
s = StatisticsDialog(None, v)
for pos in [(35, 25), (35.5, 25.5), (-5, 25)]:
    s.roi.setPos(*pos); s.roi.setSize([10, 10]); s.update_stats()
    print(pos, {k: s.value_labels[k].text() for k in ("Min", "Max", "Mean", "Total Pixels")})
yy, xx = np.mgrid[0:60, 0:80]; sig = 2.5 / 2.3548            # round star, FWHM 2.5 px, sky 10
for x0 in (40.0, 3.0):
    v.set_data(1000 * np.exp(-((xx - x0)**2 + (yy - 30)**2) / (2 * sig**2)) + 10, fits.Header())
    f = GaussianFitDialog(None, v)
    for pos in ([(30, 20), (30.5, 20.5)] if x0 == 40 else [(-7, 20)]):
        f.roi.setPos(*pos); f.roi.setSize([20, 20]); f.update_fit()
        print(x0, pos, "FWHM X", f.value_labels["FWHM X"].text(), "Background", f.value_labels["Background"].text())
```
```
(35.5, 25.5) {'Min': '100.0000', 'Max': '350.0000', 'Mean': '110.0000', 'Total Pixels': '100'}
(-5, 25) {'Min': '0.0000', 'Max': '100.0000', 'Mean': '50.0000', 'Total Pixels': '100'}
40.0 (30.5, 20.5) FWHM X 2.79 Background 9.8994
3.0 (-7, 20) FWHM X 2.52 Background 6.2267
```

### Root cause
Both tools take their pixels from `roi.getArrayRegion()`. In pyqtgraph 0.14.0 that goes through
`functions.affineSlice` with `order=1`, so a box at a fractional position returns linear
interpolations between neighbouring pixels, not pixels. This lowers peaks and broadens profiles,
and a NaN spreads to four samples. Samples outside the array come from `interpolateArray`'s
`default=0.0`, so they are real zeros to `data[~np.isnan(data)]` (`statistics.py:54`) and to the
fit, whose initial offset is `data.min()` (`fitting.py:258-259`).

### Fix (proposed)
Add one helper on `BaseToolDialog` that returns the whole pixels a rectangular ROI covers. It
should take `coords.item_to_pixel` of the box edges, clip them to the plane, and slice. Use it in
Statistics and Peak Fit. Report how many pixels lay outside the image instead of filling them.

### Regression test (proposed)
`tests/test_analysis_tools.py::test_statistics_reads_whole_pixels`: with the box at (35.5, 25.5),
Max is 1100. With the box half off the edge, Min is 100 and Total Pixels is 50.
`test_peak_fit_fwhm_does_not_depend_on_box_phase`: FWHM agrees within 1% at integer and
half-integer box positions.

---

## R10. Sky-anchor conversions read the WCS's own coordinates as ICRS, so a file saved on a galactic image cannot be loaded back

- **Status:** open
- **Severity:** high — a file the program wrote itself raises out of the loader, and the frame offset it ignores applies to every OSIRIS cube
- **Files:** `pyql3/core/ds9_regions.py:409-413`, `pyql3/core/regions_io.py:185-210`, `pyql3/core/regions_io.py:237-245`, `pyql3/core/regions_io.py:302-320`, `pyql3/core/regions_model.py:134-147`, `pyql3/gui/main_window.py:1023-1032`
- **Related:** M21 fixed the same assumption for hand-parsed ds9 arrows (`_arrow_from_match` converts the tail to ICRS first); the native file's sky anchors still make it.

### Symptom
Three triggers, one cause:

- **Galactic WCS (GLON/GLAT).** Save a box or arrow on the image, then load the file on the same image: `load_regions` raises `numpy.linalg.LinAlgError: SVD did not converge`. `MainWindow.load_regions_from` catches only `RegionFormatError` and `OSError`, so Load Regions prints a traceback and shows nothing. `--regions` at startup (`main.py:132`) has no handler at all, so startup ends there.
- **RADESYS = 'FK4'.** A box saved at 30° loads back on its own image at 29.614°. With the menu, the frame question is asked even though the file is on the image it was drawn on.
- **FK5 file on an ICRS image.** The bundled OSIRIS cube declares `RADESYS = 'FK5'` (`EQUINOX = 2000`), while the JWST NIRSpec s3d SCI header declares `RADESYS = 'ICRS'`. A circle saved on Sgr A* in an FK5 image lands at (48.30, 49.54) instead of (49.00, 49.00) on an ICRS image of the same star with 36 mas pixels: 0.9 px, the 31.6 mas FK5/ICRS offset. The note says "this file was drawn on an image pointed differently from this one".

### Reproduction
```python
import os, tempfile
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from pyql3.core.regions_model import Box, Circle
from pyql3.core.regions_io import save_regions, load_regions

def wcs(ctype, crval, cdelt=1e-4, radesys=None, equinox=None):
    w = WCS(naxis=2)
    w.wcs.ctype, w.wcs.crval, w.wcs.cdelt, w.wcs.crpix = ctype, crval, [-cdelt, cdelt], [50, 50]
    if radesys:
        w.wcs.radesys, w.wcs.equinox = radesys, equinox
    return w

path = os.path.join(tempfile.mkdtemp(), "r.yml")
box = Box(x=60.0, y=40.0, width=8.0, height=3.0, angle=30.0)
fk4 = wcs(['RA---TAN', 'DEC--TAN'], [265.6, -28.9], radesys='FK4', equinox=1950.0)
save_regions(path, [box], wcs=fk4)
print("FK4, own image: 30 ->", round(load_regions(path, wcs=fk4)[0].regions[0].angle, 3))
star = SkyCoord(266.41683708, -29.00781056, unit="deg")          # Sgr A*, ICRS
save_regions(path, [Circle(x=49.0, y=49.0, radius=3.0)],
             wcs=wcs(['RA---TAN', 'DEC--TAN'], [star.fk5.ra.deg, star.fk5.dec.deg], 1e-5, 'FK5', 2000.0))
c = load_regions(path, wcs=wcs(['RA---TAN', 'DEC--TAN'], [star.ra.deg, star.dec.deg], 1e-5))[0].regions[0]
print("FK5 file on ICRS image, expected (49, 49):", round(c.x, 2), round(c.y, 2))
gal = wcs(['GLON-TAN', 'GLAT-TAN'], [0.0, 0.0])
save_regions(path, [box], wcs=gal)
load_regions(path, wcs=gal)
```
```
FK4, own image: 30 -> 29.614
FK5 file on ICRS image, expected (49, 49): 48.3 49.54
numpy.linalg.LinAlgError: SVD did not converge
```

### Root cause
`SkyAnchor` (`regions_model.py:134-147`) stores `ra_deg`/`dec_deg` with no frame. `sky_anchor_for` (`regions_io.py:302-320`) fills them from `CelestialMap.to_sky` (`sky.py:94-103`), which returns the WCS's own world coordinates: (l, b) for GLON/GLAT, B1950 for FK4, FK5 J2000 for FK5. `_from_sky_anchor` (`regions_io.py:185-210`) places them with `from_sky` (`sky.py:105-114`), which reads them in the *loading* WCS's frame with no conversion. That is the FK5-on-ICRS offset.

`_set_angle` (`regions_io.py:237-245`) then calls `sky_angle_to_pixel` (`ds9_regions.py:409-413`), which builds `SkyCoord(ra_deg, dec_deg)`. Its default frame is ICRS. `regions` transforms that point into the WCS's frame and takes the local Jacobian there:
- For FK4, that point is about 0.7° from the region, so north is rotated slightly differently there.
- For galactic, (0, 0) read as ICRS is (l, b) ≈ (96°, -60°), off the TAN projection. The Jacobian is NaN and `np.linalg.svd` raises.

### Fix (proposed)
Record the frame in `SkyAnchor`: write it to the file and read it back. Take it from `astropy.wcs.utils.wcs_to_celestial_frame(mapping.wcs2d)` when the anchor is made. A file without one means "the loading WCS's frame", as now. Build the anchor's `SkyCoord` in that frame, place it with `CelestialMap.from_skycoord`, and give `sky_angle_to_pixel` the `SkyCoord` rather than bare numbers. In `placed_on_image`, catch exceptions from the conversion and count the region as stranded.

### Regression test (proposed)
`tests/test_region_ui.py`, beside `test_a_rotated_field_turns_a_sky_anchored_box_with_it`:
- `test_a_file_saved_on_a_galactic_image_loads_back`: no exception; angle within 0.01°.
- `test_an_fk4_image_keeps_a_box_angle`: `choose_frame` is never called.
- `test_an_fk5_anchor_lands_on_the_same_star_on_an_icrs_image`: within 0.05 px of the star.

## R11. Auto-load retries are never cancelled, so a late retry replaces a newer frame or a file the user opened

- **Status:** ✅ FIXED — `pyql3/gui/main_window.py` (`_auto_load_generation`, advanced by `on_file_detected`, by `load_fits(cancel_auto_load=True)` and when the watch stops; `_attempt_auto_load` returns when its generation is stale or polling is off; `load_fits` clears `_last_load_error` on entry and sets `no image data` when the file has none), `pyql3/services/poller.py` (`DirectoryPoller.polling_stopped`, emitted by `stop_polling()` and so also when `start_polling()` replaces a watch), covered by `test_a_stale_retry_does_not_replace_a_newer_frame`, `test_a_retry_does_not_replace_a_file_opened_by_hand`, `test_no_retry_after_polling_stops`, `test_a_retry_does_not_survive_a_restarted_watch` and `test_the_failure_warning_quotes_this_files_error` (`tests/test_poller.py`)
- **Severity:** medium — the window ends up showing an older frame or an unrequested file, and it happens without any user action
- **Files:** `pyql3/gui/main_window.py:1357-1382`, `pyql3/gui/main_window.py:642-649`, `pyql3/gui/main_window.py:1375`
- **Related:** S0 introduced the retry schedule. `tests/test_multi_window.py::test_a_pending_auto_load_retry_does_not_revive_a_closed_window` covers only the closed-window case.

### Symptom
A frame that fails to parse is retried at 1, 2, 4 and 8 s. Nothing stops those retries when something else is loaded:

- Frame 1 fails because it is still arriving, and frame 2 then loads. When frame 1's retry succeeds a second later, the title goes from `frame_0002.fits` back to `frame_0001.fits` and the data from 2.0 to 1.0.
- The user opens `user_choice.fits` with File → Open while a retry is pending. 1.5 s later the window shows `frame_0003.fits` instead.
- The user unticks Enable Polling. A pending retry still loads its frame.

There is a second problem in the same function. `_last_load_error` is set only in the `except` branch, so the "could not be read" warning can quote an unrelated earlier failure. A table-only `s251021_a001_tbl.fits` was reported with `Last error: FITS file not found: .../yesterday_frame.fits`, from an earlier File → Open.

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import io, time, tempfile, numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication, QMessageBox
import pyql3.services.config as cfg
app = QApplication([]); d = tempfile.mkdtemp()
cfg._shared_config = cfg.ConfigManager(d + "/config.json")
QMessageBox.warning = staticmethod(lambda *a, **k: None)
from pyql3.gui.main_window import MainWindow
buf = io.BytesIO(); fits.PrimaryHDU(np.full((6, 5, 5), 1.0, "f4")).writeto(buf)
f1, f2 = d + "/frame_0001.fits", d + "/frame_0002.fits"
open(f1, "wb").write(buf.getvalue()[:2980])          # header plus part of the data
win = MainWindow()
win.on_file_detected(f1)                             # fails; a retry is queued for 1 s
fits.PrimaryHDU(np.full((6, 5, 5), 2.0, "f4")).writeto(f2)
win.on_file_detected(f2)                             # the newer frame displays
print(win.windowTitle())                             # -> QuickLook 3 - frame_0002.fits
open(f1, "wb").write(buf.getvalue())                 # frame 1 finishes arriving
end = time.time() + 1.5
while time.time() < end:
    app.processEvents()
print(win.windowTitle())                             # -> QuickLook 3 - frame_0001.fits
```

### Root cause
`_attempt_auto_load` queues each retry with `QTimer.singleShot(delay_ms, lambda: ...)` (`main_window.py:1369-1371`). The only check before reloading is `_closed` (`:1360`). Nothing records that a newer detection, a user load or `stop_polling()` has happened since the retry was queued. In `load_fits`, the data-is-None branch (`:642-645`) returns False without touching `_last_load_error`, and `:1375` reads whatever the last exception left there.

### Fix (proposed)
Keep a counter `self._auto_load_generation`. Increment it in `on_file_detected`, in every user-initiated load (`open_file`, `open_recent_file`, `WindowManager.open_path`) and when polling stops. Pass the value into the timer lambda, and have `_attempt_auto_load` return if it no longer matches. Clear `_last_load_error` at the start of `load_fits`, and set it to "no image data" in the data-is-None branch.

### Regression test (proposed)
`tests/test_poller.py::test_a_stale_retry_does_not_replace_a_newer_frame`: the snippet above, asserting the title still names `frame_0002.fits`. Add a second test that opens a file by hand during a pending retry and asserts it stays displayed.

---

## R12. A failed load leaves `FitsReader` pointing at a different file from the one displayed

- **Status:** open
- **Severity:** medium — Save As, Edit Header and the Extension combo then act on the wrong file or refuse to act, and auto-load failures make this happen without any user action
- **Files:** `pyql3/core/fits_reader.py:62-86`, `pyql3/gui/main_window.py:607-650`, `pyql3/gui/main_window.py:742-754`, `pyql3/gui/main_window.py:794-800`, `pyql3/gui/main_window.py:1234`
- **Related:** S7. Its fix states that a data-less load leaves the displayed cube untouched. That is true of the viewer, but the reader underneath has already switched.

### Symptom
The window shows `good_cube.fits`, with `0: PRIMARY` and `1: SCI` in the Extension combo. One auto-load attempt on `arriving.fits`, a frame still being written, fails. The viewer, title and combo still show `good_cube.fits`, but `fits_reader.filepath` is now `arriving.fits` with no data or header. From then on:

- Edit FITS Header says "Please load a FITS file first."
- Save FITS As says "No data to save."
- Choosing `1: SCI` in the combo opens `arriving.fits` at index 1: `Failed to open FITS file: list index out of range`.
- Region → Save Regions As suggests `arriving_regions.yml`, and the saved file records `arriving.fits` as its source.

When `fits.open` itself raises (a truncated header, `Empty or corrupt FITS file`), the reader is closed but keeps the old path. Display → Redisplay image, which the exhausted-retry warning tells the user to use, then reloads the old cube rather than the new file.

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import io, os, tempfile, numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication, QMessageBox
import pyql3.services.config as cfg
app = QApplication([]); d = tempfile.mkdtemp()
cfg._shared_config = cfg.ConfigManager(d + "/config.json")
msgs = []
QMessageBox.warning = QMessageBox.critical = staticmethod(lambda *a, **k: msgs.append(a[2]))
from pyql3.gui.main_window import MainWindow
good = d + "/good_cube.fits"
fits.HDUList([fits.PrimaryHDU(np.full((6, 5, 5), 3.0, "f4")),
              fits.ImageHDU(np.full((6, 5, 5), 4.0, "f4"), name="SCI")]).writeto(good)
buf = io.BytesIO(); fits.PrimaryHDU(np.ones((6, 5, 5), "f4")).writeto(buf)
partial = d + "/arriving.fits"
open(partial, "wb").write(buf.getvalue()[:2944])     # header plus part of the data
win = MainWindow(); win.load_fits(good)
win.load_fits(partial, show_errors=False)            # one auto-load attempt
print(win.windowTitle(), "| reader:", os.path.basename(win.fits_reader.filepath))
win.edit_header(); win.save_file_as()
win.image_viewer.combo_ext.setCurrentIndex(1)        # "1: SCI" of the cube on screen
print(msgs)
# -> QuickLook 3 - good_cube.fits | reader: arriving.fits
# -> ['Please load a FITS file first.', 'No data to save.',
#     'Failed to open FITS file:\nlist index out of range']
```

### Root cause
`FitsReader.load()` closes the current HDUList (`fits_reader.py:63`), opens the new file (`:67`), sets `filepath` (`:70`) and clears `data` and `header` (`:73-74`), and only then reads the data (`:78`, `:83`). That read is where a partial file raises. `load_fits` catches the exception (`main_window.py:646-650`) but restores nothing. Every later action reads the reader, not the viewer: `edit_header` (`:795`), `save_file_as` (`:701`), `on_extension_changed` (`:742-747`), `redisplay_image` (`:749-754`) and `save_regions_as` (`:1234`).

### Fix (proposed)
In `load_fits`, load a different path into a new `FitsReader`. Replace `self.fits_reader`, and close the old one, only after the data is read and `set_data` has succeeded. For same-path extension switches, keep using the current reader so that unsaved header edits survive, as B5 requires. The data-less case should leave the old reader in place too, unless the S7 decision to publish the data-less file's header to the Header Editor is kept on purpose. If it is, the Extension combo and title should be updated to match.

### Regression test (proposed)
`tests/test_main_window_and_poller.py::test_failed_load_leaves_the_reader_on_the_displayed_file`: load a two-extension cube, then fail a load on a truncated file. Assert that `fits_reader.filepath` is still the cube, `get_data()` is not None, and selecting extension 1 displays the SCI data.

---

## R13. A header that astropy's `WCS` cannot parse fails the whole load and leaves the viewer half-updated

- **Status:** open
- **Severity:** medium — a cube that ds9 and astropy's FITS reader both open cannot be displayed at all, and the failed attempt leaves the analysis array out of step with the screen
- **Files:** `pyql3/gui/viewers/image_viewer.py:1066-1080`, `pyql3/gui/main_window.py:646-650`
- **Related:** R12 (a failed load also leaves `FitsReader` on the new file). Together they mean the reader, `raw_data` and the screen can describe three different states.

### Symptom
A cube whose spectral axis says `CUNIT1 = 'micron'` (or `'microns'`) is refused with
"Failed to open FITS file: ERROR 6 in wcs_units() ...". `'um'` and `'Angstrom'` load. wcslib
does not accept `micron` as a unit string, and `ImageViewer.set_data` lets the exception out.

After the failure the previous cube stays on screen, but `raw_data` and `header` already
belong to the refused file, and `view_rotation` has been reset to 0. Every tool that reads
`raw_data` (Arithmetic's "Active Image", exports) now works on data the user is not looking
at; the WCS readout still uses the old file's WCS.

### Reproduction
```python
import numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

def header(unit):
    h = fits.Header()
    h['NAXIS'] = 3
    h['CTYPE1'], h['CUNIT1'], h['CRVAL1'], h['CDELT1'], h['CRPIX1'] = 'WAVE', unit, 2.0, 0.001, 1
    h['CTYPE2'], h['CUNIT2'], h['CRVAL2'], h['CDELT2'], h['CRPIX2'] = 'DEC--TAN', 'deg', -29.0, 1e-5, 5
    h['CTYPE3'], h['CUNIT3'], h['CRVAL3'], h['CDELT3'], h['CRPIX3'] = 'RA---TAN', 'deg', 266.4, -1e-5, 5
    return h

v = ImageViewer()
v.set_data(np.ones((10, 10, 20)), header('um'))
try:
    v.set_data(np.full((10, 10, 20), 7.0), header('micron'))
except Exception as e:
    print(type(e).__name__)                       # InvalidTransformError
print(v.raw_data[0, 0, 0], v.display_data.max())  # 7.0 1.0  -- raw_data is the refused cube
```

### Root cause
`set_data` assigns `self.raw_data`, `self.header`, `self._is_new_data` and `self.view_rotation`
first (`image_viewer.py:1068-1071`) and only then calls `WCS(header)` (`:1080`) inside a
`warnings.catch_warnings()` block that suppresses warnings but not exceptions. `load_fits`
catches the exception (`main_window.py:646`), so nothing crashes, but nothing restores the
assignments either.

### Fix (proposed)
Build the WCS through a helper that cannot raise: try `WCS(header)`; on failure retry with a
copy of the header whose `CUNITn` values `micron`/`microns` (any case) are rewritten to `um`;
if that also fails, use `wcs = None` and show "WCS: unreadable (<message>)" in the WCS label.
Do every fallible step (the ndim check, the WCS) before assigning any viewer state.

### Regression test (proposed)
`tests/test_image_viewer.py::test_micron_cunit_loads_with_a_wavelength_axis` — `set_data` with
`CUNIT1 = 'micron'` does not raise, and `viewer.wcs_z_idx is not None`.
`tests/test_image_viewer.py::test_unparseable_wcs_leaves_data_displayed_without_wcs` — a header
with a deliberately broken WCS (e.g. `CTYPE1 = 'WAVE'`, `CUNIT1 = 'parsec'`) loads,
`viewer.wcs is None`, and `viewer.raw_data` is the new array.

## R14. Statistics, Photometry, Peak Fit and Strehl never refresh when the plane or the file changes

- **Status:** open
- **Severity:** medium — numbers for a channel or a frame that is no longer on screen are shown as current. With directory polling this happens on every new frame.
- **Files:** `pyql3/gui/tools/statistics.py:39-40`, `pyql3/gui/tools/photometry.py:85-93`, `pyql3/gui/tools/fitting.py:214-231`, `pyql3/gui/tools/strehl.py:64-65`, `pyql3/gui/tools/cuts.py:92-93`, `pyql3/gui/tools/advanced_plots.py:29-30`, `:115-116`, `pyql3/gui/viewers/image_viewer.py:958`, `:1033`
- **Related:** B4 item 3 records that the Statistics dialog reports 30.00 for a collapsed 4-8 Sum. That is true when the dialog computes; nothing makes it compute again. S5/B17 fixed which plane the cuts read, not when they read it.

### Symptom
Cube whose channel *k* holds a constant (0, 50, 3, 80, 1, 90, 7, 20, 60, 5). All dialogs are opened at slice 0:

| action | on screen | Statistics mean | Photometry raw | Horizontal Cut |
|---|---|---|---|---|
| slider to 3 | 80 | **0.0** | **0.0** | 80 |
| Boxcar 3, slider to 6 | 20 | — | — | **80** |
| reload with data ×100 | 8000 | **0.0** | **0.0** | — |

The values change only when the ROI is moved or the display unit is switched.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.statistics import StatisticsDialog
from pyql3.gui.tools.photometry import PhotometryDialog
from pyql3.gui.tools.cuts import CutPlotDialog
vals = np.array([0., 50., 3., 80., 1., 90., 7., 20., 60., 5.])
cube = np.broadcast_to(vals, (40, 30, 10)).copy()   # numpy (RA, DEC, WAVE): channel k holds vals[k]
v = ImageViewer(); v.set_data(cube)
s = StatisticsDialog(None, v); p = PhotometryDialog(None, v); cut = CutPlotDialog('horizontal', None, v)
v.slider_slice.setValue(3); app.processEvents()
print("slice 3:        screen", v.current_plane()[5, 5], "| Statistics mean", s.value_labels["Mean"].text(),
      "| Photometry raw", p.lbl_flux.text(), "| cut", cut.plot_curve.yData[5])
v.txt_boxcar.setText("3"); v.slider_slice.setValue(6); app.processEvents()
print("boxcar 3 at 6:  screen", v.current_plane()[5, 5], "| cut", cut.plot_curve.yData[5])
v.txt_boxcar.setText("1"); v.slider_slice.setValue(3); v.set_data(cube * 100); app.processEvents()
print("reload x100:    screen", v.current_plane()[5, 5], "| Statistics mean", s.value_labels["Mean"].text(),
      "| Photometry raw", p.lbl_flux.text())
```
```
slice 3:        screen 80.0 | Statistics mean 0.0000 | Photometry raw 0.0000 | cut 80.0
boxcar 3 at 6:  screen 20.0 | cut 80.0
reload x100:    screen 8000.0 | Statistics mean 0.0000 | Photometry raw 0.0000
```

### Root cause
Statistics, Photometry, Peak Fit and Strehl recompute only from `on_roi_changed` and from
`MainWindow.update_tools_for_unit` (`main_window.py:783-792`). They connect to no viewer signal.
Cuts, Surface and Contour connect to `imv.sigTimeChanged`, which fires only for a single-slice
display. Boxcar and Z Range draw through `update_image_display(bypass_imv=True)`
(`image_viewer.py:958`, `:1033`), which never moves the ImageView's index, so a slider move in
those modes reaches none of them. `display_changed` is emitted on a flip, a rotation or a 2-D
load, but no tool listens to it.

### Fix (proposed)
Emit a `plane_changed` signal from `on_slider_changed`, `apply_z_slice`, `apply_z_range`,
`refresh_display` and `set_data`. Connect it once in `BaseToolDialog.__init__` to the tool's
`update_stats` / `update_plot`, and disconnect it in teardown. Remove the per-tool
`imv.sigTimeChanged` connections.

### Regression test (proposed)
`tests/test_analysis_tools.py::test_tools_follow_the_displayed_plane`: with the cube above, after
the slider, a Boxcar change, a Z Range apply and a `set_data`, each open tool's reported value
equals `current_plane()` at the ROI.

---

## R15. Escape hides a tool dialog without running its teardown, so its ROI, overlays and drag handling stay on the image

- **Status:** open
- **Severity:** medium — Escape is the usual way to dismiss a dialog. What it leaves behind cannot be removed except by closing the window, and in draw mode the image can no longer be panned.
- **Files:** `pyql3/gui/tools/base_tool.py:48-129`, `pyql3/gui/tools/cuts.py:298`, `pyql3/gui/tools/depth_plot.py:1052`, `pyql3/gui/tools/photometry.py:72`, `pyql3/gui/tools/advanced_plots.py:79`, `:164`, `pyql3/gui/tools/plot_catalog.py:1271`, `pyql3/gui/main_window.py:812-880`
- **Related:** B7 and M27 made `closeEvent` remove items correctly. This is the path that skips `closeEvent` entirely. `pyql3/gui/dialogs/region_properties.py:237` already overrides `reject`.

### Symptom
Pressing Escape in any tool dialog hides it. Its ROI stays on the image. Reopening the tool from
the menu builds a second dialog with a second ROI, because the openers reuse a dialog only while it
`isVisible()`. Measured:
- Statistics and Horizontal Cut: `visible after Escape: False | closeEvent ran: False | roi still in scene: True`. After reopening, a new dialog exists and the old ROI is still in the scene, including after the main window closes.
- Photometry: the same, and its two sky rings stay as well.
- Plot Catalog: markers and labels (`markers painted: 2 | labels painted: ['b','a']`) stay painted after a reopened, empty dialog is closed and the view is panned.
- Draw mode: with **Draw Region** checked, Escape leaves the hidden dialog as the viewer's exclusive drag owner, and the view reports `mouseEnabled [False, False]`, so the image cannot be panned.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.statistics import StatisticsDialog
v = ImageViewer(); v.show(); v.set_data(np.random.rand(40, 50))
scene = v.imv.getView().scene()
a = StatisticsDialog(None, v); a.show(); ra = a.roi
a.close(); app.processEvents()
print("close():  ROI left in scene:", ra.scene() is scene)
b = StatisticsDialog(None, v); b.show(); rb = b.roi; b.btn_draw.click()
QTest.keyClick(b, Qt.Key.Key_Escape); app.processEvents()
print("Escape:   visible", b.isVisible(), "| ROI left in scene:", rb.scene() is scene,
      "| drag owner is the hidden dialog:", v.exclusive_drag_owner() is b,
      "| view mouse enabled:", v.imv.getView().state['mouseEnabled'])
```
```
close():  ROI left in scene: False
Escape:   visible False | ROI left in scene: True | drag owner is the hidden dialog: True | view mouse enabled: [False, False]
```

### Root cause
`BaseToolDialog` subclasses `QDialog`. `QDialog.keyPressEvent` maps Escape to `reject()`, which
calls `done()` and `hide()` and never sends a `QCloseEvent`. Every tool puts its cleanup in
`closeEvent`: ROI removal and `disable_draw_mode` (`base_tool.py:126-129`), the `sigTimeChanged`
disconnect (`cuts.py:298`, `advanced_plots.py:79`, `:164`), the background ROI and annulus rings
(`depth_plot.py:1052`), the sky rings (`photometry.py:72`) and the markers, labels and range
connection (`plot_catalog.py:1271`). `BaseToolDialog` overrides neither `reject` nor `hideEvent`.

### Fix (proposed)
Move each tool's `closeEvent` cleanup into one idempotent `tear_down()` on `BaseToolDialog`, and
call it from both `closeEvent` and an overridden `reject()` that then calls `super().reject()`.

Do **not** override `reject()` to call `self.close()`, the obvious fix. `QDialog.closeEvent`
itself calls `reject()` while the dialog is visible, so that override re-enters `close()`, which
Qt refuses while a close is in progress; `QDialog.closeEvent` then sees the dialog still visible
and ignores the event. Measured on a bare `QDialog`: with that override, the dialog stays on
screen after both `close()` and Escape. The `tear_down()` route closes in both cases, and runs
twice on a close (once from `closeEvent`, once from the inherited `reject()`), which is why it
must be idempotent.

### Regression test (proposed)
`tests/test_menu_actions.py::test_escape_tears_a_tool_down_like_close`, parametrized over every
`BaseToolDialog` subclass: after `QTest.keyClick(dialog, Qt.Key.Key_Escape)`, the dialog's items
are gone from the scene and `exclusive_drag_owner()` is None.

---

## R16. Several tool dialogs fall outside `TOOL_DIALOG_ATTRS`: they stay on screen after their window closes and keep running hidden

- **Status:** open
- **Severity:** medium — leftover windows act on a closed window's released data, extra ROIs stay drawn, and region drags slow down every time the Region List is reopened
- **Files:** `pyql3/gui/main_window.py:821-847`, `pyql3/gui/main_window.py:943-948`, `pyql3/gui/main_window.py:950-978`, `pyql3/gui/main_window.py:595-605`, `pyql3/gui/tools/region_list.py:88-89`
- **Related:** the `TOOL_DIALOG_ATTRS` rule in `AGENTS.md`: each tool is reopened only if it is not already visible, and every tool must be listed.

### Symptom
There are three triggers.

1. **Cut, Surface and Contour build a new dialog on every menu click.** Opening Horizontal Cut twice gives two dialogs and two ROIs, but only the second is kept in `_hcut_dialog`.
   - On a DN/s → Total DN change only the second refreshes (`{'first': 0, 'second': 1}`).
   - When the window closes, the first stays on screen with its green ROI still drawn on the image.
   - Opening each tool twice and closing the window left "Horizontal Cut", "Contour Overlay" and "Surface Plot" visible. Surface was measured in the tools review; it needs OpenGL and was not part of the offscreen run below.
2. **Region property dialogs are never closed.** They are held in `_region_property_dialogs`, which `close_tool_dialogs()` does not walk, so "Circle Region" stays on screen after its window closes.
3. **Region List is rebuilt each time it is opened while hidden, and the hidden copies stay connected to `regions_changed`.** After five open/close cycles, one `regions_changed` runs `update_plot` in six dialogs. Measured with 450 regions:
   - 0.076 s per drag frame with the list opened once;
   - 0.329 s after five cycles;
   - 0.379 s with every list closed.

The leftover windows from triggers 1 and 2 are then listed as orphans in the other windows' Window menus.

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import tempfile, numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication
import pyql3.services.config as cfg
app = QApplication([]); d = tempfile.mkdtemp()
cfg._shared_config = cfg.ConfigManager(d + "/config.json")
from pyql3.gui.main_window import MainWindow
from pyql3.gui.tools.region_list import RegionListDialog
fits.PrimaryHDU(np.ones((6, 20, 20), "f4")).writeto(d + "/cube.fits")
win = MainWindow(); win.show(); win.load_fits(d + "/cube.fits")
win.open_horizontal_cut(); win.open_horizontal_cut()      # Plot -> Horizontal Cut, twice
win.open_contour_plot(); win.open_contour_plot()          # Plot -> Contour, twice
win.open_region_properties(win.region_layer.place("circle", 5.5, 5.5))
for _ in range(5):                                        # Region List: open, Close, x5
    win.open_region_list(); win._region_list_dialog.close()
win.open_region_list()
calls, real = [], RegionListDialog.update_plot
RegionListDialog.update_plot = lambda self: (calls.append(self), real(self))[1]
win.region_layer.regions_changed.emit()                   # what every drag frame emits
print("Region List refreshes per change:", len(calls))   # -> 6
win.close(); app.processEvents()
print(sorted(w.windowTitle() for w in app.topLevelWidgets() if w.isVisible()))
# -> ['Circle Region', 'Contour Overlay', 'Horizontal Cut']
```

### Root cause
- `open_horizontal_cut`, `open_vertical_cut`, `open_diagonal_cut`, `open_surface_plot` and `open_contour_plot` (`main_window.py:821-847`) assign a new dialog unconditionally. The previous one keeps its Qt parent, so it stays alive and visible, but it is no longer reachable from `TOOL_DIALOG_ATTRS`, which `update_tools_for_unit` (`:783-792`) and `close_tool_dialogs` (`:595-605`) walk.
- `open_region_properties` (`:950-978`) keeps its dialogs in a dict that neither walk reads.
- `open_region_list` (`:943-948`) builds a new `RegionListDialog` whenever the cached one is hidden. Each one connects `regions_changed` to its own `update_plot` (`region_list.py:88-89`) and never disconnects, so closed lists keep rebuilding their tables on every drag frame.

### Fix (proposed)
Route every opener through one helper, `_open_tool(attr, factory)`, which reuses the cached dialog when it exists and shows it again, rather than building a new one when it is merely hidden. Have `close_tool_dialogs()` also close `_region_property_dialogs.values()`. Disconnect `regions_changed` in `RegionListDialog.closeEvent`, or reuse the hidden instance.

### Regression test (proposed)
`tests/test_multi_window.py::test_every_tool_dialog_closes_with_its_window`: open each tool twice and a region property dialog, close the window, and assert that no dialog parented to it is visible. `tests/test_region_ui.py::test_reopening_region_list_does_not_multiply_refreshes`: five open/close cycles, then assert one `regions_changed` calls `update_plot` exactly once.

---

## R17. A ds9 export containing only arrows or rotated labels declares `icrs` whatever frame the WCS is in

- **Status:** open
- **Severity:** medium — the arrows are misplaced in ds9 and in our own reader with nothing reported; about 1 px for every OSIRIS cube, thousands of pixels for FK4
- **Files:** `pyql3/core/ds9_regions.py:198-203`, `pyql3/core/ds9_regions.py:416-423`
- **Related:** M21 (sky frames of hand-written arrows), on the write side this time.

### Symptom
Export in sky coordinates a set with no circle, box or unrotated text. The file declares `icrs`, but the numbers are in the WCS's own frame. Read back on the same image, an arrow and a rotated label drawn at (49, 49) land at:

- (6999.66, 257.53) on an FK4 image;
- (48.30, 49.54) on an FK5 image with 36 mas pixels. That is 0.9 px, and the bundled OSIRIS cube declares `RADESYS = 'FK5'`, so this applies to every OSIRIS file.
- On a galactic image the arrow is dropped as "falls outside this image".

Both reports are empty. Add one circle and the library writes `b1950`, `j2000` or `galactic`, and the same arrow lines become correct.

### Reproduction
```python
import astropy.units as u
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from pyql3.core.regions_model import Arrow, Text, RegionList
from pyql3.core.ds9_regions import to_ds9, from_ds9

def wcs(crval, radesys, equinox, cdelt=1e-4):
    w = WCS(naxis=2)
    w.wcs.ctype, w.wcs.crval = ['RA---TAN', 'DEC--TAN'], crval
    w.wcs.cdelt, w.wcs.crpix = [-cdelt, cdelt], [50, 50]
    w.wcs.radesys, w.wcs.equinox = radesys, equinox
    return w

star = SkyCoord(266.41683708 * u.deg, -29.00781056 * u.deg)
for name, w in [("FK4", wcs([star.fk4.ra.deg, star.fk4.dec.deg], 'FK4', 1950.0)),
                ("FK5, 36 mas px", wcs([star.fk5.ra.deg, star.fk5.dec.deg], 'FK5', 2000.0, 1e-5))]:
    regions = [Arrow(x=49.0, y=49.0, length=10.0, angle=30.0),
               Text(x=49.0, y=49.0, text="tilted", angle=20.0)]
    text, out = to_ds9(RegionList(regions=regions), wcs=w, frame="sky")
    back, rep = from_ds9(text, wcs=w)
    print(name, "| frame line:", text.splitlines()[1], "| (49, 49) ->",
          [(type(r).__name__, round(r.x, 2), round(r.y, 2)) for r in back],
          "| reports:", out.skipped, rep.skipped)
```
```
FK4 | frame line: icrs | (49, 49) -> [('Text', 6999.66, 257.53), ('Arrow', 6999.66, 257.53)] | reports: [] []
FK5, 36 mas px | frame line: icrs | (49, 49) -> [('Text', 48.3, 49.54), ('Arrow', 48.3, 49.54)] | reports: [] []
```

### Root cause
When no library shape is left to serialize, `to_ds9` writes its own preamble, `[DS9_HEADER, "icrs" if use_sky else "image"]` (`ds9_regions.py:203`). The hand-written lines that follow take their numbers from `_ds9_position` (`:416-423`), that is `CelestialMap.to_sky`, which returns the WCS's own frame. With a shape present, `Regions.serialize` declares the `SkyCoord`'s actual frame, which is why the mixed case is right. The reader converts the declared `icrs` into the WCS's frame (`from_skycoord`), which adds the frame difference.

### Fix (proposed)
Write the ds9 name of `wcs_to_celestial_frame(mapping.wcs2d)` instead of the literal `icrs`, using the inverse of `_SKY_FRAMES` (`icrs`, `fk5`, `fk4`, `galactic`, `ecliptic`).

### Regression test (proposed)
`tests/test_ds9_regions.py::test_an_arrow_only_sky_export_declares_the_wcs_frame`, parametrised over ICRS, FK5, FK4 and galactic WCSs. Assert that the arrow round-trips within `SUB_PIXEL`, and that the frame line equals the one the library writes for a circle on the same WCS.

## R18. Aperture Photometry reads the sky as zero off the image, subtracts a full-area sky at the edge, returns NaN for one bad pixel, and freezes when the annulus is inverted

- **Status:** open
- **Severity:** medium — near an edge or a bad pixel the subtracted flux is wrong or NaN, and with an inverted annulus the dialog keeps showing old numbers as if they were current
- **Files:** `pyql3/gui/tools/photometry.py:132-155`
- **Related:** The Depth Plot's circular extraction (`pyql3/core/spectral_photometry.py`) weights only finite pixels. `tests/test_spectral_photometry.py::test_the_cross_check_against_the_photometry_tool` compares the two tools only for an interior aperture on finite data, so it does not cover any of the cases below.

### Symptom
Flat sky of 10, aperture r = 3, annulus 6–15. Every subtracted flux should be 0:

| case | raw | sky | subtracted |
|---|---|---|---|
| centre of the image | 282.74 | 282.74 | 0.00 |
| aperture over the left edge | 227.47 | 282.74 | **−55.28** |
| aperture at the corner | 139.56 | **0.00** | **139.56** |
| one NaN pixel in the aperture | **nan** | 282.74 | **nan** |

With Inner Sky Radius set to 25 against an outer radius of 15, every update raises
`ValueError: 'r_out' must be greater than 'r_in'`. The labels then keep showing 282.74 / 282.74 /
0.00 after the sky has changed to 50 and the aperture has been moved.

### Reproduction
```python
import numpy as np, sys
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.photometry import PhotometryDialog
sky = np.full((60, 80), 10.0)          # flat sky of 10: every subtracted flux should be 0
v = ImageViewer(); v.set_data(sky)
d = PhotometryDialog(None, v)
d.spin_aperture.setValue(3.0); d.spin_inner.setValue(6.0); d.spin_outer.setValue(15.0)
def at(cx, cy, tag):
    d.roi.setPos(cx - 3, cy - 3); d.roi.setSize([6, 6]); d.update_photometry()
    print(f"{tag:20s} raw {d.lbl_flux.text():>9s}  sky {d.lbl_sky.text():>9s}  subtracted {d.lbl_total.text()}")
at(40, 30, "centre"); at(1, 30, "aperture over edge"); at(0.5, 0.5, "corner")
nan = sky.copy(); nan[30, 40] = np.nan; v.set_data(nan); at(40, 30, "one NaN in aperture")
v.set_data(sky); at(40, 30, "centre again")
errors = []; sys.excepthook = lambda *a: errors.append(a[1])
d.spin_inner.setValue(25.0); v.set_data(sky * 5); d.roi.setPos(30, 20); app.processEvents()
print("inner 25 > outer 15, sky now 50:", d.lbl_flux.text(), d.lbl_sky.text(), d.lbl_total.text(), "|", repr(errors[-1]))
```
The output is the table above, followed by
`inner 25 > outer 15, sky now 50: 282.7433 282.7433 -0.0000 | ValueError("'r_out' must be greater than 'r_in'")`.

### Root cause
- `mask.multiply(data_for_phot)` (`photometry.py:140`) fills annulus pixels outside the image with its default `fill_value=0`. Those zeros pass the `mask.data > 0` filter and enter the median (`:141-143`).
- The sky is scaled by the geometric `aperture.area` (`:144`), not by the area actually summed, which is smaller off the edge and around a NaN.
- `aperture_photometry` is given the plane unmasked (`:135`), so one NaN makes the sum NaN.
- `CircularAnnulus` is built at `:133`, outside the `try` that starts at `:138`, so an inverted annulus raises out of the slot before any label is written.

### Fix (proposed)
Take the annulus values with `mask.get_values(data)` and keep only finite ones. Pass
`mask=~np.isfinite(data)` to `aperture_photometry`. Scale the sky by the aperture area that
overlapped finite data, as `spectral_photometry.aperture_spectrum` does. Validate `r_in < r_out`
first; if it fails, set the labels to an explanatory message.

### Regression test (proposed)
`tests/test_analysis_tools.py::test_photometry_on_flat_sky_is_zero_everywhere`: on a flat sky,
the subtracted flux is 0 within 1e-6 at the centre, over the edge and at the corner, and finite
with one NaN pixel. A second test asserts that inner ≥ outer shows a message rather than the
previous values.

---

## R19. A rectangular box hanging off the low edge of the image, or wholly off it, measures pixels it does not cover

- **Status:** ✅ FIXED — `pyql3/gui/tools/depth_plot.py` (`_box_slices` over `coords.box_index_slices`, used by the rectangle Depth Plot, both cuts and `region_background_level`), covered by `test_a_box_half_off_the_image_measures_only_what_it_covers`, `test_a_box_wholly_off_the_image_is_reported_not_measured`, `test_a_cut_over_a_box_half_off_the_image_covers_only_the_image`, `test_a_background_box_half_off_the_image_measures_only_what_it_covers` and `test_a_circular_aperture_with_a_background_box_off_the_image_says_so` (`tests/test_depth_plot.py`)
- **Severity:** medium — a plausible spectrum or cut is drawn from the wrong columns, with
  no message
- **Files:** `pyql3/gui/tools/depth_plot.py:1540-1549` (source box, and both cut types),
  `:1621-1629` (background box), `:888-895` (`region_background_level`)

### Symptom
With Shape set to Rectangle, on a cube whose value equals the display x column:
- A box at x=[-5, 5) covers columns 0-4 (mean 2.0) but plots 4.5, the mean of columns 0-9.
- A box at x=[-50, -40), entirely off the image, also plots 4.5.
- A box at x=[100, 110) plots 29.0, the last column.
- Horizontal Cut over x=[-5, 5) labels its x axis 0..9.

The status line stays empty in all of these cases. The circular aperture reports the same
situations in red ("does not overlap the cube"). The background box clamps the same way, so
a background region dragged off the image measures the edge columns.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

data = np.broadcast_to(np.arange(30.0)[:, None, None], (30, 20, 3)).copy()  # value = display x
v = ImageViewer(); v.set_data(data, None)
d = DepthPlotDialog(image_viewer=v)
d.combo_shape.setCurrentText("Rectangle")
d.combo_calc.setCurrentText("Average")
for x0 in (-5, -50, 100):
    d.roi.setPos([x0, 2]); d.roi.setSize([10, 4])
    print(f"box x=[{x0}, {x0 + 10}): mean x = {d.plot_data.getData()[1][0]}",
          f"status={d.lbl_bg_info.text()!r}")
d.combo_type.setCurrentText("Horizontal Cut")
d.roi.setPos([-5, 2]); d.roi.setSize([10, 4])
print("Horizontal Cut over x=[-5, 5): x axis =", d.plot_data.getData()[0].tolist())
```
```
box x=[-5, 5): mean x = 4.5 status=''
box x=[-50, -40): mean x = 4.5 status=''
box x=[100, 110): mean x = 29.0 status=''
Horizontal Cut over x=[-5, 5): x axis = [0, 1, 2, 3, 4, 5, 6, 7, 8, 9]
```

### Root cause
All three copies first clamp `x0 = max(0, min(x0, x_len-1))` and then compute
`x1 = max(x0+1, min(x0+w, x_len))` from the *clamped* `x0` (`depth_plot.py:1546-1549`,
`:1626-1629`, `:892-895`). A box starting at -5 therefore keeps its full width from 0. The
`max(x0+1, ...)` guarantees at least one column, so a box with no overlap becomes the edge
column rather than an empty selection. `int()` also truncates toward zero, so a box at
x=-0.5 is read as starting at 0.

### Fix (proposed)
One helper, e.g. `_box_slices(pos, size, nx, ny)`. It should compute both edges from the
unclamped position (`floor(pos)`, `floor(pos) + int(size)`), clip each to the image, and
return `None` when nothing overlaps. Use it in the three places. On `None`, the callers
should clear the curves and report "does not overlap the cube", as the circle path
does at `:1569-1577`.

### Regression test (proposed)
`tests/test_depth_plot.py::test_a_box_half_off_the_image_measures_only_what_it_covers`: on
the value = x cube, a box at [-5, 5) gives Average 2.0. A box at [-50, -40) leaves
`plot_data` empty and puts "overlap" in `lbl_bg_info`. A matching case for the background
box.

## R20. With a Region background, a circular Total aperture subtracts the background over its full area even in channels where NaN pixels made it smaller

- **Status:** open
- **Severity:** medium — over-subtraction by the background level times the number of NaN
  pixels, in exactly the channels that have bad pixels
- **Files:** `pyql3/gui/tools/depth_plot.py:1584-1592`, `pyql3/core/spectral_photometry.py:203-218`

### Symptom
Take a flat sky of 5 with two NaN pixels inside the aperture in channel 1, Combine = Total.
Annulus mode gives Subtracted `[0, 0, 0]`. Region mode, with a background box elsewhere on
the same flat sky, gives `[0, -10, 0]`. The comment at `depth_plot.py:1585-1587` says the
two modes are applied "by the same rule". They are not. Average and Median do not use the
area and are unaffected.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

data = np.full((40, 30, 3), 5.0)            # flat sky of 5, no source
data[12:14, 15, 1] = np.nan                 # two bad pixels inside the aperture, channel 1
v = ImageViewer(); v.set_data(data, None)
d = DepthPlotDialog(image_viewer=v)
d.set_center((12.0, 15.0))
d.chk_enable_bg.setChecked(True)            # Annulus mode by default
print("Annulus mode:", d.plot_sub.getData()[1])
d.combo_bg_mode.setCurrentText("Region")
d.bg_roi.setPos([25, 5]); d.bg_roi.setSize([8, 8])
print("Region mode: ", d.plot_sub.getData()[1], " background level", d.plot_bg.getData()[1])
```
```
Annulus mode: [0.00000000e+00 2.84217094e-14 0.00000000e+00]
Region mode:  [  0. -10.   0.]  background level [5. 5. 5.]
```

### Root cause
- `aperture_spectrum()` returns the per-channel summed weight, which excludes NaN pixels.
  `annulus_spectrum()` subtracts with that array (`spectral_photometry.py:214`).
- For the label, however, it reports `aperture_area = float(np.nanmax(area))`
  (`:216`), the area of the cleanest channel.
- The Region path passes that scalar to `subtract_background()`
  (`depth_plot.py:1590-1591`), so every channel is charged for the full aperture.

### Fix (proposed)
Carry the per-channel weight in `SpectrumResult` (e.g. an `aperture_weight` array beside the
scalar `aperture_area`), and pass the array to `subtract_background()` in the Region branch.

### Regression test (proposed)
`tests/test_depth_plot.py::test_a_region_background_uses_the_per_channel_area`: the flat-sky
cube above, Region mode, Combine Total. Assert that `plot_sub` is 0 to 1e-9 in every channel.

## R21. Total reports 0.0, not NaN, for a channel in which no aperture pixel is finite

- **Status:** open
- **Severity:** medium — gaps in the data are drawn as real zeros, and on a JWST cube that
  is dozens to hundreds of channels at the edge of the field
- **Files:** `pyql3/core/spectral_photometry.py:138-148`, `pyql3/gui/tools/depth_plot.py:1614-1615`

### Symptom
When every pixel of the aperture is NaN in some channel, Combine = Total plots Source 0.0
and Subtracted 0.0 there, while Average correctly gives NaN. The zero reads as a
measurement. On a JWST NIRSpec G235H `s3d` cube (not in the repository; 53% of its voxels
are NaN outside the footprint), an r=1.5 aperture at the footprint edge gave exactly 0.0 in
156 of 3814 channels. In those channels the annulus measured 953 MJy/sr. The synthetic case
below shows the same thing.

### Reproduction
```python
import numpy as np
from pyql3.core.spectral_photometry import annulus_spectrum

cube = np.full((3, 30, 30), 5.0)            # (z, x, y)
cube[1, 2:9, 12:19] = np.nan                # channel 1: every aperture pixel is NaN,
                                            # the annulus around it is still finite
for combine in ("Total", "Average"):
    r = annulus_spectrum(cube, (5.0, 15.0), 3.0, r_in=4.0, r_out=6.0, combine=combine)
    print(f"{combine:8} source={r.source}  background={r.background}  subtracted={r.subtracted}")
```
```
Total    source=[141.37166941   0.         141.37166941]  background=[5. 5. 5.]  subtracted=[0. 0. 0.]
Average  source=[ 5. nan  5.]  background=[5. 5. 5.]  subtracted=[ 0. nan  0.]
```

### Root cause
`aperture_spectrum()` replaces NaN by 0 before the weighted sum (`spectral_photometry.py:142`).
With nothing finite, `total` is 0 and `per_channel_weight` is 0.
- The Average branch guards with `np.where(per_channel_weight > 0, ..., np.nan)`
  (`:146-148`).
- The Total branch returns `total` unguarded (`:144-145`). The subtraction then yields
  `0 - level * 0 = 0`.
- The rectangle path's `np.nansum` (`depth_plot.py:1615`) also returns 0 for an all-NaN
  channel.

`tests/test_spectral_photometry.py::test_a_channel_that_is_all_nan_in_the_annulus_yields_nan_not_an_exception`
(`:192-198`) builds a cube with exactly this channel, but asserts only on `background`.

### Fix (proposed)
- Return `np.where(per_channel_weight > 0, total, np.nan)` for Total.
- In the rectangle path, set a channel with no finite pixel to NaN after the `nansum`.

### Regression test (proposed)
Extend the existing all-NaN test to assert `np.isnan(result.source[1])` and
`np.isnan(result.subtracted[1])` for `combine="Total"`.

## R22. A sky annulus that starts inside the aperture is accepted without warning, so source flux is subtracted as sky

- **Status:** open
- **Severity:** medium — the flux comes out low by an amount that depends on the source,
  and the status line reads as if nothing were wrong
- **Files:** `pyql3/gui/tools/depth_plot.py:793-818`, `:1558-1583`, `pyql3/core/spectral_photometry.py:157-158`

### Symptom
The comment at `depth_plot.py:18-22` describes this failure as the reason the annulus
follows the aperture, but nothing prevents it. Two triggers:
- **Customised radii, then a larger aperture.** Set the sky radii to 5-8, then enlarge the
  aperture to r=7. On a Gaussian star with a sky of 2.0, the sky level reads 39.63 and the
  subtracted flux is 32653, against a true 38446 (-15%). The status line shows only
  "Aperture 153.9 px², sky annulus 124 px".
- **A typed inner radius smaller than the aperture.** Inner 1.5 inside a 3 px aperture
  gives a sky level of 238.9, also without a warning.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from photutils.aperture import CircularAperture
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

xx, yy = np.mgrid[:41, :41]
star = 1000.0 * np.exp(-((xx - 20) ** 2 + (yy - 20) ** 2) / (2 * 2.5 ** 2))
v = ImageViewer(); v.set_data(np.repeat((star + 2.0)[:, :, None], 2, axis=2), None)  # sky = 2
d = DepthPlotDialog(image_viewer=v)
d.set_center((20.0, 20.0))
d.chk_enable_bg.setChecked(True)
d.spin_r_in.setValue(5.0); d.spin_r_out.setValue(8.0)   # the user chooses a sky annulus
d.spin_radius.setValue(7.0)                             # then enlarges the aperture past it
print("radii: aperture", d.aperture_geometry()[2], "sky", d.spin_r_in.value(), d.spin_r_out.value())
print("status:", repr(d.lbl_bg_info.text()))
print("sky level", round(d.plot_bg.getData()[1][0], 2), "(true 2.0); subtracted",
      round(d.plot_sub.getData()[1][0], 1))
print("true flux in r=7:", round(CircularAperture((20, 20), 7.0).do_photometry(star.T)[0][0], 1))
```
```
radii: aperture 7.0 sky 5.0 8.0
status: 'Aperture 153.9 px², sky annulus 124 px'
sky level 39.63 (true 2.0); subtracted 32653.0
true flux in r=7: 38445.5
```

### Root cause
- `track_aperture_radii()` stops moving the sky radii once the user has edited them
  (`depth_plot.py:799-800`, set at `:810`). That is intended.
- `on_annulus_changed()` keeps only inner < outer (`:813-816`).
- `annulus_background()` checks only `0 < r_in < r_out` (`spectral_photometry.py:157-158`).
- Nothing compares the inner radius with the aperture radius.

### Fix (proposed)
In `annulus_spectrum()`, raise `ApertureError` when `r_in < r_ap` ("the sky annulus starts
inside the aperture"). The dialog already reports that error in red and draws no curve
(`depth_plot.py:1569-1577`). The radii the user typed stay as they are.

### Regression test (proposed)
- `tests/test_spectral_photometry.py::test_an_annulus_inside_the_aperture_is_refused`:
  `annulus_spectrum(cube, c, r_ap=7, r_in=5, r_out=8)` raises `ApertureError`.
- A dialog test asserting that the status line says so after the sequence above.

## R23. Image Arithmetic rounds the Number operand to two decimals, wraps integer data, and gives the result a header that disagrees with its data

- **Status:** open
- **Severity:** medium — divisors or factors below 0.01 cannot be entered, and Active + Active on integer data silently produces wrong values
- **Files:** `pyql3/gui/tools/arithmetic.py:40-42`, `:97-99`, `:150-152`, `:188-199`, `pyql3/core/fits_reader.py:102-114`
- **Related:** M4 says the Active-Image operand's missing float cast "is not currently reachable for integer wraparound (the other operand is always float)". That is wrong: both operands can be **Active Image**. M5 is about the same header object.

### Symptom
- **Number** is a `QDoubleSpinBox` with Qt's default two decimals. `0.0012` reads back as `0.0`, so dividing by it gives "Cannot divide by zero." and multiplying by it gives zeros. Values such as 0.123 become 0.12 without any message.
- **Active Image + Active Image** on a uint16 frame (BITPIX 16, BZERO 32768) holding 40000 gives `uint16 14464` instead of 80000.
- **Active / 2** on the same frame gives float64 data, but the result window's `fits_reader.header` still says `BITPIX 16, BZERO 32768`.
- Header Editor changes to an Arithmetic result are lost on save. The editor writes to `hdul[0].header`, while Save FITS As writes `fits_reader.get_header()`. These are different objects: an edit of `OBJECT` from `before` to `after` was `after` in memory and `before` in the saved file.

### Reproduction
```python
import numpy as np, os, tempfile
from PySide6.QtWidgets import QApplication, QMessageBox
app = QApplication([])
from astropy.io import fits
import pyql3.services.config as config
config._shared_config = config.ConfigManager(os.path.join(tempfile.mkdtemp(), "config.json"))
from pyql3.gui.main_window import MainWindow
from pyql3.gui.tools.arithmetic import ArithmeticDialog
QMessageBox.warning = QMessageBox.critical = staticmethod(lambda *a, **k: print("dialog:", a[2]))
path = os.path.join(tempfile.mkdtemp(), "raw.fits")
fits.PrimaryHDU(np.full((4, 5), 40000, dtype=np.uint16)).writeto(path)   # BITPIX 16, BZERO 32768
win = MainWindow(); win.load_fits(path)
a = ArithmeticDialog(win, win.image_viewer)
a.spin2.setValue(0.0012); print("Number operand 0.0012 reads back as", a.spin2.value())
a.rb_div.setChecked(True); a.calculate()
a.rb2_active.setChecked(True); a.rb_add.setChecked(True); a.calculate()
r = a.result_win.image_viewer.raw_data
print("Active + Active:", r.dtype, r[0, 0], "(expected 80000)")
a.rb2_num.setChecked(True); a.spin2.setValue(2.0); a.rb_div.setChecked(True); a.calculate()
r = a.result_win.image_viewer.raw_data; h = a.result_win.fits_reader.get_header()
print("Active / 2:", r.dtype, r[0, 0], "| result header BITPIX", h["BITPIX"], "BZERO", h.get("BZERO"))
```
```
Number operand 0.0012 reads back as 0.0
dialog: Cannot divide by zero.
Active + Active: uint16 14464 (expected 80000)
Active / 2: float64 20000.0 | result header BITPIX 16 BZERO 32768
```

### Root cause
- `spin1` and `spin2` (`arithmetic.py:40-42`, `:97-99`) never call `setDecimals`.
- The Active operand is `raw_data.copy()` with its original dtype (`:152`). Only the File operand is cast (`:159`).
- The result header is the operand's header copied unchanged (`:197-199`).
- `FitsReader.load_from_memory` (`fits_reader.py:107`, `:113`) builds a `PrimaryHDU`, which normalises a *copy* of the header (BITPIX −64, no BZERO), but then stores the caller's original object as `self.header`. From then on the reader holds two headers that disagree.

### Fix (proposed)
- Use `setDecimals(10)` on both spin boxes, or a `QLineEdit` with a float validator so scientific notation works.
- Cast both array operands with `astype(float)`.
- In `load_from_memory`, set `self.header = hdu.header`, so there is one header object and astropy keeps BITPIX/BZERO consistent with the data.

### Regression test (proposed)
`tests/test_analysis_tools.py::test_arithmetic_keeps_precision_and_range`:
- `spin2.setValue(0.0012)` reads back 0.0012.
- Active + Active of a uint16 40000 frame equals 80000.
- `fits_reader.get_header() is fits_reader.hdul[0].header` after `load_from_memory`.

---

## R24. An AWAV spectral axis is not recognised, and the WCS readout keeps an old position whenever Z is not a wavelength axis

- **Status:** open
- **Severity:** medium — an air-wavelength cube gets no wavelength labels and no sky readout at all, and in other cases the label silently shows a previous position
- **Files:** `pyql3/gui/viewers/image_viewer.py:1186-1199`, `pyql3/gui/viewers/image_viewer.py:1674`, `pyql3/gui/tools/depth_plot.py:1264`, `pyql3/gui/tools/depth_plot.py:1289`, `pyql3/gui/tools/depth_plot.py:1489`
- **Related:** M23, which made a non-finite coordinate read "WCS: N/A". Here the label is never written at all.

### Symptom
- **An AWAV cube** (AGENTS.md lists `AWAV` as an OSIRIS spectral type, and MUSE and KCWI use it)
  shows "Slice: 0" with no wavelength, no wavelength axis above the timeline, and "WCS: N/A" for
  every hover. The Depth Plot review found that its x-axis falls back to "Slice Index (pixels)"
  (`depth_plot.py:1489`) and the line list reports "WCS conversion failed." (`depth_plot.py:1289`).
  `depth_plot.py:1264` already accepts `AWAV`; the viewer does not.
- **When Z is not a spectral axis** — for example after setting X to AXIS 1 on an OSIRIS cube, so
  that RA becomes Z — the WCS label keeps the coordinates of the last pixel hovered before the
  change, while X and Y update.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
from astropy.wcs import WCS
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

def viewer(spectral_ctype):
    w = WCS(naxis=3); w.wcs.ctype = [spectral_ctype, 'DEC--TAN', 'RA---TAN']
    w.wcs.crval = [2.2, 34.0, 242.0]; w.wcs.cdelt = [0.0005, 1e-4, 1e-4]; w.wcs.cunit = ['um', 'deg', 'deg']
    v = ImageViewer(); v.resize(800, 800); v.show()
    v.set_data(np.random.default_rng(0).normal(size=(16, 12, 20)).astype(np.float32), w.to_header())
    app.processEvents(); return v

def hover(v, x, y):
    v.mouse_moved((v.imv.getImageItem().mapToScene(QPointF(x + 0.5, y + 0.5)),))
    return f"{v.lbl_x.text()} {v.lbl_y.text()} | {v.lbl_wcs.text()}"

wave, awav = viewer('WAVE'), viewer('AWAV')
print(f"AWAV: wcs_z_idx={awav.wcs_z_idx} | {awav.lbl_slice_info.text()!r} | {hover(awav, 3, 3)}")
print(f"WAVE: wcs_z_idx={wave.wcs_z_idx} | {wave.lbl_slice_info.text()!r} | {hover(wave, 3, 3)}")
wave.combo_x.setCurrentText("AXIS 1"); app.processEvents()   # wavelength on X, RA becomes Z
print("WAVE, X = AXIS 1:", hover(wave, 15, 9))
```
```
AWAV: wcs_z_idx=None | 'Slice: 0' | X: 3 Y: 3 | WCS: N/A
WAVE: wcs_z_idx=0 | 'Slice: 0, Wavelength: 2.2005 µm' | X: 3 Y: 3 | WCS: RA: 16h08m00.116s  |  DEC: 34d00m01.44s
WAVE, X = AXIS 1: X: 15 Y: 9 | WCS: RA: 16h08m00.116s  |  DEC: 34d00m01.44s
```

### Root cause
`apply_axis_mapping` decides that Z is spectral with `'WAVE' in ctype` (`:1191`). `'WAVE'` is not a
substring of `'AWAV'`, so `wcs_z_idx` stays `None`. The 3-D readout branch requires
`wcs_z_idx is not None` (`:1674`), and there is no `else` for a 3-axis WCS without it, so
`lbl_wcs` is left at whatever it last said.

### Fix (proposed)
Identify the spectral axis with `self.wcs.wcs.spec` (astropy's index of the spectral axis, -1 when
there is none). In `mouse_moved`, compute the spatial readout without requiring a spectral Z, and
set `lbl_wcs` to "WCS: N/A" in every path that does not produce a coordinate.

### Regression test (proposed)
`tests/test_image_viewer.py::test_awav_is_a_spectral_axis`: an AWAV cube gets a non-None
`wcs_z_idx` and a wavelength in the slice label. Add
`::test_wcs_readout_never_shows_a_previous_position`: hover, remap X to AXIS 1, hover elsewhere, and
assert that the label changed or reads "WCS: N/A".

---

## R25. A non-science extension is set up from its own header only: wrong default axes, no WCS, and a Total DN multiplier of 1

- **Status:** open
- **Severity:** medium — switching to an error or noise extension rearranges the axes or silently changes units
- **Files:** `pyql3/gui/viewers/image_viewer.py:1077-1089`, `pyql3/gui/viewers/image_viewer.py:1117-1122`, `pyql3/gui/main_window.py:619-620`, `pyql3/core/fits_reader.py:79`
- **Related:** B6. That entry stopped table extensions from reaching the viewer. This one concerns image extensions whose header lacks the keywords the viewer depends on.

### Symptom
- **JWST NIRSpec s3d ERR/DQ extensions** have no WCS cards; only SCI does. ERR therefore gets the
  OSIRIS default X = AXIS 3, which is wavelength: a 60-channel test cube shows a (60, 9)
  wavelength-by-Dec plane with 7 "slices" along RA, where SCI shows (7, 9) with 60 slices. On
  `example/f170lp_g235h-f170lp_s3d.fits` the ERR plane is (3814, 49).
- **OSIRIS extension 1 (noise)** has no `ITIME`, so **As Total DN** multiplies by 1.0 while the label
  says DN. It also loses the wavelength labels and the sky readout.

### Reproduction
```python
import os, tempfile, numpy as np
from PySide6.QtWidgets import QApplication
from astropy.io import fits
from astropy.wcs import WCS
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.core.fits_reader import FitsReader

def wcs_header(ctypes):
    w = WCS(naxis=3); w.wcs.ctype = ctypes; w.wcs.cdelt = [1e-5, 1e-5, 1e-9]; return w.to_header()
tmp = tempfile.mkdtemp()
jwst = os.path.join(tmp, "s3d.fits")     # JWST s3d layout: WCS only in SCI
cube = np.zeros((60, 9, 7), np.float32)  # (wave, dec, ra)
fits.HDUList([fits.PrimaryHDU(), fits.ImageHDU(cube, wcs_header(['RA---TAN', 'DEC--TAN', 'WAVE']), name='SCI'),
              fits.ImageHDU(cube, name='ERR')]).writeto(jwst)
osiris = os.path.join(tmp, "osiris.fits")  # OSIRIS layout: ITIME and WCS only in the primary
h = wcs_header(['WAVE', 'DEC--TAN', 'RA---TAN']); h['ITIME'] = 900.0; h['COADDS'] = 1
fits.HDUList([fits.PrimaryHDU(np.zeros((7, 9, 60), np.float32), h), fits.ImageHDU(np.zeros((7, 9, 60), np.float32))]).writeto(osiris)
for path, ext in ((jwst, 1), (jwst, 2), (osiris, 0), (osiris, 1)):
    r = FitsReader(path); r.load(path, ext=ext)
    v = ImageViewer(); v.set_data(r.data, r.header); v.disp_as_dn = True
    print(f"{os.path.basename(path)} ext {ext}: X {v.combo_x.currentText()}, plane {v.display_data.shape[1:]}, "
          f"{v.transposed_data.shape[0]} slices, multiplier {v.data_multiplier}, wcs_z_idx {v.wcs_z_idx}")
```
```
s3d.fits ext 1: X AXIS 1, plane (7, 9), 60 slices, multiplier 1.0, wcs_z_idx 2
s3d.fits ext 2: X AXIS 3, plane (60, 9), 7 slices, multiplier 1.0, wcs_z_idx None
osiris.fits ext 0: X AXIS 3, plane (7, 9), 60 slices, multiplier 900.0, wcs_z_idx 0
osiris.fits ext 1: X AXIS 3, plane (7, 9), 60 slices, multiplier 1.0, wcs_z_idx None
```

### Root cause
`FitsReader.load` publishes the selected extension's own header (`fits_reader.py:79`), and
`load_fits` passes it straight to `set_data` (`main_window.py:619-620`). `set_data` builds the WCS
and reads `ITIME`/`COADDS` from that header alone (`image_viewer.py:1077-1089`). It then picks the
default X axis by checking whether `CTYPE1` contains `RA` (`:1117-1122`), which treats any header
without a `CTYPE1` as OSIRIS-ordered.

### Fix (proposed)
When the selected extension has no celestial or spectral WCS, or no integration time, fall back to
the primary header or to the SCI extension with the same shape. Choose the default axes from
`wcs.wcs.spec` (the spectral axis becomes Z) instead of the `CTYPE1` string test. Where no WCS
exists anywhere in the file, keep the current default.

### Regression test (proposed)
`tests/test_main_window_and_poller.py::test_error_extension_keeps_the_science_axis_layout`: the
three-HDU file above, asserting that ext 2 shows the same plane shape as ext 1. Add
`::test_noise_extension_uses_the_primary_integration_time`, asserting that `data_multiplier` is the
same for ext 0 and ext 1.

---

## R26. Choosing the axis the other combo box already shows leaves `current_y_axis` wrong

- **Status:** open
- **Severity:** medium — the sky readout, the region sky frame and the compass all read the wrong FITS axes after one ordinary combo box change
- **Files:** `pyql3/gui/viewers/image_viewer.py:1160-1175`, `pyql3/gui/viewers/image_viewer.py:356-377`, `pyql3/gui/viewers/image_viewer.py:1685-1695`

### Symptom
On a JWST-ordered cube (X = AXIS 1, Y = AXIS 2), setting Y to AXIS 1 makes the combo box revert to
AXIS 2, but the stored `current_y_axis` stays at AXIS 1:

- `display_axis_indices()` returns `(0, 0)`.
- `CelestialMap` reports "the display shows FITS axes 1 and 1, ... this plane has no sky
  coordinates", so regions lose their sky frame.
- The readout prints RA twice, and the RA is wrong: `RA: 17h45m36.038s | RA: 17h45m36.038s`, where
  the pixel is at 17h45m36.025s, -29d00m00.32s.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
from astropy.wcs import WCS
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.core.sky import CelestialMap

w = WCS(naxis=3); w.wcs.ctype = ['RA---TAN', 'DEC--TAN', 'WAVE']   # JWST NIRSpec order
w.wcs.crval = [266.4, -29.0, 2.0e-6]; w.wcs.cdelt = [-1e-5, 1e-5, 1e-9]
w.wcs.crpix = [20, 15, 1]; w.wcs.cunit = ['deg', 'deg', 'm']
v = ImageViewer(); v.resize(800, 800); v.show()
v.set_data(np.zeros((25, 30, 40), np.float32), w.to_header()); app.processEvents()
v.combo_y.setCurrentText("AXIS 1")            # the user picks the axis X already shows
print("combos", v.combo_x.currentText(), v.combo_y.currentText(),
      "| current_x/y", v.current_x_axis, v.current_y_axis,
      "| display_axis_indices", v.display_axis_indices())
print("CelestialMap:", CelestialMap(v.wcs, *v.display_axis_indices()).reason)
v.mouse_moved((v.imv.getImageItem().mapToScene(QPointF(10.5, 5.5)),))
print("readout at (10, 5):", v.lbl_wcs.text())
print("truth:", v.wcs.celestial.pixel_to_world(10, 5).to_string('hmsdms', precision=3))
```
```
combos AXIS 1 AXIS 2 | current_x/y AXIS 1 AXIS 1 | display_axis_indices (0, 0)
CelestialMap: the display shows FITS axes 1 and 1, but the celestial axes are axes 1 and 2 — this plane has no sky coordinates
readout at (10, 5): WCS: RA: 17h45m36.038s  |  RA: 17h45m36.038s
truth: 17h45m36.025s -29d00m00.324s
```

### Root cause
`apply_axis_mapping` stores `self.current_x_axis` and `self.current_y_axis` (`:1162-1163`) before
it resolves a duplicate (`:1165-1172`). The resolution changes only the local `y_axis` and the
combo box. Everything that asks which FITS axes are on screen reads the stale attribute:
`display_axis_indices()` (`:356-377`), the readout's `val_dict` (`:1685-1695`),
`get_north_angle_base`, the region layer, and the Depth Plot's wavelength lookup.

### Fix (proposed)
Assign `current_x_axis`, `current_y_axis` and `current_z_axis` once, after the duplicate has been
resolved. Swapping the two axes, rather than reverting the user's choice, would also do what was
asked.

### Regression test (proposed)
`tests/test_image_viewer.py::test_duplicate_axis_choice_keeps_current_axes_consistent`: set each
combo box to the value of the other, then assert that `display_axis_indices()` has two distinct
entries matching `combo_x` / `combo_y`.

---

## R27. Arrow keys, Home, End and Space on the image replace a collapsed plane with a single channel

- **Status:** ✅ FIXED — `pyql3/gui/viewers/image_viewer.py` (`on_imv_time_changed` redraws the collapse in Z Range mode; `eventFilter` ignores presses of the ImageView's frame keys in that mode, because Home at frame 0 and End at the last frame repaint a channel without emitting `sigTimeChanged`, and lets releases through; `z_mode_changed` stops playback on entering the mode; in Boxcar mode `apply_z_slice` keeps the ImageView's frame at the slider, `eventFilter` turns the stepping keys into slider moves, and `_after_playback_tick` stops playback at either end of the cube), `docs/developer.md`, covered by `test_frame_keys_do_not_replace_a_collapse` (range and boxcar), `test_boxcar_playback_at_the_end_keeps_the_median`, `test_switching_to_a_collapse_stops_playback` and `test_a_key_released_after_switching_to_a_collapse_is_not_left_pressed` (`tests/test_image_viewer.py`)
- **Severity:** medium — an ordinary keyboard action shows a different plane while the label still says the range is collapsed
- **Files:** `pyql3/gui/viewers/image_viewer.py:443-468`, `pyql3/gui/viewers/image_viewer.py:941-958`
- **Related:** B4, which synced a timeline drag back to the slider in Z Slice mode. This is the keyboard path in Collapse Region mode, which that sync skips.

### Symptom
In Collapse Region mode (Sum over 4-8, displayed value 30), clicking the image and pressing **End**
shows channel 19 (19.0) and **Home** shows channel 0 (0.0). Left/Right step through channels and
Space plays them. In each case the label still reads "Collapsed: 4-8". Boxcar mode has the same fault;
see below.

**Boxcar mode, found in the Phase 2 review.** The review first said Boxcar mode recovers. It does
not. With Boxcar 5 and the slider moved to 10, the ImageView's own frame index stays at 0. **Home**
then paints raw channel 0 (0.0) while the label reads "Slice (Boxcar): 8-12" and `current_plane()` is
10.0, and **Right** from slider 10 steps the ImageView from frame 0 to 1, which moves the slider to 1.
Playback has a related fault at the ends of the cube: pyqtgraph does not stop at the last frame, and
each further tick repaints raw channel 19 (4.0 in place of the median 7.0) without emitting
`sigTimeChanged`. The same behaviour is on HEAD.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from astropy.wcs import WCS
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

w = WCS(naxis=3); w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
w.wcs.crval = [2.2, 34.0, 242.0]; w.wcs.cdelt = [0.0005, 1e-4, 1e-4]; w.wcs.cunit = ['um', 'deg', 'deg']
cube = np.zeros((16, 12, 20), np.float32); cube[:] = np.arange(20)   # channel k holds k
v = ImageViewer(); v.resize(800, 800); v.show(); v.set_data(cube, w.to_header()); app.processEvents()
v.combo_collapse.setCurrentText("Sum"); v.txt_zmin.setText("4"); v.txt_zmax.setText("8")
v.radio_range.setChecked(True); app.processEvents()
shown = lambda: float(np.nanmean(v.imv.getImageItem().image))
print("collapsed :", shown(), "|", v.lbl_slice_info.text())
view = v.imv.ui.graphicsView; view.setFocus()
for key in (Qt.Key_End, Qt.Key_Home):
    QTest.keyClick(view, key); app.processEvents()
    print(f"{key.name:9s} :", shown(), "|", v.lbl_slice_info.text())
```
```
collapsed : 30.0 | Collapsed: 4-8, Wavelengths: 2.2025-2.2045 µm
Key_End   : 19.0 | Collapsed: 4-8, Wavelengths: 2.2025-2.2045 µm
Key_Home  : 0.0 | Collapsed: 4-8, Wavelengths: 2.2025-2.2045 µm
```

### Root cause
`apply_z_range` draws the collapsed plane with `bypass_imv=True` (`:958`), which sets only the
ImageItem. The ImageView keeps the full cube as its own image, and its time axis stays active.
pyqtgraph's `ImageView.keyPressEvent` calls `setCurrentIndex`, which repaints the ImageItem from
that stored cube. The resulting `sigTimeChanged` reaches `on_imv_time_changed`, which returns early
when Z Slice is not checked (`:452`), so nothing restores the collapse.

In Boxcar mode the boxcar branch of `apply_z_slice` also draws with `bypass_imv=True` and never calls
`imv.setCurrentIndex`, so the ImageView's frame stays wherever it last was. A frame key then steps
from that stale frame, and Home at frame 0 (or a playback tick at either end) repaints a channel
without a `sigTimeChanged`, which is the only path by which the slider sync could redraw the median.

### Fix (proposed)
In `on_imv_time_changed`, when `radio_range` is checked, re-run `apply_z_range()` (or redraw the
cached collapsed plane) instead of returning. Alternatively, install an event filter on the
ImageView that ignores the frame keys in range mode.

In Boxcar mode (as done): `apply_z_slice` calls `imv.setCurrentIndex(val)` under `_syncing_slice`
before the boxcar paint; the event filter turns Left, Right, Home, End, PageUp and PageDown into
clamped `slider_slice.setValue(...)` moves, so the median is drawn for the new centre; and a slot
run after each playback tick stops playback at either end of the cube and redraws the median.

### Regression test (proposed)
`tests/test_image_viewer.py::test_frame_keys_do_not_replace_a_collapse`: the channel cube collapsed
over 4-8 with Sum; send End, Home and Right with `QTest.keyClick` to `imv.ui.graphicsView`, and
assert that the rendered image is still 30.0 after each. Parametrized over range and boxcar; the
boxcar case asserts the slider position after each key and that the rendered image is the median
over `boxcar_range()`. `::test_boxcar_playback_at_the_end_keeps_the_median` covers the playback ticks.

---

## R28. In Depth Plot mode the Y-axis label keeps the unit the dialog opened with

- **Status:** open
- **Severity:** medium — after a DN/s ↔ Total DN switch, the label and the plotted values
  disagree by ITIME × COADDS
- **Files:** `pyql3/gui/tools/depth_plot.py:139-140`, `:1418-1503`, `:1681-1682`, `:1707-1708`;
  `pyql3/gui/main_window.py:773-792`
- **Related:** S5 (the cuts applied the multiplier twice). Here the values are right and
  the label is wrong.

### Symptom
On a cube with ITIME = 10, switching Units to Total DN multiplies the spectrum from 1.0 to
10.0, but the axis still reads "Intensity (DN/s)". The reverse switch leaves "DN" on DN/s
values. Switching Type to a cut and back corrects the label, because the cut branches set
it.

### Reproduction
```python
import numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

hdr = fits.Header(); hdr['ITIME'] = 10.0
v = ImageViewer(); v.set_data(np.ones((30, 20, 5)), hdr)
d = DepthPlotDialog(image_viewer=v)
d.combo_calc.setCurrentText("Average")
print(d.plot_data.getData()[1][0], d.plot_widget.getAxis('left').labelText)
# MainWindow.set_display_unit(True): refresh_display(), then update_plot() on each open tool
v.disp_as_dn = True; v.refresh_display(); d.update_plot()
print(d.plot_data.getData()[1][0], d.plot_widget.getAxis('left').labelText)
```
```
1.0 Intensity (DN/s)
10.0 Intensity (DN/s)
```
The same result was obtained through `MainWindow.set_display_unit(True)`.

### Root cause
The left label is written in `__init__` (`depth_plot.py:139-140`) and in the two cut
branches (`:1681-1682`, `:1707-1708`). `_draw_depth_curves()` (`:1418-1503`) applies
`data_multiplier` but never writes the label. `MainWindow.update_tools_for_unit()`
(`main_window.py:783-792`) relies on `update_plot()` to refresh both.

### Fix (proposed)
Move the three copies into one `_set_intensity_label()` and call it from
`_draw_depth_curves()` as well as the cut branches.

### Regression test (proposed)
`tests/test_depth_plot.py::test_the_intensity_label_follows_the_display_unit`: set
`disp_as_dn`, call `update_plot()` in Depth Plot mode, and assert that the left label
contains "DN" and not "DN/s".

## R29. Selecting a catalog row under a view rotation recentres the view somewhere else

- **Status:** open
- **Severity:** medium — the selected source is moved off screen instead of to the centre
- **Files:** `pyql3/gui/tools/plot_catalog.py:1204-1221`

### Symptom
After a view rotation (Rotate tool, any non-zero angle, including North Up), clicking a row in
the catalog table moves the view, but not to the source. With the view zoomed in and rotated by 90°,
selecting a source at item coordinates (50.5, 30.5) centres the view on (50.5, 30.5) in ViewBox
coordinates. The source is actually at (18.5, 60.5), outside the visible rect. The red highlight ring
is drawn in the right place, off screen.

### Reproduction
```python
import numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
from astropy.table import Table
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
viewer = ImageViewer(); viewer.resize(600, 600); viewer.show()
viewer.set_data(np.zeros((40, 60), np.float32))
dlg = PlotCatalogDialog(None, viewer)
dlg.set_catalog_table(Table({"name": ["a", "b"], "x": [5.0, 50.0], "y": [7.0, 30.0]}), "t")
view = viewer.imv.getView()
view.setRange(xRange=(0, 20), yRange=(0, 20), padding=0)     # zoomed in
viewer.apply_view_rotation(90.0)                             # the Rotate tool's view rotation
dlg.table.selectRow(1)
centre = view.viewRect().center()
source = viewer.imv.getImageItem().mapToParent(QPointF(50.5, 30.5))
print("view centre", (centre.x(), centre.y()), "| source b", (source.x(), source.y()),
      "| on screen:", view.viewRect().contains(source))
```
```
view centre (50.5, 30.5) | source b (18.5, 60.5) | on screen: False
```

### Root cause
`on_table_selection` passes `disp_x + 0.5, disp_y + 0.5` straight to `view.setRange`
(`plot_catalog.py:1217-1221`). Those are ImageItem coordinates, while `setRange` takes ViewBox
coordinates. The two are equal only while the ImageItem has no transform, and a view rotation is a
`QTransform` on the ImageItem (`image_viewer.py:1397`). `update_visible_text_labels` already does the
conversion with `img_item.mapToParent` (`plot_catalog.py:1098`).

### Fix (proposed)
In `on_table_selection`, map the centre with
`self.image_viewer.imv.getImageItem().mapToParent(QPointF(center_x, center_y))` before building the
ranges.

### Regression test (proposed)
`test_selecting_a_row_centres_it_under_a_view_rotation` in `tests/test_plot_catalog.py`. Apply a 90°
view rotation, select a row, and assert that `view.viewRect().contains(img_item.mapToParent(source))`
and that the rect's centre is within 0.01 of the mapped source position.

## R30. SExtractor `X_IMAGE`/`Y_IMAGE` positions are plotted one pixel off

- **Status:** open
- **Severity:** medium — every marker from a SExtractor catalog is offset by one pixel in both axes
- **Files:** `pyql3/gui/tools/plot_catalog.py:48-51`, `pyql3/gui/tools/plot_catalog.py:858-866`,
  `pyql3/gui/main_window.py:1101-1110`, `pyql3/core/coords.py:32-36`, `pyql3/core/coords.py:128-130`

### Symptom
SExtractor writes `X_IMAGE`/`Y_IMAGE` (and the `XWIN_IMAGE` family) in the FITS convention, where the
centre of the first pixel is 1.0. The catalog tool recognises those names, chooses *FITS Pixels*, and
places them as 0-based array indices. A star at numpy `[20, 10]` has `X_IMAGE = 11`, `Y_IMAGE = 21`;
its marker covers pixel (11, 21), whose value is 0.0. Switching the same row to its
`ALPHA_J2000`/`DELTA_J2000` columns puts the marker on pixel (10, 20), value 100.0. The two
coordinate types disagree by 1.0 pixel in x and in y for the same row.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.table import Table
from astropy.wcs import WCS
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
w = WCS(naxis=2); w.wcs.ctype = ['RA---TAN', 'DEC--TAN']; w.wcs.crval = [266.4, -29.0]
w.wcs.crpix = [30, 20]; w.wcs.cdelt = [-0.0001, 0.0001]
data = np.zeros((40, 60), np.float32); data[20, 10] = 100.0  # one star, numpy [row 20, col 10]
viewer = ImageViewer(); viewer.set_data(data, w.to_header())
ra, dec = w.pixel_to_world_values(10, 20)
# SExtractor's X_IMAGE/Y_IMAGE are 1-based, so that star is at (11, 21)
cat = Table({"NUMBER": [1], "X_IMAGE": [11.0], "Y_IMAGE": [21.0],
             "ALPHA_J2000": [float(ra)], "DELTA_J2000": [float(dec)]})
dlg = PlotCatalogDialog(None, viewer); dlg.set_catalog_table(cat, "sextractor")
for kind in (1, 2):                                          # FITS Pixels, then World
    dlg.combo_coord_type.setCurrentIndex(kind)
    x, y = (float(v[0]) for v in dlg.scatter_item.getData())
    px = (int(np.floor(x)), int(np.floor(y)))
    print(dlg.combo_coord_type.currentText(), dlg.combo_x.currentText(), "marker", (round(x, 3), round(y, 3)),
          "pixel", px, "value", viewer.display_data[px])
```
```
FITS Pixels X_IMAGE marker (11.5, 21.5) pixel (11, 21) value 0.0
World (RA/DEC) ALPHA_J2000 marker (10.5, 20.5) pixel (10, 20) value 100.0
```

### Root cause
The *FITS Pixels* branch of `_row_to_display` uses the column values unchanged as orig coordinates
(`plot_catalog.py:858-866`), which are 0-based. `send_regions_to_catalog` relies on that, since regions
are stored in 0-based orig coordinates (`main_window.py:1108-1110`). But `coords.py:32-36` defines
FITS/ds9 coordinates as 1-based and provides `fits_to_index` (`:128-130`) for the conversion, and
`X_COLUMN_NAMES`/`Y_COLUMN_NAMES` (`plot_catalog.py:48-51`) include SExtractor's 1-based `x_image` and
`y_image` next to photutils' 0-based `xcentroid` and `x_fit`. One mode is used for both conventions.

### Fix (proposed)
Give the pixel mode an explicit origin (0-based array index, or 1-based FITS) and apply
`coords.fits_to_index` for the 1-based case. Default to 1-based when the chosen columns are SExtractor
`*_IMAGE` names, and keep the regions hand-off at 0-based.

### Regression test (proposed)
`test_sextractor_image_columns_are_one_based` in `tests/test_plot_catalog.py`. Build the catalog
above and assert that the *FITS Pixels* marker and the World marker for the same row agree to within
0.01 pixel.

## R31. An RA/Dec catalog takes 9.5 s to load and 9.6 s for every change to a marker control

- **Status:** open
- **Severity:** medium — the tool works, but each spin-box step on a large RA/Dec catalog stops the window for about ten seconds
- **Files:** `pyql3/gui/tools/plot_catalog.py:846-857`, `pyql3/gui/tools/plot_catalog.py:868-895`,
  `pyql3/gui/tools/plot_catalog.py:957-958`, `pyql3/gui/tools/plot_catalog.py:429-473`
- **Related:** M29 removed the per-cell cost from building the table. This is the per-row cost of
  resolving positions, which M29 and M30 did not measure because their catalog used pixel columns.

### Symptom
Measured on a 2000 x 2000 image with 66,196 random sources:

| columns | load | one marker-size step |
|---------|------|----------------------|
| `x`, `y` | 0.38 s | 0.32 s |
| `ra`, `dec` | 9.51 s | 9.57 s |

The full pass runs on every change of marker size, shape, colour, *Labels*, the label column,
*Show All*, either coordinate column and the display unit, so a spin box held down on an RA/Dec
catalog queues a ten-second stall per step. A single vectorised `SkyCoord` plus
`world_to_pixel_values` call over the same 66,196 rows takes 0.006 s.

### Reproduction
```python
import time
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.table import Table
from astropy.wcs import WCS
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
w = WCS(naxis=2); w.wcs.ctype = ['RA---TAN', 'DEC--TAN']; w.wcs.crval = [266.4, -29.0]
w.wcs.crpix = [1000, 1000]; w.wcs.cdelt = [-0.0001, 0.0001]
viewer = ImageViewer(); viewer.set_data(np.zeros((2000, 2000), np.float32), w.to_header())
n = 66196
x, y = np.random.default_rng(1).uniform(0, 2000, (2, n))
ra, dec = w.pixel_to_world_values(x, y)
for cols in ({"x": x, "y": y}, {"ra": ra, "dec": dec}):
    dlg = PlotCatalogDialog(None, viewer)
    t0 = time.perf_counter(); dlg.set_catalog_table(Table({"id": np.arange(n), **cols}), "t")
    t1 = time.perf_counter(); dlg.spin_size.setValue(11)
    t2 = time.perf_counter()
    print(f"{list(cols)}: load {t1 - t0:.2f} s, one marker-size step {t2 - t1:.2f} s")
    dlg.close()
```
```
['x', 'y']: load 0.38 s, one marker-size step 0.32 s
['ra', 'dec']: load 9.51 s, one marker-size step 9.57 s
```

### Root cause
`update_plot` iterates over `Table.Row`s and calls `_row_to_display` once per row
(`plot_catalog.py:957-958`). In World mode each call builds its own `SkyCoord`
(`:846-857`) and makes its own `world_to_pixel_values` call (`:868-895`), about 0.14 ms per row. Every
marker control is connected straight to `update_plot` (`:429-473`), so none of that work is reused
between refreshes.

### Fix (proposed)
Resolve whole columns at once in `update_plot`: read the two columns as numpy arrays, build one
`SkyCoord` (or parse sexagesimal strings once), make one `world_to_pixel_values` call, and map the
result with `coords.orig_to_display`, which accepts floats and can be applied element-wise. Keep
`_row_to_display` for the single-row highlight, and cache the resolved positions so that a change of
marker style does not resolve them again.

### Regression test (proposed)
`test_world_catalog_refresh_is_not_per_row` in `tests/test_plot_catalog.py`. Load a 20,000-row RA/Dec
catalog and assert that changing `spin_size` takes under 0.5 s, and that the resolved positions match
`wcs.world_to_pixel_values` for a sample of rows.

## R32. VizieR coordinate column names are not recognised, so their catalogs plot nothing

- **Status:** open
- **Severity:** medium — catalogs downloaded from VizieR, the most common source, do not plot without manual setup
- **Files:** `pyql3/gui/tools/plot_catalog.py:44-47`, `pyql3/gui/tools/plot_catalog.py:733-743`
- **Related:** M28 fixed the pixel-column half of the guess (`x_fit`); this is the RA/Dec half.

### Symptom
VizieR names its J2000 columns `RAJ2000` and `DEJ2000`; Gaia DR3 through VizieR uses `RA_ICRS` and
`DE_ICRS`, and VizieR's computed columns are `RAdeg` and `DEdeg`. The RA spelling is recognised in
two of the three pairs, but none of the Dec spellings is. World is therefore never chosen, the type
stays at *Display Pixels*, and the numeric fallback picks a sequence number and the RA column as X
and Y. Nothing is plotted:

```
RAJ2000/DEJ2000: 'Display Pixels' X=Seq Y=RAJ2000 | Loaded: 2 sources | 0 plotted | 2 out of bounds
RA_ICRS/DE_ICRS: 'Display Pixels' X=Seq Y=RA_ICRS | Loaded: 2 sources | 0 plotted | 2 out of bounds
RAdeg/DEdeg: 'Display Pixels' X=Seq Y=RAdeg | Loaded: 2 sources | 0 plotted | 2 out of bounds
```

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.table import Table
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
viewer = ImageViewer(); viewer.set_data(np.zeros((40, 60), np.float32))
for ra, dec in (("RAJ2000", "DEJ2000"), ("RA_ICRS", "DE_ICRS"), ("RAdeg", "DEdeg")):
    dlg = PlotCatalogDialog(None, viewer)
    dlg.set_catalog_table(Table({"Seq": [1, 2], ra: [266.40, 266.41], dec: [-29.00, -29.01],
                                 "Vmag": [12.0, 13.0]}), "vizier")
    print(f"{ra}/{dec}: {dlg.combo_coord_type.currentText()!r} X={dlg.combo_x.currentText()} "
          f"Y={dlg.combo_y.currentText()} | {dlg.lbl_status.text()}")
    dlg.close()
```
Output as under Symptom.

### Root cause
`DEC_COLUMN_NAMES` (`plot_catalog.py:46-47`) spells Dec as `dec`/`delta` only, so `dej2000`,
`de_icrs` and `dedeg` never match. `RA_COLUMN_NAMES` (`:44-45`) lacks `ra_icrs` and `_raj2000`.
`auto_assign_columns` selects World only when both an RA and a Dec column are found (`:741-742`).

### Fix (proposed)
Add `ra_icrs`, `_raj2000` to `RA_COLUMN_NAMES` and `dej2000`, `_dej2000`, `de_icrs`, `dedeg` to
`DEC_COLUMN_NAMES`, after the existing names so that their ranking is unchanged.

### Regression test (proposed)
Parametrise `test_vizier_coordinate_names_select_world` in `tests/test_plot_catalog.py` over the three
pairs above, asserting `combo_coord_type.currentIndex() == 2` and that X and Y are the RA and Dec
columns.

## R33. The region properties dialog undoes a drag made while it is open

- **Status:** open
- **Severity:** medium — a move or resize the user made is silently reverted by OK, Cancel or closing the dialog
- **Files:** `pyql3/gui/dialogs/region_properties.py:44`, `:193-231`, `:237-242`, `:317-323`

### Symptom
The dialog is deliberately non-modal ("keep working on the image with it open"). Open it by
double-clicking a circle at (10, 10) with radius 2, then drag the circle to (18, 16) and resize it to
radius 5 on the image. The dialog still shows 10, 10, 2. Changing only the colour and pressing OK puts
the circle back at (10, 10) with radius 2. Closing the dialog with its title-bar button, or pressing
Cancel, also puts it back, without the colour change.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.core.regions_model import Circle
from pyql3.gui.dialogs.region_properties import RegionPropertiesDialog
from pyql3.gui.viewers.image_viewer import ImageViewer

v = ImageViewer(); v.set_data(np.zeros((50, 30, 40), np.float32))
layer = v.region_layer
region = layer.add(Circle(x=10.0, y=10.0, radius=2.0))
dialog = RegionPropertiesDialog(region, layer); dialog.show()   # what a double-click opens

roi = layer.item_for(region)
roi.translate([8.0, 6.0])                          # the user drags the circle on the image
roi.setSize([10.0, 10.0], center=[0.5, 0.5])       # ... and resizes it from its handle
print("after the drag:", region.x, region.y, region.radius,
      "| dialog shows:", dialog.spin_x.value(), dialog.spin_y.value(), dialog.spin_size.value())

dialog._colour = "red"                             # picks a colour in the dialog
dialog.accept()                                    # and presses OK
print("after OK:      ", region.x, region.y, region.radius, region.color)
```
```
after the drag: 18.0 16.0 5.0 | dialog shows: 10.0 10.0 2.0
after OK:       10.0 10.0 2.0 red
```

### Root cause
The spin boxes are filled once, in `__init__`, and nothing updates them afterwards. The dialog does
not listen to `RegionLayer.regions_changed`. `apply()` (`:193-231`) then writes every field back to
the region, the stale geometry included. `reject()` (`:237-242`) restores the snapshot taken when the
dialog opened (`:44`). `QDialog.closeEvent` calls `reject()`, so closing the dialog undoes the drag
as well.

### Fix (proposed)
Connect the dialog to `layer.regions_changed` and, when the event concerns `self.region`, refresh
the geometry spin boxes the user has not edited since opening. A simpler option is to make `apply()`
write only the fields whose widget value differs from `_original`. In both cases the snapshot
`reject()` restores should be retaken after a drag, or Cancel should be documented to discard drags.

### Regression test (proposed)
`tests/test_region_properties.py::test_a_drag_while_the_dialog_is_open_survives_ok`: open the
editor, `translate` the ROI, change the colour, `accept()`, and assert the region's `x`, `y` are the
dragged values and its colour is the new one.

## R34. The Region List tracks its selection by row index, so Delete can remove a region the user did not select

- **Status:** open
- **Severity:** medium — the Delete button can remove a different region from the one the user chose, with no undo
- **Files:** `pyql3/gui/tools/region_list.py:98-113`, `:173-179`, `:251-255`

### Symptom
Five regions A to E are listed, and the user selects C (row 2). A is then deleted somewhere else,
for example from its right-click menu on the image. The table is rebuilt, the selection stays on
row 2, and row 2 now holds D. Pressing the dialog's **Delete** button removes D, and C is left.
**Zoom To** goes to the same wrong region. Any deletion of a region listed above the selected one
has this effect.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.core.regions_model import Circle
from pyql3.gui.tools.region_list import RegionListDialog
from pyql3.gui.viewers.image_viewer import ImageViewer

v = ImageViewer(); v.set_data(np.zeros((50, 30, 40), np.float32))
layer = v.region_layer
regions = [layer.add(Circle(x=5.0 + 5 * i, y=10.0, radius=2.0, text=name))
           for i, name in enumerate("ABCDE")]
dialog = RegionListDialog(None, v); dialog.show()
dialog.table.selectRow(2)
print("selected:", [r.text for r in dialog.selected_regions()])

layer.remove(regions[0])            # what the image's right-click Delete on A calls
print("selected after A is deleted elsewhere:", [r.text for r in dialog.selected_regions()])

dialog.delete_selected()            # the Delete button, meant for C
print("regions left:", [r.text for r in layer.regions])
```
```
selected: ['C']
selected after A is deleted elsewhere: ['D']
regions left: ['B', 'C', 'E']
```

### Root cause
`update_plot()` (`:98-113`) rebuilds the table in place on every `regions_changed`. It calls
`setRowCount` and `setItem` row by row, so the selection model keeps its row indices while the
regions shift up underneath them. `selected_regions()` (`:177-179`) turns the selected rows back
into regions through `_region_at(row)` (`:173-175`), which indexes the layer's current list.
`delete_selected()` (`:251-255`) then removes whatever is now at those rows.

### Fix (proposed)
In `update_plot()`, record the selected regions (by identity) before the rebuild. After it, clear
the selection and reselect the rows that hold those same objects. A region that no longer exists
drops out of the selection.

### Regression test (proposed)
`tests/test_region_ui.py::test_the_selection_follows_the_region_when_another_is_deleted`: select
row 2, call `layer.remove()` on row 0's region, and assert `selected_regions()` is still the
originally selected object (checked with `is`).

## R35. Labels in the aggregate overlay never come back after a pan or zoom once none are built

- **Status:** open
- **Severity:** medium — named sources in a large region file lose their labels, and the status bar's advice to zoom in does not work
- **Files:** `pyql3/gui/viewers/region_layer.py:652-672`, `:578-594`
- **Related:** M30 (catalog labels rebuilt on every pan). The policy shared through
  `label_policy.py` is the same; this is a separate defect in the region layer's pan handler.

### Symptom
Above `INTERACTIVE_LIMIT` (500) regions, labels are rebuilt 200 ms after the view stops moving. If
no label is built at the moment a pan or zoom starts, the rebuild is never scheduled. There are two
ways to reach that state:

1. **The view is on an empty part of the field.** With 600 labelled regions in the lower part of a
   40×30 plane, the view was moved to an empty corner and then panned onto 190 of the sources.
   0 labels were drawn. They appeared only when something else forced a rebuild (220 labels).
2. **There were too many labels.** When more than `LABEL_SAFETY_LIMIT` labels are in view, none are
   built and the status bar says "Zoom in, or turn them off". Zooming in does not bring them back.
   With the limit lowered to 50, 600 labels in view were refused, and after zooming to 22 labelled
   regions in view, 0 were drawn. The guard still reported `suppressing`.

### Reproduction
```python
import time, numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.core.regions_model import Circle
from pyql3.gui.viewers.image_viewer import ImageViewer

def settle():                       # longer than LABEL_REDRAW_DELAY_MS
    end = time.time() + 0.4
    while time.time() < end:
        app.processEvents()

v = ImageViewer(); v.set_data(np.zeros((50, 30, 40), np.float32))
v.resize(800, 700); v.show(); app.processEvents()
layer, view = v.region_layer, v.imv.getView()
# 600 named sources (> INTERACTIVE_LIMIT, so the aggregate overlay) in the lower half of the field
layer.set_regions([Circle(x=float(i % 30), y=2.0 + (i // 30) * 0.2, radius=0.3, text=f"s{i}")
                   for i in range(600)])
view.setRange(xRange=(30, 40), yRange=(20, 30), padding=0); settle()   # an empty corner
print("empty corner: labels drawn", len(layer._bulk_labels))
view.setRange(xRange=(0, 10), yRange=(0, 6), padding=0); settle()      # pan onto the sources
rect = view.viewRect()
in_view = sum(rect.contains(*layer._to_item(r.x, r.y)) for r in layer.regions)
print("panned onto", in_view, "labelled sources: labels drawn", len(layer._bulk_labels))
```
```
empty corner: labels drawn 0
panned onto 190 labelled sources: labels drawn 0
```

### Root cause
`_on_view_range_changed()` returns early unless there are labels to hide (`:661-664`):
`bool(self._bulk_labels) or any(entry.label is not None ...)`. In aggregate mode the entries hold no
items, so the test reduces to "are any overlay labels built right now". When `_draw_bulk_labels()`
(`:578-594`) built none, because none were in view or because the density guard refused them, the
handler returns before `self._label_timer.start(...)`. `_labels_settled()` therefore never runs, and
the labels are not rebuilt for the new view.

### Fix (proposed)
In aggregate mode, decide from the model rather than from the labels currently built. Restart the
timer whenever any visible region has `text`, or whenever the layer is in bulk mode and labels are
switched on. Hiding the existing `_bulk_labels` can stay as it is.

### Regression test (proposed)
`tests/test_region_layer.py::test_overlay_labels_appear_after_panning_from_an_empty_area` and
`::test_overlay_labels_return_after_zooming_in_below_the_ceiling` (monkeypatching
`region_layer.LABEL_SAFETY_LIMIT`, as the existing hang-guard test does): after the second
`setRange` and a wait longer than `LABEL_REDRAW_DELAY_MS`, assert `_bulk_labels` is non-empty.

## R36. A region's channel range is not re-checked when the viewer enters or changes Collapse Region mode

- **Status:** open
- **Severity:** medium — regions restricted to an emission line show or hide according to the previous mode, not what is on screen
- **Files:** `pyql3/gui/viewers/image_viewer.py:878-897`, `:926-961`, `:1004-1039`, `pyql3/gui/viewers/region_layer.py:271-276`

### Symptom
A circle with `z_range: [0, 5]`, shown on Z Slice 2:

| action | `current_z()` | in range | circle shown |
|---|---|---|---|
| Z Slice 2 | 2 | yes | yes |
| switch to Collapse Region 30-40 | 34 | no | **yes** |
| Z Slice 45, then Collapse Region 1-4 | 2 | yes | **no** |

The state corrects itself only on an unrelated event. A pan re-runs visibility after the label
debounce, but only if some region has a label. In collapse mode the slider is disabled, so moving it
is not an option.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.core.regions_model import Circle
from pyql3.gui.viewers.image_viewer import ImageViewer

v = ImageViewer(); v.set_data(np.zeros((50, 30, 40), np.float32))
region = v.region_layer.add(Circle(x=10.0, y=10.0, radius=2.0, z_range=(0, 5)))
roi = v.region_layer.item_for(region)

v.slider_slice.setValue(2)
print("Z Slice 2:              current_z", v.current_z(), "visible", roi.isVisible())

v.txt_zmin.setText("30"); v.txt_zmax.setText("40")
v.radio_range.setChecked(True)                    # Collapse Region 30-40
print("Collapse Region 30-40:  current_z", v.current_z(), "visible", roi.isVisible())

v.radio_slice.setChecked(True); v.slider_slice.setValue(45)
v.txt_zmin.setText("1"); v.txt_zmax.setText("4")
v.radio_range.setChecked(True)                    # Collapse Region 1-4
print("Collapse Region 1-4:    current_z", v.current_z(), "visible", roi.isVisible())
```
```
Z Slice 2:              current_z 2 visible True
Collapse Region 30-40:  current_z 34 visible True
Collapse Region 1-4:    current_z 2 visible False
```

### Root cause
The layer re-checks channel ranges on only two signals: `display_changed`, which triggers a full
`refresh()`, and `slider_slice.valueChanged` (`region_layer.py:271-276`). `z_mode_changed()`
(`image_viewer.py:878-897`), `apply_z_range()` (`:926-961`) and `apply_z_slice()` (`:1004-1039`) draw
the new plane through `update_image_display(bypass_imv=True)` and emit neither. The value
`current_z()` returns changes, from the slider to the middle of the range, but the layer is not told.

### Fix (proposed)
Have `apply_z_range()` and `apply_z_slice()` end by notifying the layer, either through a small
`z_changed` signal that the layer connects to `update_channel_visibility`, or by calling it
directly. While there, decide what a channel range means against a collapsed range. Overlap with
`[zmin, zmax]` probably matches intent better than the midpoint.

### Regression test (proposed)
`tests/test_region_layer.py::test_channel_ranges_follow_a_collapse_range`: with a `z_range=(0, 5)`
circle, switch `radio_range` on with 30-40 and assert the item is hidden. Then apply 1-4 and assert
it is shown.

## R37. With non-square pixels, arrow and box angles are converted to the sky wrongly, and a save and load on the same image changes them

- **Status:** open
- **Severity:** medium — wrong by tens of degrees for IFUs with rectangular spaxels; OSIRIS and NIRSpec cubes have square pixels and are unaffected
- **Files:** `pyql3/core/ds9_regions.py:387-413`, `pyql3/core/ds9_regions.py:371-377`, `pyql3/core/ds9_regions.py:654`, `pyql3/core/regions_io.py:226-245`, `pyql3/core/regions_io.py:308-317`, `pyql3/core/sky.py:142-165`

### Symptom
On a WCS with 0.36" × 0.72" pixels (aspect 1:2) and no rotation, take an arrow of 10 px at 45°:
- The sky angle written to the file is 70.67°. The true value, computed with `SkyCoord.position_angle` from its tail to its head, is 63.44°.
- The length is written as 5.40". The arrow is 5.69" long on the sky.
- Saved and loaded on the *same* image, the arrow and a box at 45° both come back at 33.16°, and the frame question is asked.

Measured over all angles, the worst change on a round trip through the same image:

| pixel aspect | worst change |
|---|---|
| 1:1.05 | 0.06° |
| 1:1.2 | 0.97° |
| 1:2 | 41.3° |
| 1:2.4 | 46.7° |

The ds9 sky export and import give the same numbers.

### Reproduction
```python
from astropy.coordinates import SkyCoord
from astropy.wcs import WCS
from pyql3.core.regions_model import Arrow, Box
from pyql3.core.regions_io import with_sky_anchors, placed_on_image
from pyql3.core.ds9_regions import pixel_angle_to_sky
from pyql3.core.sky import CelestialMap

w = WCS(naxis=2)
w.wcs.ctype, w.wcs.crval = ['RA---TAN', 'DEC--TAN'], [266.4, 34.0]
w.wcs.cdelt, w.wcs.crpix = [-1e-4, 2e-4], [50, 50]          # 0.36" x 0.72" pixels
m = CelestialMap(w, 0, 1)
arrow = Arrow(x=30.0, y=20.0, length=10.0, angle=45.0)
tail, head = (SkyCoord(*m.to_sky(*p), unit="deg") for p in ((arrow.x, arrow.y), arrow.end))
print("sky angle: written %.2f, true %.2f" % (pixel_angle_to_sky(m, 30.0, 20.0, 45.0),
                                             (tail.position_angle(head).deg + 90) % 360))
print("length:    written %.2f\", true %.2f\"" % (m.pixels_to_arcsec(10.0),
                                                 tail.separation(head).arcsec))
for r in (arrow, Box(x=30.0, y=20.0, width=6.0, height=3.0, angle=45.0)):
    asked = []
    placed, _ = placed_on_image(with_sky_anchors([r], wcs=w), wcs=w,
                                choose_frame=lambda offer: asked.append(offer) or "sky")
    print(f"{type(r).__name__} on its own image: angle 45.0 -> {placed[0].angle:.2f}, asked: {bool(asked)}")
```
```
sky angle: written 70.67, true 63.44
length:    written 5.40", true 5.69"
Arrow on its own image: angle 45.0 -> 33.16, asked: True
Box on its own image: angle 45.0 -> 33.16, asked: True
```

### Root cause
`pixel_angle_to_sky` (`ds9_regions.py:387-406`) rotates a 2 × 1 pixel rectangle by the angle, converts it to the sky, and reads back the rectangle's angle. regions 0.12 does that conversion with `pixel_shape_to_sky_svd`, which returns the principal axes of the mapped shape:
- With square pixels, the mapped width axis is a principal axis, so the result is the arrow's direction.
- With unequal scales, the mapped width and height are no longer perpendicular, and the principal axis is a different direction.

`sky_angle_to_pixel` (`:409-413`) uses a 2" × 1" probe, which is a different shape in pixels, so the two functions are not inverses. Lengths and sizes go through `pixel_scale_arcsec` (`sky.py:142-153`), the arithmetic mean of the two axis scales, whatever the direction. The same helpers serve the YAML anchors (`regions_io.py:226-245`, `:308-317`) and the ds9 arrow export and import (`ds9_regions.py:371-377`, `:643-654`).

### Fix (proposed)
Convert a direction by mapping the direction itself, not a rectangle:
- Pixel to sky: `to_sky` of the tail and of a point a small step along the angle; the sky angle is `position_angle + 90`; the length is the separation from tail to head.
- Sky to pixel: `directional_offset_by(angle - 90, step)` and then `from_skycoord`, with `atan2` in orig coordinates.

Use that for arrows and for the anchor angle of boxes. Leave the library to convert boxes for ds9 output.

### Regression test (proposed)
`tests/test_ds9_regions.py::test_an_arrow_points_the_same_way_on_the_sky_with_non_square_pixels`, on a 1:2 WCS:
- The written angle equals `position_angle + 90` within 0.01°.
- A sky round trip on the same image returns the angle within 0.01° and the length within 1e-3.

## R38. With the display axes swapped (X = DEC, Y = RA), box and arrow angles are passed to and from the WCS unconverted, so they come out mirrored about the diagonal

- **Status:** open
- **Severity:** medium — wrong orientation in ds9 and on import, but only after the user picks a non-default AXIS mapping
- **Files:** `pyql3/core/sky.py:75-90`, `pyql3/core/ds9_regions.py:304-314`, `pyql3/core/ds9_regions.py:377`, `pyql3/core/ds9_regions.py:404`, `pyql3/core/ds9_regions.py:654`, `pyql3/core/ds9_regions.py:770-774`, `pyql3/core/regions_io.py:244`, `pyql3/core/regions_io.py:314-317`

### Symptom
Take a box and an arrow on an OSIRIS-ordered cube, first described in the default mapping (X = RA, Y = DEC). Then describe the same physical box and arrow after switching to X = DEC, Y = RA, where (x, y) becomes (y, x) and a direction θ becomes 90 − θ. The sky exports should be identical. They are not: the position and size agree, but the angle is 25.00° from the default mapping and 65.00° from the swapped one. Reading the correct file back on the swapped display gives 25° where 65° is right.

Within the program the round trip hides this, because both directions make the same mistake. ds9, and a file from ds9, do not. Arrow directions are off by up to 180°. Positions are handled correctly.

### Reproduction
```python
from astropy.wcs import WCS
from pyql3.core.regions_model import Arrow, Box, RegionList
from pyql3.core.ds9_regions import to_ds9, from_ds9

w = WCS(naxis=3)                                   # OSIRIS order: WAVE, DEC, RA
w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
w.wcs.crval, w.wcs.crpix = [2.2, 34.0, 266.4], [1, 10, 32]
w.wcs.cdelt, w.wcs.cunit = [5e-4, 1e-4, -1e-4], ['um', 'deg', 'deg']

# One box and one arrow in the default mapping (X=RA, Y=DEC) and in the swapped one
# (X=DEC, Y=RA): (x, y) becomes (y, x) and a direction t becomes 90 - t.
default = [Box(x=30.0, y=12.0, width=10.0, height=3.0, angle=25.0),
           Arrow(x=30.0, y=12.0, length=8.0, angle=25.0)]
swapped = [Box(x=12.0, y=30.0, width=10.0, height=3.0, angle=65.0),
           Arrow(x=12.0, y=30.0, length=8.0, angle=65.0)]
a, _ = to_ds9(RegionList(regions=default), wcs=w, axis_indices=(2, 1), frame="sky")
b, _ = to_ds9(RegionList(regions=swapped), wcs=w, axis_indices=(1, 2), frame="sky")
print("\n".join(a.splitlines()[-2:]))
print("\n".join(b.splitlines()[-2:]))
back, _ = from_ds9(a, wcs=w, axis_indices=(1, 2))
print("read on the swapped display:", [(type(r).__name__, round(r.angle, 2)) for r in back])
```
```
box(266.40012062,34.00030000,0.00100000,0.00030000,25.00006746)
# vector(266.40012062,34.00030000,0.00080000,25.00006747) vector=1 color=green
box(266.40012062,34.00030000,0.00100000,0.00030000,65.00006746)
# vector(266.40012062,34.00030000,0.00080000,65.00006746) vector=1 color=green
read on the swapped display: [('Box', 25.0), ('Arrow', 25.0)]
```

### Root cause
`CelestialMap.swapped` (`sky.py:75-90`) exchanges coordinates only. `_with_wcs_pixels` (`ds9_regions.py:304-314`) moves a library shape's centre into the WCS's axis order, but leaves its angle as it was measured from orig X. `pixel_angle_to_sky` (`:404`) and `sky_angle_to_pixel` (`:413`) take and return angles in the WCS's pixel axes, and every caller treats those as orig angles:
- `_arrow_lines` (`:377`) and `_arrow_from_match` (`:654`);
- the box branch of `_from_regions_shape` (`:770-774`);
- `sky_anchor_for` (`regions_io.py:314-317`) and `_set_angle` (`:244`).

Exchanging two axes is a reflection, under which a direction θ becomes 90 − θ.

### Fix (proposed)
When `mapping.swapped`, convert angles as θ → (90 − θ) mod 360 on the way into the WCS's pixel axes and on the way back. Do it in one helper on `CelestialMap`, used by `_with_wcs_pixels`, the two angle helpers and the box import. Width and height need no change once the angle is converted. The direction mapping proposed in R37 goes through orig coordinates and so needs no special case.

### Regression test (proposed)
`tests/test_ds9_regions.py::test_a_swapped_display_writes_the_same_sky_regions`: the default and swapped descriptions of one box and one arrow produce identical ds9 lines, and reading a sky file on axes (1, 2) returns 90 − θ.

## R39. A box or arrow at angle 0 can come back at 359.99999999999994, which counts as moved and puts the frame question on its own image

- **Status:** open
- **Severity:** medium — a pointless question, with a note that the file was drawn on another pointing, for about 8% of field rotations at the default angle
- **Files:** `pyql3/core/regions_io.py:213-223`, `pyql3/core/regions_io.py:237-245`, `pyql3/core/ds9_regions.py:413`

### Symptom
Save a box at the default angle 0 on a rotated image and load it back onto the same image. For 30 of 361 field rotations (−111°, −104°, −95°, −91°, −89°, ...) the program asks: "1 region(s) sit up to 0.0 pixel(s) apart in the two frames on this image, so the file was drawn on a different pointing". After the load, the note says the regions were "placed from their sky positions ... moving up to 0.0 pixel(s)". Arrows behave the same.

### Reproduction
```python
import numpy as np
from astropy.wcs import WCS
from pyql3.core.regions_model import Box
from pyql3.core.regions_io import with_sky_anchors, placed_on_image

hits = []
for rotation in range(-180, 181):
    w = WCS(naxis=2)
    w.wcs.ctype, w.wcs.crval = ['RA---TAN', 'DEC--TAN'], [266.4, 34.0]
    w.wcs.cdelt, w.wcs.crpix = [-1e-4, 1e-4], [50, 50]
    r = np.radians(rotation)
    w.wcs.pc = [[np.cos(r), -np.sin(r)], [np.sin(r), np.cos(r)]]
    saved = with_sky_anchors([Box(x=30.0, y=20.0, width=6.0, height=3.0)], wcs=w)
    asked = []
    placed, report = placed_on_image(saved, wcs=w, choose_frame=lambda o: asked.append(o) or "sky")
    if asked:
        hits.append((rotation, asked[0].summary(), placed[0].angle, report.notes[0]))
print(f"asked on its own image for {len(hits)} of 361 rotations, e.g.", [h[0] for h in hits[:5]])
print("first: rotation %d | %s | angle 0.0 -> %r\n  note: %s" % hits[0])
```
```
asked on its own image for 30 of 361 rotations, e.g. [-111, -104, -95, -91, -89]
first: rotation -111 | 1 region(s) sit up to 0.0 pixel(s) apart in the two frames | angle 0.0 -> 359.99999999999994
  note: 1 region(s) were placed from their sky positions rather than their stored pixels, moving up to 0.0 pixel(s): this file was drawn on an image pointed differently from this one
```

### Root cause
`_same_geometry` (`regions_io.py:213-223`) compares every geometry field, `angle` included, with `math.isclose(..., abs_tol=SAME_PLACE)`. The sky round trip ends in `% 360.0` (`ds9_regions.py:413`, `regions_io.py:244-245`), so a result of about −6e-15 becomes 359.99999999999994, which `isclose` treats as 360 away from 0.0. The region therefore becomes a candidate (`regions_io.py:128-135`), and the question is put.

### Fix (proposed)
In `_same_geometry`, compare `angle` fields modulo 360: `abs((a - b + 180) % 360 - 180) <= SAME_PLACE`.

### Regression test (proposed)
`tests/test_region_ui.py`, beside `test_the_question_is_not_put_when_the_frames_agree`: an angle-0 box on fields rotated −111°, −91° and −89° is saved and loaded with a `choose_frame` that raises; assert it is never called and the angle is still 0.

## R40. A region's `frame: image` is ignored when its file is loaded and is rewritten to `frame: sky` when it is saved

- **Status:** open
- **Severity:** medium — the documented way to keep a detector-fixed region on its pixels does not work
- **Files:** `pyql3/core/regions_io.py:165-182`, `pyql3/core/regions_io.py:156-157`, `pyql3/core/regions_io.py:274-299`

### Symptom
`docs/tools.md` says `frame: image` "keeps the stored pixels instead, which is what something fixed to the detector wants". The `load_regions` docstring promises to fall back "to what each region was saved as when there is no one to ask".

Take a file holding a `frame: sky` star and a `frame: image` hot pixel, and load it with no one to ask (`--regions`) on a frame dithered by 5 px. The star moves from (20, 20) to (25, 20), as intended, but the hot pixel also moves, from (70, 70) to (75, 70). Through the menu the question is all-or-nothing, so the same thing happens on "Sky". Saving the loaded set writes `frame: sky` for both regions.

### Reproduction
```python
import os, tempfile, yaml
from astropy.wcs import WCS
from pyql3.core.regions_model import Circle, RegionList
from pyql3.core.regions_io import with_sky_anchors, load_regions, save_regions

def image(crpix1):
    w = WCS(naxis=2)
    w.wcs.ctype, w.wcs.crval = ['RA---TAN', 'DEC--TAN'], [266.4, 34.0]
    w.wcs.cdelt, w.wcs.crpix = [-1e-4, 1e-4], [crpix1, 50]
    return w

d = tempfile.mkdtemp()
star, hot = with_sky_anchors([Circle(x=20.0, y=20.0, radius=3.0, text="star"),
                              Circle(x=70.0, y=70.0, radius=3.0, text="hot pixel")], wcs=image(50))
hot.frame = "image"                       # fixed to the detector: keep its pixels
RegionList(regions=[star, hot]).save(os.path.join(d, "a.yml"))
dither = image(55)                        # the next frame, 5 px away
loaded, _ = load_regions(os.path.join(d, "a.yml"), wcs=dither)     # no one to ask
print([(r.text, r.frame, round(r.x, 1), round(r.y, 1)) for r in loaded])
save_regions(os.path.join(d, "b.yml"), loaded.regions, wcs=dither)
print([(r["text"], r["frame"]) for r in yaml.safe_load(open(os.path.join(d, "b.yml")))["regions"]])
```
```
[('star', 'sky', 25.0, 20.0), ('hot pixel', 'image', 75.0, 70.0)]
[('star', 'sky'), ('hot pixel', 'sky')]
```

### Root cause
`_chosen_frame` (`regions_io.py:165-182`) returns one answer for the whole file. With no hook, the answer is `"sky"` if *any* anchored region says sky (`:174`), and `placed_on_image` applies it to every candidate (`:156-157`). On save, `with_sky_anchors` (`:297-298`) sets `frame="sky"` on every region it anchors unless the whole save was asked for `frame="image"`. The window never asks for that. Because the model's default frame is also `"image"` (`regions_model.py:207`), an explicit `frame: image` read from a file cannot be told apart from a region nobody has chosen a frame for.

### Fix (proposed)
- Make the model's default frame `None`, meaning "not chosen"; `with_sky_anchors` turns it into `"sky"` when it records an anchor.
- Keep an explicit `"image"` through `with_sky_anchors`.
- In `placed_on_image`, with no hook, place each candidate by its own `frame`, and ask the question only about `frame: sky` regions.

### Regression test (proposed)
`tests/test_region_ui.py`:
- `test_a_frame_image_region_keeps_its_pixels_in_a_mixed_file`: on the dithered WCS, the hot pixel stays at (70, 70) and the star moves.
- `test_resaving_keeps_frame_image`: the re-saved file still says `frame: image` for the hot pixel.

## R41. An anchor that cannot be recomputed is saved again, so moving a region on an image without a WCS is undone on the next load with one

- **Status:** open
- **Severity:** medium — the user's edit is silently reverted, and the report blames the pointing
- **Files:** `pyql3/core/regions_io.py:274-299`, `pyql3/core/regions_io.py:128-162`

### Symptom
1. Load a region file that was saved with a WCS onto an image that has none, such as a raw frame or a plane with no sky axes. The circle is placed at its stored (20, 20), with a note.
2. Drag it to the right source at (60, 45) and save. The file has `x: 60.0, y: 45.0`, plus the old `sky:` block and `frame: sky`.
3. Load it on the reduced image. The circle goes back to (20.0, 20.0), and the note reads: "moving up to 47.2 pixel(s): this file was drawn on an image pointed differently from this one". Through the menu, the question defaults to Sky.

### Reproduction
```python
import os, tempfile
from astropy.wcs import WCS
from pyql3.core.regions_model import Circle
from pyql3.core.regions_io import save_regions, load_regions

w = WCS(naxis=2)
w.wcs.ctype, w.wcs.crval = ['RA---TAN', 'DEC--TAN'], [266.4, 34.0]
w.wcs.cdelt, w.wcs.crpix = [-1e-4, 1e-4], [50, 50]
d = tempfile.mkdtemp()
save_regions(os.path.join(d, "a.yml"), [Circle(x=20.0, y=20.0, radius=3.0)], wcs=w)

raw, _ = load_regions(os.path.join(d, "a.yml"), wcs=None)     # an image with no WCS
raw.regions[0].x, raw.regions[0].y = 60.0, 45.0                # the user moves it
save_regions(os.path.join(d, "b.yml"), raw.regions, wcs=None)

back, report = load_regions(os.path.join(d, "b.yml"), wcs=w)  # the reduced image again
print("saved at (60, 45), loaded at (%.1f, %.1f)" % (back.regions[0].x, back.regions[0].y))
print(report.notes)
```
```
saved at (60, 45), loaded at (20.0, 20.0)
['1 region(s) were placed from their sky positions rather than their stored pixels, moving up to 47.2 pixel(s): this file was drawn on an image pointed differently from this one']
```

### Root cause
When the mapping is unusable, `with_sky_anchors` returns the regions unchanged (`regions_io.py:287-289`), so each keeps the anchor and `frame: sky` it was loaded with, whatever its pixels are now. Nothing clears `region.sky` when a region is dragged or edited. On the next load with a WCS, `placed_on_image` finds the pixels and the anchor disagreeing, and uses the anchor (`:128-162`).

AGENTS.md records exactly this staleness as the reason anchors are recomputed on save. `tests/test_region_ui.py::test_a_sky_anchor_is_kept_when_this_image_cannot_make_a_new_one` pins the keeping, which is right only for a region that has not moved.

### Fix (proposed)
When a region's geometry changes (drag, properties dialog, Region List edit), mark its anchor as stale. One way is to keep the geometry the anchor was paired with and compare it on save. In `with_sky_anchors`, keep the stored anchor only when it is not stale; otherwise drop it, or write `frame: image` so the moved pixels win. The existing test still holds for an unmoved region.

### Regression test (proposed)
`tests/test_region_ui.py::test_a_region_moved_on_an_image_without_a_wcs_keeps_its_new_pixels`: the steps above; assert the final load places the circle at (60, 45).

## R42. The Header Editor writes rows back by keyword name, so duplicate keywords overwrite each other, undefined values become 'None', and Cancel keeps edits

- **Status:** open
- **Severity:** medium — saving with no edits changes the file's header, and Cancel does not undo edits after an extension switch
- **Files:** `pyql3/gui/dialogs/header_editor.py:61-68`, `:76-91`, `:104-143`, `pyql3/core/fits_reader.py:195-207`
- **Related:** M10 (every row written back, values coerced by string parsing). This is a separate mechanism: rows are addressed by keyword instead of by card. S4 covers the structural keywords.

### Symptom
For a header with two `FILTER` cards ('Kn5', 'Hbb'), an undefined-value card and a HISTORY card,
opening the editor and applying with no changes leaves:
- both `FILTER` cards set to 'Hbb', so 'Kn5' is lost;
- `UNDEFKEY` set to the string `'None'`;
- a HISTORY row the user edited unchanged, with no message.

Editing `OBJECT`, switching the editor's Extension combo, and pressing Cancel leaves `OBJECT`
changed in memory. The next save of any kind writes it.

### Reproduction
```python
import numpy as np, os, tempfile
from PySide6.QtWidgets import QApplication
app = QApplication([])
from astropy.io import fits
from pyql3.core.fits_reader import FitsReader
from pyql3.gui.dialogs.header_editor import HeaderEditorDialog
h = fits.Header([('OBJECT', 'GC'), ('FILTER', 'Kn5', 'first card'), ('FILTER', 'Hbb', 'second card'),
                 ('UNDEFKEY', None, 'no value')])
h.add_history('reduced with DRP v4')
path = os.path.join(tempfile.mkdtemp(), "h.fits")
fits.HDUList([fits.PrimaryHDU(np.zeros((3, 3), np.float32), header=h), fits.ImageHDU(np.zeros((2, 2)))]).writeto(path)
r = FitsReader(path); dlg = HeaderEditorDialog(r)
def edit(keyword, text):
    for row in range(dlg.table.rowCount()):
        if dlg.table.item(row, 0).text() == keyword:
            dlg.table.item(row, 1).setText(text); return
edit('HISTORY', 'EDITED'); edit('OBJECT', 'typo')
dlg.combo_ext.setCurrentIndex(1)          # look at extension 1 ...
dlg.reject()                              # ... then Cancel
print("FILTER:", [c.value for c in r.header.cards if c.keyword == 'FILTER'])
print("UNDEFKEY:", repr(r.header['UNDEFKEY']), "| HISTORY:", list(r.header['HISTORY']),
      "| OBJECT after Cancel:", r.header['OBJECT'])
```
```
FILTER: ['Hbb', 'Hbb']
UNDEFKEY: 'None' | HISTORY: ['reduced with DRP v4'] | OBJECT after Cancel: typo
```

### Root cause
- `populate_table` fills one row per card (`header_editor.py:78-91`), but reads each comment with `header.comments[keyword]`, which always returns the first duplicate's comment.
- `apply_table_edits` writes each row back with `update_header_card(keyword, ...)` → `hdr[keyword] = (value, comment)` (`fits_reader.py:203-206`), which always addresses the first card with that name. The last duplicate row therefore wins, and later cards are never written.
- `str(value)` of astropy's `Undefined` is `'None'`, and the type guess at `:120-129` keeps it as a string.
- COMMENT, HISTORY and blank rows are editable cells but are skipped at `:134-135`.
- `on_extension_changed` calls `apply_table_edits` before switching (`:63`), and Cancel is a plain `reject()`, so nothing restores the header.

### Fix (proposed)
Remember each row's card index when populating the table. Write back by index (`header[i] = ...`),
and only for rows whose text differs from what was loaded. Keep `Undefined` when the cell is
unchanged. Make COMMENT/HISTORY rows read-only, or write them back by index too. Keep pending edits
per extension in the dialog, and apply them to the reader only on Save.

### Regression test (proposed)
`tests/test_fits_reader.py::test_header_editor_round_trip_is_identity`: populate then apply with no
edits on a header with duplicate keywords, an undefined value, HISTORY and COMMENT cards. Assert
that `header.tostring()` is unchanged. A second test asserts that Cancel after an extension switch
leaves the header unchanged.

---

## R43. Restarting a directory watch loses files that were seen but not yet announced

- **Status:** open
- **Severity:** medium — a frame that finished arriving just before the restart is never displayed, and nothing reports it
- **Files:** `pyql3/services/poller.py:160-165`, `pyql3/services/poller.py:172-197`, `pyql3/services/poller.py:199-215`, `pyql3/gui/dialogs/polling.py:121-125`
- **Related:** S0 (the polling rewrite that introduced settle-and-hold).

### Symptom
A file must hold steady for two scans after it is first seen. Restarting the watch during that time loses it permanently. Two common actions restart the watch:

- **Changing Scan Interval.** Every spin-box step and every keystroke restarts the observer, through `valueChanged` → `poller.interval`.
- **Another window taking over the watch.**

Measured at a 0.5 s interval, with the file seen by one scan and then the restart, waiting 4 s:
- no restart: `['frame_0001.fits']`
- interval changed: `[]`
- watch taken over: `[]`

A settled file that is being held while a batch finishes arriving (`_held_path`) is lost the same way.

**Related, found 2026-10-21 in Phase 1.** R4's fix (plan Task 5) added `DirectoryPoller.polling_stopped`, which `stop_polling()` emits and `MainWindow` connects to `_cancel_auto_loads`. The `interval` setter restarts the watch through `start_polling(self.watch_path)`, which calls `stop_polling()`, so changing the scan interval also cancels a pending auto-load retry. `PollingDialog` changes the interval on every spin-box step. A file that was announced but failed to parse and is waiting for its retry is then never loaded: the restarted observer does not announce it again, for the reason given under Root cause.

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import os, time, tempfile, numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication
from pyql3.services.poller import DirectoryPoller
app = QApplication([])
def pump(seconds):
    end = time.time() + seconds
    while time.time() < end:
        app.processEvents(); time.sleep(0.01)
for restart in ("none", "interval", "takeover"):
    d = tempfile.mkdtemp(); a, b = DirectoryPoller(interval=0.5), DirectoryPoller(interval=0.5)
    seen = []
    for p in (a, b):
        p.file_detected.connect(lambda path: seen.append(os.path.basename(path)))
    a.start_polling(d); pump(0.8)
    fits.PrimaryHDU(np.ones((4, 4, 4), "f4")).writeto(d + "/frame_0001.fits")
    while not a._pending:                       # seen by a scan, not yet settled
        pump(0.02)
    if restart == "interval": a.interval = 0.6  # the Scan Interval spin box
    if restart == "takeover": b.start_polling(d)
    pump(4.0)
    print(restart, seen)                        # none ['frame_0001.fits'] / interval [] / takeover []
    a.stop_polling(); b.stop_polling()
```

### Root cause
The `interval` setter calls `start_polling(self.watch_path)` (`poller.py:163-165`). Like a takeover (`:185-187`), that goes through `stop_polling()`, which clears `_pending` and `_held_path` (`:207-211`). The new `PollingObserver` (`:193-195`) takes its first snapshot with the file already present, so it emits no created or modified event for a file that has stopped changing. Nothing offers the file again.

### Fix (proposed)
When `start_polling` restarts the same directory, carry `_pending`, `_held_path`, `_held_count` and `_hold_ticks` across. On a takeover, pass them to the new owner. In `PollingDialog`, apply the interval on `editingFinished` rather than on every `valueChanged` step.

### Regression test (proposed)
`tests/test_poller.py::test_restarting_a_watch_keeps_unsettled_files`: add a candidate, change `interval` (and, separately, take the watch over), then tick `_check_pending` `SETTLE_CHECKS + 1` times. Assert that the file is announced exactly once.

`tests/test_poller.py::test_changing_the_interval_keeps_a_pending_retry`: in a polling `MainWindow`, announce a file that fails to parse so a retry is queued, change `poller.interval`, make the file readable, and let the retry run. Assert that the file is loaded.

---

## R44. A shape's caption stays where it was when the shape is dragged

- **Status:** open
- **Severity:** medium — every drag of a labelled circle, box or arrow leaves its text behind
- **Files:** `pyql3/gui/viewers/region_layer.py:916-967`, `:745-770`
- **Related:** M13. The arrow head was made to follow a dragged endpoint (`_item_edited` re-places
  it). The caption, a separate item, was not.

### Symptom
Dragging a circle, box or arrow that has `text` moves the shape and updates the model. The caption
stays at its old position until something calls `refresh()`, such as a flip, a rotation or loading
a file. Measured on a 7 × 5 pixel drag: circle moved to (17, 14), caption still at (10.5, 12.5).
Box and arrow behave the same way. Text regions are not affected, because their label is the item
being dragged.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.core.regions_model import Arrow, Box, Circle
from pyql3.gui.viewers.image_viewer import ImageViewer

v = ImageViewer(); v.set_data(np.zeros((50, 30, 40), np.float32))
layer = v.region_layer
for region in (Circle(x=10.0, y=9.0, radius=3.0, text="src A"),
               Box(x=20.0, y=12.0, width=6.0, height=4.0, text="box B"),
               Arrow(x=5.0, y=5.0, length=8.0, angle=30.0, text="arrow C")):
    layer.add(region)
    label = layer.label_for(region)
    before = (label.pos().x(), label.pos().y())
    layer.item_for(region).translate([7.0, 5.0])          # what an ROI drag does
    print(f"{region.TYPE:6s} region now at ({region.x:.1f}, {region.y:.1f}); "
          f"caption {before} -> {(label.pos().x(), label.pos().y())}")
layer.refresh()                                            # e.g. a flip
print("after refresh(), last caption at", (label.pos().x(), label.pos().y()))
```
```
circle region now at (17.0, 14.0); caption (10.5, 12.5) -> (10.5, 12.5)
box    region now at (27.0, 17.0); caption (20.5, 15.5) -> (20.5, 15.5)
arrow  region now at (12.0, 10.0); caption (5.5, 5.5) -> (5.5, 5.5)
after refresh(), last caption at (12.5, 10.5)
```

### Root cause
A caption is its own `TextItem`, parented to the ImageItem rather than to the ROI, and only
`_place()` positions it, through `_place_label()` (`:745-770`). `_item_edited()` (`:916-967`) reads
the new geometry back into the model. For an arrow it also calls `_place_arrow_head()`
(`:953-958`). It never calls `_place_label()`.

### Fix (proposed)
At the end of `_item_edited()`, before `regions_changed.emit()`, call
`self._place_label(entry, self._to_item(region.x, region.y))` with `_placing` held, as is already
done for the arrow head.

### Regression test (proposed)
`tests/test_region_layer.py::test_a_caption_follows_its_dragged_shape`, parametrized over circle,
box and arrow: `translate` the ROI, then assert `label_for(region).pos()` equals the position
`_place_label` gives for the new model coordinates.

## R45. View rotation is ignored by the Region List's Zoom To and by region label culling

- **Status:** open
- **Severity:** medium — with a North Up rotation, which Keck data usually needs, Zoom To moves the view away from the region and labels on screen go undrawn
- **Files:** `pyql3/gui/tools/region_list.py:266-282`, `pyql3/gui/viewers/region_layer.py:578-594`, `:637-650`, `pyql3/gui/viewers/image_viewer.py:1380-1400`
- **Related:** B20 (view rotation composed with the array transforms). Here the rotation is left
  out completely, in three places that compare ImageItem coordinates with view coordinates.

### Symptom
View rotation is a `QTransform` on the ImageItem, so ImageItem coordinates and view coordinates
differ whenever it is non-zero. Three distinct triggers:

1. **Zoom To** (Region List). With a 90° view rotation, the view was centred on (39.5, 29.5) while
   the region was drawn at (9.5, 49.5) in view coordinates, so it was not in the view at all.
2. **Aggregate overlay culling.** 1,200 labelled regions on a 40×30 plane, with the view on a
   quarter of the rotated image. At 90°, 675 regions were on screen and 575 were labelled, and 345
   labels were built for regions off screen. At 30°: 577 on screen, 551 labelled, 361 built off
   screen. At 0° every on-screen region was labelled.
3. **Changing the rotation does not re-cull** (per-region mode, which itself maps correctly). After
   applying a 90° rotation, 72 labels were on screen and 24 were visible, until the next pan.

### Reproduction
```python
import time, numpy as np
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.core.regions_model import Circle
from pyql3.gui.tools.region_list import RegionListDialog
from pyql3.gui.viewers.image_viewer import ImageViewer

def settle():
    end = time.time() + 0.4
    while time.time() < end:
        app.processEvents()

v = ImageViewer(); v.set_data(np.zeros((50, 30, 40), np.float32))
v.resize(800, 700); v.show(); app.processEvents()
layer, view, img = v.region_layer, v.imv.getView(), v.imv.getImageItem()
regions = [layer.add(Circle(x=1.0 + 2 * (i % 20), y=1.0 + 2 * (i // 20), radius=0.5, text=f"s{i}"))
           for i in range(300)]
view.setRange(xRange=(12, 28), yRange=(8, 22), padding=0); settle()
v.apply_view_rotation(90.0); settle()                       # Rotate Image..., or North Up
rect = view.viewRect()
on = [r for r in regions if rect.contains(img.mapToView(layer.label_for(r).pos()))]
print("labels on screen", len(on), "| of those visible", sum(layer.label_for(r).isVisible() for r in on))

RegionListDialog(None, v).zoom_to(regions[299])             # Region List > Zoom To
drawn = img.mapToView(QPointF(*layer._to_item(regions[299].x, regions[299].y)))
print("Zoom To: view centre", view.viewRect().center().toTuple(), "| region drawn at", drawn.toTuple())
```
```
labels on screen 72 | of those visible 24
Zoom To: view centre (39.5, 29.5) | region drawn at (9.5, 49.5)
```

### Root cause
`zoom_to()` (`region_list.py:274-282`) passes `layer._to_item(...)`, an ImageItem position, straight
to `ViewBox.setRange`, which takes view coordinates. `_draw_bulk_labels()` (`region_layer.py:585`)
tests `rect.contains(QPointF(*place))` with the same mix. The per-region path does map correctly,
through `_label_view_position()` (`:637-650`). `apply_view_rotation()` (`image_viewer.py:1380-1400`)
sets the transform and emits nothing, and does not change the view range, so neither
`display_changed` nor `sigRangeChanged` reaches the layer to re-cull.

### Fix (proposed)
Add one `RegionLayer` helper that maps orig coordinates to view coordinates
(`image_item.mapToView` of `_to_item`), and use it in `zoom_to()` and `_draw_bulk_labels()`. Have
`apply_view_rotation()` emit a signal (for example `view_rotation_changed`) that the layer connects
to `update_channel_visibility`.

### Regression test (proposed)
In `tests/test_region_ui.py`, `test_zoom_to_lands_on_the_region_under_a_view_rotation` asserts the
view rect contains the region's mapped position. In `tests/test_region_layer.py`,
`test_labels_are_culled_in_view_coordinates_under_a_view_rotation` is parametrized over per-region
and aggregate mode. It asserts that every label whose mapped anchor lies inside the view rect is
drawn after `apply_view_rotation(90)` and a settle.

## R46. Drawing mode cannot be cancelled, and pan and zoom stay disabled until a drag completes

- **Status:** open
- **Severity:** medium — a shape tool armed by mistake leaves the image stuck until the user draws a region and deletes it
- **Files:** `pyql3/gui/viewers/region_layer.py:1030-1063`, `pyql3/gui/viewers/image_viewer.py:308-329`, `pyql3/gui/region_toolbar.py:131-140`

### Symptom
After **Region ➔ New Circle** (or Box, Arrow, or the toolbar buttons), the view's panning and wheel
zoom are disabled (`mouseEnabled [False, False]`). What the user can then try, and what happens:

- a single click on the image does nothing;
- there is no Escape handler;
- clicking the pressed toolbar button again re-arms the same tool, and it stays checked;
- the right-click menu has no cancel entry.

The only ways out are to drag out a region (and delete it if it was not wanted), or to start a tool
dialog's draw mode, which takes the drag away. The text tool does not have this problem: its click
always ends the mode.

### Reproduction
```python
import tempfile, numpy as np
import pyql3.services.config as config
config._shared_config = config.ConfigManager(tempfile.mktemp(suffix=".json"))  # keep ~/.pyql3 out
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.main_window import MainWindow

win = MainWindow(); win.image_viewer.set_data(np.zeros((50, 30, 40), np.float32))
win.show_region_toolbar(True)
view, button = win.image_viewer.imv.getView(), win.region_toolbar.shape_actions["circle"]
button.trigger()                                    # arm the circle tool
print("armed:", win.region_layer.draw_kind, "| pan/zoom enabled:", view.state["mouseEnabled"])
button.trigger()                                    # click the pressed button again
print("clicked again:", win.region_layer.draw_kind, "| button checked:", button.isChecked(),
      "| pan/zoom enabled:", view.state["mouseEnabled"])
```
```
armed: circle | pan/zoom enabled: [False, False]
clicked again: circle | button checked: True | pan/zoom enabled: [False, False]
```

### Root cause
`begin_draw()` (`region_layer.py:1030-1052`) claims the drag through `begin_exclusive_drag()`, which
calls `view.setMouseEnabled(x=False, y=False)` (`image_viewer.py:327`). `cancel_draw()`
(`region_layer.py:1054-1063`) is the only way back, and it is called only from the end of a
completed drag (`:1174`) and from the text tool's click (`:1139`). A click without movement never
reaches `mouseDragEvent`, so it does not count. No key binding or menu entry calls `cancel_draw()`.
The toolbar's shape actions are in an exclusive `QActionGroup` (`region_toolbar.py:131-132`), so a
second click cannot uncheck the pressed button.

### Fix (proposed)
Add an Escape shortcut on the window (or the viewer) that calls `region_layer.cancel_draw()`. Set
the toolbar group to `QActionGroup.ExclusionPolicy.ExclusiveOptional` and make `_start()` cancel
when the clicked kind is the one already armed. Optionally, treat a plain click with a shape tool
armed as `place_at`, as the text tool already does through `_on_scene_clicked`.

### Regression test (proposed)
`tests/test_region_toolbar.py::test_clicking_the_pressed_shape_again_disarms_it` asserts
`draw_kind is None` and `view.state["mouseEnabled"] == [True, True]` after a second `trigger()`.
`tests/test_region_ui.py::test_escape_cancels_drawing` covers the shortcut the same way.

## R47. The vector reader tracks the frame only from bare frame lines, so arrows after `fk5;circle(...)` or `fk5 # ...` are read as pixels, and `fk5; # vector(...)` is dropped

- **Status:** open
- **Severity:** medium — arrows are placed off the image or lost with nothing in the Report; mostly hand-written or third-party files
- **Files:** `pyql3/core/ds9_regions.py:578-609`, `pyql3/core/ds9_regions.py:63-77`, `pyql3/core/ds9_regions.py:655-661`, `pyql3/core/ds9_regions.py:460-475`
- **Related:** M21, which records `detector` vectors as refused rather than read as pixels; M20, whose `physical` rewrite already splits lines on `;`.

### Symptom
On an ICRS image, with a vector given at the sky position of pixel (40, 30):

| file contents | result | Report |
|---|---|---|
| `fk5;circle(...)` then `# vector(...)` | arrow at pixel (265.40, −1.25), off the image | empty |
| `fk5 # from ds9` then `# vector(...)` | the same | empty |
| `fk5; # vector(...)` | the arrow is gone | empty |
| `detector` or `linear` then `# vector(41,31,10,45)` | arrow placed as image pixels (40, 30) | — |

Separately, image-frame vectors on an OSIRIS cube shown on axes (2, 1) do not get the "may not line up" note that image-frame circles get.

### Reproduction
```python
from astropy.wcs import WCS
from pyql3.core.ds9_regions import from_ds9
from pyql3.core.sky import CelestialMap

w = WCS(naxis=2)
w.wcs.ctype, w.wcs.crval = ['RA---TAN', 'DEC--TAN'], [266.4, -0.25]
w.wcs.cdelt, w.wcs.crpix = [-1e-4, 1e-4], [50, 50]
ra, dec = CelestialMap(w).to_sky(40.0, 30.0)
H = "# Region file format: DS9 version 4.1\n"
cases = {
    "fk5; # vector": f"fk5; circle({ra},{dec},1\")\nfk5; # vector({ra},{dec},2\",45) vector=1\n",
    "after fk5;circle": f"fk5;circle({ra},{dec},1\")\n# vector({ra},{dec},2\",45) vector=1\n",
    "fk5 # comment": f"fk5 # from ds9\ncircle({ra},{dec},1\")\n# vector({ra},{dec},2\",45) vector=1\n",
    "detector": "detector\n# vector(41,31,10,45) vector=1\n",
}
for name, body in cases.items():
    regions, report = from_ds9(H + body, wcs=w)
    print(f"{name:17s}", [(type(r).__name__, round(r.x, 2), round(r.y, 2)) for r in regions],
          "| skipped:", report.skipped)
```
```
fk5; # vector     [('Circle', 40.06, 29.95)] | skipped: []
after fk5;circle  [('Circle', 40.06, 29.95), ('Arrow', 265.4, -1.25)] | skipped: []
fk5 # comment     [('Circle', 40.06, 29.95), ('Arrow', 265.4, -1.25)] | skipped: []
detector          [('Arrow', 40.0, 30.0)] | skipped: ['"detector" frame or shape is not supported by the regions package']
```
(The circles sit 0.06 px from (40, 30) because the file says `fk5` and the WCS is ICRS; that part is correct.)

### Root cause
- `_extract_vectors` (`ds9_regions.py:578-609`) updates its frame only when `_FRAME_RE` (`:75-77`) matches a whole line: one frame word and nothing else. `regions` and ds9 both apply a frame given inside `fk5;circle(...)`, or before a comment.
- `_VECTOR_RE` and `_VECTOR_LOOSE_RE` (`:63-72`) are anchored at the start of the line, so `fk5; # vector(...)` matches neither. It is passed on to `regions`, which reads `# vector` as a comment.
- `_FRAME_RE` has no `detector`, `amplifier` or `linear`, so the refusal branch at `:655-661` is never reached for them.
- Arrows are added after `pixel_frame_count` is computed (`:460-477`), so image-frame arrows never contribute to the note.

### Fix (proposed)
In `_extract_vectors`, split each line on `;` as `_normalize_pixel_frames` does, drop a trailing `#` comment that is not itself a `# vector(`/`# text(` statement, update the frame from any segment that is a frame word, and match vectors per segment. Add the missing frame names to `_FRAME_RE`, and count image-frame arrows toward the axis note.

### Regression test (proposed)
`tests/test_ds9_regions.py`:
- `test_a_vector_follows_a_frame_given_on_a_statement_line`, parametrised over the three forms above: the arrow lands within `SUB_PIXEL` of the circle.
- `test_a_detector_frame_arrow_is_refused`.

## R48. ds9 export drops `z_range`, `visible: false`, a rotated label's tag and label font sizes, and import drops exclusion, all with an empty Report

- **Status:** open
- **Severity:** low — the native file keeps everything, but the Report that exists to announce these losses says nothing
- **Files:** `pyql3/core/ds9_regions.py:248-289`, `pyql3/core/ds9_regions.py:317-337`, `pyql3/core/ds9_regions.py:340-384`, `pyql3/core/ds9_regions.py:846-873`
- **Related:** M20 (losses that bypassed the Report); TODO_regions.md:338-339 planned for exactly these to be reported.

### Symptom
Export, then read back:
- A circle with `z_range=(120, 180)` and an arrow with `z_range=(1, 2)` come back with `None`.
- A box with `visible=False` comes back visible, and is drawn in ds9.
- A rotated label with `tag="labels"` comes back with no tag.
- A circle label with `font_size=20` comes back as 12.

The export report is empty. On import, `-circle(41,31,5)`, a ds9 exclusion, becomes an ordinary circle; that report is empty too. `docs/tools.md` states: "Anything that cannot be carried across, in either direction, is listed in a summary".

### Reproduction
```python
from pyql3.core.regions_model import Arrow, Box, Circle, Text, RegionList
from pyql3.core.ds9_regions import to_ds9, from_ds9

regions = [Circle(x=10.0, y=10.0, radius=3.0, z_range=(120, 180), text="Brg", font_size=20),
           Box(x=20.0, y=20.0, width=4.0, height=2.0, visible=False),
           Text(x=30.0, y=30.0, text="rotated", angle=30.0, tag="labels"),
           Arrow(x=5.0, y=5.0, length=6.0, angle=10.0, z_range=(1, 2))]
text, report = to_ds9(RegionList(regions=regions))
print("export report:", report.skipped, report.notes)
back = {type(r).__name__: r for r in from_ds9(text)[0]}
print("z_range", back["Circle"].z_range, back["Arrow"].z_range, "| font_size", back["Circle"].font_size,
      "| visible", back["Box"].visible, "| tag", repr(back["Text"].tag))
regions, report = from_ds9("image\n-circle(41,31,5)\n")
print("excluded circle imported:", len(regions), "| report:", report.skipped, report.notes)
```
```
export report: [] []
z_range None None | font_size 12 | visible True | tag ''
excluded circle imported: 1 | report: [] []
```

### Root cause
- `_to_regions_shape` and `_visual_and_meta` (`ds9_regions.py:248-289`) map colour, width, dash, label and tag. They map font size only for text, and they record nothing about what they leave out.
- `_rotated_text_lines` (`:317-337`) writes no `tag=`, and `_arrow_lines` (`:340-384`) writes no `font=`.
- On import, `_attributes_from_regions` (`:846-873`) ignores `meta['include']`, which `regions` sets to 0 for a `-` region.

### Fix (proposed)
In `to_ds9`, add one `report.skipped` entry for each kind of attribute that is not written: channel ranges, hidden regions, label font sizes of shapes. Write `tag=` on rotated labels. On import, report an excluded region by index, or carry `include` into the model and write it back.

### Regression test (proposed)
`tests/test_ds9_regions.py`:
- `test_attributes_ds9_cannot_carry_are_reported`: one region per attribute; assert each appears in `report.skipped`.
- `test_an_excluded_region_is_reported`.

## R49. File → Exit (Cmd-Q) closes only the current window

- **Status:** open
- **Severity:** low — with several windows open, Quit does what Close Window does, and the application keeps running
- **Files:** `pyql3/gui/main_window.py:139-146`

### Symptom
Exit has `QuitRole`, so on macOS it becomes the application menu's Quit item with Cmd-Q. It is connected to the window's own `close`, exactly like Close Window. With two windows open, File → Exit leaves one window open (`count() == 1`, the second window still visible).

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import tempfile
from PySide6.QtWidgets import QApplication
import pyql3.services.config as cfg
app = QApplication([])
cfg._shared_config = cfg.ConfigManager(tempfile.mkdtemp() + "/config.json")
from pyql3.gui.main_window import MainWindow
from pyql3.gui.window_manager import get_window_manager
first = MainWindow(); first.show()
second = first.new_window()                               # File -> New Window
exit_action = next(a for a in first.file_menu.actions() if a.text() == "Exit")
exit_action.trigger()                                     # File -> Exit
app.processEvents()
print(exit_action.menuRole(), get_window_manager().count(), second.isVisible())
# -> MenuRole.QuitRole 1 True
```

### Root cause
`exit_action.triggered.connect(self.close)` (`main_window.py:146`) is the same connection as Close Window (`:141`). It was correct while the application had one window.

### Fix (proposed)
Connect Exit to `QApplication.closeAllWindows()`. That runs every window's `closeEvent`, so pollers, tools and readers are still released, and the application then quits on the last window closed.

### Regression test (proposed)
`tests/test_multi_window.py::test_exit_closes_every_window`: two windows, trigger Exit, assert `manager.count() == 0`.

---

## R50. With *Show All* off, catalog labels come back on the next pan

- **Status:** open
- **Severity:** low — the overlay the user turned off reappears as text without markers
- **Files:** `pyql3/gui/tools/plot_catalog.py:925-929`, `pyql3/gui/tools/plot_catalog.py:955`,
  `pyql3/gui/tools/plot_catalog.py:1035-1041`, `pyql3/gui/tools/plot_catalog.py:1083-1089`

### Symptom
With *Labels* ticked, unticking *Show All* hides the markers and the labels. Panning or zooming
afterwards brings back every label in view, with no markers: 10 markers and 10 labels before, 0 and
0 after unticking, 0 markers and 10 labels after one pan.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.table import Table
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
viewer = ImageViewer(); viewer.show()
viewer.set_data(np.zeros((40, 60), np.float32))
dlg = PlotCatalogDialog(None, viewer)
dlg.set_catalog_table(Table({"name": [f"s{i}" for i in range(10)],
                             "x": [3.0 + 5 * i for i in range(10)], "y": [3.0 + 3 * i for i in range(10)]}), "t")
dlg.chk_show_name.setChecked(True)
shown = lambda: (len(dlg.scatter_item.getData()[0]), sum(t.isVisible() for t in dlg.text_items))
print("Show All on:      markers, labels =", shown())
dlg.chk_master_toggle.setChecked(False)
print("Show All off:     markers, labels =", shown())
dlg._on_view_range_changed()          # a pan frame
dlg.update_visible_text_labels()      # what the 200 ms timer then calls
print("after a pan:      markers, labels =", shown())
```
```
Show All on:      markers, labels = (10, 10)
Show All off:     markers, labels = (0, 0)
after a pan:      markers, labels = (0, 10)
```

### Root cause
When *Show All* is off, `update_plot` clears the markers and returns (`plot_catalog.py:925-929`)
before `self.all_label_points` is rebuilt (`:955`), so the previous label list survives. After a pan,
the timer calls `update_visible_text_labels` (`:1035-1041`), which checks only the *Labels* box
(`:1083-1089`) and draws that list again.

### Fix (proposed)
In `update_visible_text_labels`, hide the labels and return when `chk_master_toggle` is unchecked,
and clear `all_label_points` on `update_plot`'s early returns.

### Regression test (proposed)
`test_labels_stay_hidden_with_show_all_off_after_a_pan` in `tests/test_plot_catalog.py`, following
the snippet above and asserting that no `text_items` entry is visible after the redraw.

## R51. With Show Region Labels off, new and edited regions still draw their labels, and text regions disappear

- **Status:** open
- **Severity:** low — the toggle does not do what it says for new or edited regions, and text regions cannot be seen or picked up while it is off
- **Files:** `pyql3/gui/viewers/region_layer.py:601-618`, `:866`, `:1017`

### Symptom
With **Region ➔ Show Region Labels** unchecked:

- a newly drawn, spawned or loaded-by-`add` region with `text` shows its caption;
- pressing Apply in an existing region's properties dialog shows its caption again;
- every existing text region is hidden completely. A text region's label is the region and its only
  handle, so in per-region mode nothing is left to see or drag. In aggregate mode a "+" marker is
  still drawn, so the two modes disagree.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.core.regions_model import Circle, Text
from pyql3.gui.dialogs.region_properties import RegionPropertiesDialog
from pyql3.gui.viewers.image_viewer import ImageViewer

v = ImageViewer(); v.set_data(np.zeros((50, 30, 40), np.float32))
layer = v.region_layer
old = layer.add(Circle(x=10.0, y=10.0, radius=2.0, text="old"))
old_text = layer.add(Text(x=20.0, y=20.0, text="knot"))
layer.set_labels_visible(False)                     # Region > Show Region Labels, off
print("existing caption", layer.label_for(old).isVisible(),
      "| existing text region", layer.item_for(old_text).isVisible())

new = layer.add(Circle(x=20.0, y=12.0, radius=2.0, text="new"))
print("new region's caption", layer.label_for(new).isVisible())

dialog = RegionPropertiesDialog(old, layer); dialog.spin_width.setValue(4); dialog.apply()
print("after Apply on the old region, its caption", layer.label_for(old).isVisible())
```
```
existing caption False | existing text region False
new region's caption True
after Apply on the old region, its caption True
```

### Root cause
`_apply_visibility()` (`:601-618`) defaults to `show_labels=True`. Only `update_channel_visibility()`
(`:460-472`) computes the real value from the toggle and the density guard. `_build_items()` (`:866`)
and `_restyle_in_place()` (`:1017`) call `_apply_visibility()` without it. The same method treats
every `entry.label` as a caption (`:614-616`). For a `Text` entry the label is also `entry.handle`,
so the toggle hides the region itself.

### Fix (proposed)
Pass `show_labels=self._labels_visible and not self._label_guard.suppressing` (or a small helper
returning that) from `_build_items()` and `_restyle_in_place()`. In `_apply_visibility()`, exempt an
entry whose label is its handle (`entry.label is entry.handle`) from the label rule. Alternatively,
keep text regions hidden but draw a marker for them, as aggregate mode does, so they can still be
found.

### Regression test (proposed)
`tests/test_region_layer.py::test_new_and_restyled_regions_obey_the_labels_toggle`: with labels
off, `add` a captioned circle and `restyle` an existing one, and assert neither caption is visible.
A companion test asserts a text region stays visible, or keeps a marker, with labels off.

## R52. A 2-D image whose header carries a third WCS axis gets no sky readout, or a stale one, and a wrong compass

- **Status:** open
- **Severity:** low — needs a 2-D image with leftover cube WCS cards, but then both the readout and the compass are wrong
- **Files:** `pyql3/gui/viewers/image_viewer.py:1283-1294`, `pyql3/gui/viewers/image_viewer.py:1659`, `pyql3/gui/viewers/image_viewer.py:1674`, `pyql3/gui/viewers/image_viewer.py:1091-1105`

### Symptom
A collapsed cube written with the cube's header keeps `CTYPE3`, and astropy then builds a WCS with
`naxis == 3` for the 2-D image:

- In a fresh window the readout says "WCS: N/A" everywhere. The correct value at (10, 5) is
  17h45m36.02s -29d00m00.32s.
- If an OSIRIS cube was shown in the same window first, the readout shows a wavelength and a Dec
  taken from the wrong pixel axes: `em.wl: 2.01e-06 m | DEC: ...`.
- The compass puts East at -90° instead of 180°, so the E arrow points down.

### Reproduction
```python
import warnings, numpy as np
from PySide6.QtWidgets import QApplication
from PySide6.QtCore import QPointF
from astropy.io import fits
from astropy.wcs import WCS
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

w = WCS(naxis=3); w.wcs.ctype = ['RA---TAN', 'DEC--TAN', 'WAVE']; w.wcs.crval = [266.4, -29.0, 2.0e-6]
w.wcs.cdelt = [-1e-5, 1e-5, 1e-9]; w.wcs.crpix = [20, 15, 1]; w.wcs.cunit = ['deg', 'deg', 'm']
img = np.zeros((30, 40), np.float32)
header = fits.PrimaryHDU(img, header=w.to_header()).header    # a collapsed cube saved with the cube's header
print("NAXIS", header['NAXIS'], "| CTYPE3", header['CTYPE3'])
v = ImageViewer(); v.resize(800, 800); v.show()
with warnings.catch_warnings():
    warnings.simplefilter('ignore'); v.set_data(img, header)
app.processEvents()
print("wcs.naxis", v.wcs.naxis)
v.mouse_moved((v.imv.getImageItem().mapToScene(QPointF(10.5, 5.5)),))
print("readout at (10, 5):", v.lbl_wcs.text(), "| truth:", v.wcs.celestial.pixel_to_world(10, 5).to_string('hmsdms', precision=2))
n, e, _ = v.get_north_angle_base(); print(f"compass base: N {n:.1f}  E {e:.1f}")
v2 = ImageViewer(); v2.set_data(img, w.celestial.to_header())
n, e, _ = v2.get_north_angle_base(); print(f"same image, 2-axis WCS: N {n:.1f}  E {e:.1f}")
```
```
NAXIS 2 | CTYPE3 WAVE
wcs.naxis 3
readout at (10, 5): WCS: N/A | truth: 17h45m36.02s -29d00m00.32s
compass base: N 90.0  E -90.0
same image, 2-axis WCS: N 90.0  E -180.0
```

### Root cause
`mouse_moved` (`:1659`, `:1674`) and `get_north_angle_base` (`:1283`) choose between the 2-D and
3-D cases by `wcs.naxis`, not by the dimensionality of the data. With a 3-axis WCS on a 2-D image,
the readout takes the 3-D branch, which needs `wcs_z_idx`. The compass takes X from
`current_x_axis`, which defaults to AXIS 3, so it differentiates along the wavelength axis. The 2-D
branch of `set_data` (`:1091-1105`) does not reset `wcs_z_idx` or `current_x_axis`, so the values
left by a previous cube are used.

### Fix (proposed)
Branch on `raw_data.ndim` in both functions and take the pixel axes from `display_axis_indices()`,
which already returns `(0, 1)` for 2-D data. Pass 0 for any additional WCS axes. Reset `wcs_z_idx`
in the 2-D branch of `set_data`.

### Regression test (proposed)
`tests/test_image_viewer.py::test_2d_image_with_a_third_wcs_axis_reads_sky`: the header above,
asserting that the readout contains the RA of pixel (10, 5). Add a case to `tests/test_position_angle.py`
asserting that the compass base angles match those from `w.celestial.to_header()`.

---

## R53. With Log X ticked, the line list disappears and the cursor and top axis report log10 values as wavelengths

- **Status:** open
- **Severity:** low — the overlay silently empties and the readouts are wrong, but only in
  log mode
- **Files:** `pyql3/gui/tools/depth_plot.py:666-667`, `:1269-1295`, `:1339`, `:562-573`, `:93-101`

### Symptom
On a K-band cube:
- With linear X, the line list shows "18 line(s) visible".
- After ticking **Log X**, it shows "0 line(s) visible", with the view range at
  0.3214-0.3432.
- The cursor at 2.1493 µm reads "Wavelength: 0.3323 µm (Pixel: 0)".
- The top channel axis, whose ticks at 2.12/2.15/2.18 µm read 80/200/320 in linear mode,
  reads 0/0/0.

### Reproduction
```python
import numpy as np
from astropy.wcs import WCS
from PySide6.QtCore import QPointF
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

w = WCS(naxis=3); w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']; w.wcs.cunit = ['nm', 'deg', 'deg']
w.wcs.crval = [2100.0, 0, 0]; w.wcs.cdelt = [0.25, 1e-5, 1e-5]; w.wcs.crpix = [1, 1, 1]
v = ImageViewer(); v.set_data(np.random.default_rng(1).normal(10, 1, (30, 20, 400)), w.to_header())
d = DepthPlotDialog(image_viewer=v); d.show()
d.chk_enable_lines.setChecked(True); app.processEvents()
vb = d.plot_widget.getViewBox()
print("linear X:", d.lbl_line_info.text(), [round(t, 4) for t in vb.viewRange()[0]])
d.chk_log_x.setChecked(True); app.processEvents(); d.update_line_overlays()
(x0, x1), (y0, y1) = vb.viewRange()
print("log X:   ", d.lbl_line_info.text(), [round(x0, 4), round(x1, 4)])
d.mouse_moved((vb.mapViewToScene(QPointF((x0 + x1) / 2, (y0 + y1) / 2)),))
print(f"cursor at {10 ** ((x0 + x1) / 2):.4f} um reads:", d.lbl_cursor.text())
```
```
linear X: 18 line(s) visible (out of 805 total) [2.0958, 2.204]
log X:    0 line(s) visible (out of 805 total) [0.3214, 0.3432]
cursor at 2.1493 um reads: Wavelength: 0.3323 µm  (Pixel: 0)   Intensity: 281.4245
```

### Root cause
In log mode, pyqtgraph's view coordinates are `log10(x)`. `viewRange()`,
`mapSceneToView()` and the values passed to `AxisItem.tickStrings()` are all log10.
- `update_line_overlays()` compares them with line wavelengths in µm (`:1281-1283`) and
  places each `InfiniteLine` at the linear wavelength (`:1339`).
- `mouse_moved()` (`:566-571`) and `PixelIndexAxis.tickStrings()` (`:98`) interpolate the
  log10 value against linear wavelengths. `np.interp` then clamps to channel 0.

### Fix (proposed)
When `chk_log_x` is checked, convert view x with `10 ** x` before comparing or
interpolating, in `update_line_overlays()`, `mouse_moved()` and `PixelIndexAxis` (give the
axis a log flag). Place each line at `log10(wavelength)`.

### Regression test (proposed)
`tests/test_depth_plot.py::test_the_line_list_survives_log_x`: show the dialog, enable the
line list, tick Log X, and assert that the visible count is unchanged and that each line sits
at `log10` of its wavelength.

## R54. Line lists are looked up by file name, not path, and a file with no readable lines leaves the previous status on screen

- **Status:** open
- **Severity:** low — the wrong lines can be drawn under a correct-looking name, but only
  when two files share a name
- **Files:** `pyql3/gui/tools/depth_plot.py:1096-1097`, `:1153-1158`, `:1131-1151`, `:1250-1259`;
  `docs/tools.md:102-104`

### Symptom
- **Two files with the same name.** Browse to `A/lines.csv` and then `B/lines.csv`, and
  select the `A` entry. The combo and its tooltip show A's path, but B's line is loaded:
  `[(2.2, 'from_B')]`. `docs/tools.md:102-104` says the full path distinguishes such
  copies, so the code is what is wrong. A custom file with the same name as a bundled one
  replaces the bundled entry's path in the same way.
- **A file with no readable lines.** A whitespace-delimited list (`2.1661 Brgamma`) parses
  to 0 lines and nothing is drawn, but the status still reads the previous list's
  "1 line(s) visible (out of 1 total)".

### Reproduction
```python
import os, tempfile
import numpy as np
from astropy.wcs import WCS
from PySide6.QtWidgets import QApplication, QFileDialog
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

w = WCS(naxis=3); w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']; w.wcs.cunit = ['nm', 'deg', 'deg']
w.wcs.crval = [2100.0, 0, 0]; w.wcs.cdelt = [0.25, 1e-5, 1e-5]; w.wcs.crpix = [1, 1, 1]
v = ImageViewer(); v.set_data(np.ones((30, 20, 400)), w.to_header())
d = DepthPlotDialog(image_viewer=v); d.show(); d.chk_enable_lines.setChecked(True)
tmp = tempfile.mkdtemp()
def browse(name, text):       # stubs the modal file dialog
    path = os.path.join(tmp, name); os.makedirs(os.path.dirname(path), exist_ok=True)
    open(path, "w").write(text)
    QFileDialog.getOpenFileName = staticmethod(lambda *a, **k: (path, ""))
    d.browse_custom_linelist(); app.processEvents(); return path
a = browse("A/lines.csv", "2.1661,from_A\n"); browse("B/lines.csv", "2.2000,from_B\n")
d.combo_linelist.setCurrentIndex(0); d.combo_linelist.setCurrentIndex(d.combo_linelist.findData(a))
print("selected", os.path.relpath(d.combo_linelist.currentData(), tmp), "-> loaded", d.loaded_lines)
browse("ws_lines.txt", "2.1661 Brgamma\n2.1218 H2\n")
print("whitespace list: parsed", len(d.loaded_lines), "| drawn",
      sum(li.isVisible() for li, _ in d.line_items), "| status:", repr(d.lbl_line_info.text()))
```
```
selected A/lines.csv -> loaded [(2.2, 'from_B')]
whitespace list: parsed 0 | drawn 0 | status: '1 line(s) visible (out of 1 total)'
```

### Root cause
- `add_linelist_item()` keys `linelist_files` by basename (`depth_plot.py:1096-1097`), so
  the second file overwrites the first.
- `on_linelist_selection_changed()` looks the path up by the item's text (`:1154-1157`),
  although the item already carries its path as data.
- `parse_line_list()` splits on commas only and skips every other line silently
  (`:1141-1148`).
- `update_line_overlays()` clears the overlay for an empty list, but its message chain
  (`:1252-1258`) has no branch for that case, so the old text stays.

### Fix (proposed)
- Read the path from `combo_linelist.currentData()` in `on_linelist_selection_changed()`,
  and key `linelist_files` by path.
- Accept whitespace as a separator when a line has no comma.
- Set `lbl_line_info` to "No lines could be read from <name>" when `loaded_lines` is empty.

### Regression test (proposed)
- `tests/test_depth_plot.py::test_two_line_lists_with_one_name_load_their_own_files`: the
  sequence above; assert `[(2.1661, 'from_A')]`.
- `test_an_unreadable_line_list_says_so`: assert that the status names the file.

## R55. A radius edit leaves the hidden rectangle spins at the old box, so the first rectangle edit rebuilds the box from stale numbers

- **Status:** open
- **Severity:** low — the box jumps to a different size and place on the first edit, which
  is visible and can be undone
- **Files:** `pyql3/gui/tools/depth_plot.py:749-770`, `:669-684`, `:686-698`, `:1355-1416`

### Symptom
Set the Radius to 6, so the circle's bounding box becomes 12 × 12 at (9, 4). Then switch
Shape to Rectangle. The rectangle is 12 × 12 at (9, 4), but the X/Y Region spins still show
12-18 / 7-13. Nudging Y1 up by one turns the box into 6 × 7 at (12, 7).

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.depth_plot import DepthPlotDialog

v = ImageViewer(); v.set_data(np.ones((30, 20, 3)), None)
d = DepthPlotDialog(image_viewer=v)
d.set_center((15.0, 10.0))
d.spin_radius.setValue(6.0)
roi = lambda: (d.roi.pos().x(), d.roi.pos().y(), d.roi.size().x(), d.roi.size().y())
spins = lambda: (d.spin_x0.value(), d.spin_x1.value(), d.spin_y0.value(), d.spin_y1.value())
print("after radius 6: ROI", roi(), " rectangle spins", spins())
d.combo_shape.setCurrentText("Rectangle")
print("as a rectangle: ROI", roi(), " rectangle spins", spins())
d.spin_y1.setValue(d.spin_y1.value() + 1)   # the user moves the top edge up by one pixel
print("after Y1 + 1:   ROI", roi())
```
```
after radius 6: ROI (9.0, 4.0, 12.0, 12.0)  rectangle spins (12, 18, 7, 13)
as a rectangle: ROI (9.0, 4.0, 12.0, 12.0)  rectangle spins (12, 18, 7, 13)
after Y1 + 1:   ROI (12.0, 7.0, 6.0, 7.0)
```

### Root cause
- The rectangle spins are written only in `on_roi_changed()` (`depth_plot.py:693-698`).
- `on_radius_changed()` resizes the ROI with its signals blocked (`:760-763`) and calls
  `update_plot()` directly, so `on_roi_changed()` does not run.
- `toggle_roi_shape()` rebuilds the ROI without calling it either (`:1363-1373`).
- `on_spin_changed()` (`:669-684`) then rebuilds the box from all four spins, three of
  which are stale.
- The Center X/Y path is not affected: `set_center()` calls `on_roi_changed()`.

### Fix (proposed)
Move the four spin writes out of `on_roi_changed()` into a `_sync_rect_spins()` and call it
from `on_roi_changed()`, `on_radius_changed()` and `toggle_roi_shape()`.

### Regression test (proposed)
`tests/test_depth_plot.py::test_the_rectangle_spins_follow_a_radius_edit`: after
`spin_radius.setValue(6)`, assert that the four spins equal the ROI's edges.

## R56. The ROTPOSN fallback compass uses 90 + PA where the WCS and QL2 both give 90 - PA

- **Status:** open
- **Severity:** low — reached only when the header has no celestial WCS, but then North and East point the wrong way
- **Files:** `pyql3/gui/viewers/image_viewer.py:1329-1347`
- **Related:** B20, which fixed the order in which the display transforms are applied to the compass. This entry concerns the base angle that goes into that composition, in the fallback path.

### Symptom
With the WCS and rotator cards of `example/s180722_a005002_Kn3_035.fits` (`PA_SPEC = 285`), the
file's own WCS puts North at 165° and East at 255° in the default OSIRIS display. The
`ROTPOSN`/`INSTANGL` fallback puts them at 15° and 105°. QL2's `osiris_calc_pa` with its
`arrowpa = -pa` drawing step gives 165° and 255°, the same as the WCS.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.io import fits
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

# The WCS and rotator cards of example/s180722_a005002_Kn3_035.fits (PA_SPEC = 285)
h = fits.Header({'NAXIS': 3, 'CTYPE1': 'WAVE', 'CTYPE2': 'DEC--TAN', 'CTYPE3': 'RA---TAN',
                 'CUNIT1': 'nm', 'CUNIT2': 'deg', 'CUNIT3': 'deg', 'CRVAL1': 2121.0, 'CDELT1': 0.25,
                 'CRVAL2': -29.0, 'CRVAL3': 266.4, 'CDELT2': 9.72222e-06, 'CDELT3': 9.72222e-06,
                 'PC2_2': 0.258819241497, 'PC2_3': -0.965925773665,
                 'PC3_2': -0.965925773665, 'PC3_3': -0.258819241497,
                 'ROTPOSN': 597.0, 'INSTANGL': 311.99999999, 'INSTR': 'spec', 'TELESCOP': 'Keck I'})
v = ImageViewer(); v.set_data(np.zeros((51, 66, 40), np.float32), h)
n, e, _ = v.get_north_angle_base(); print(f"from the WCS        : N {n % 360:.1f}  E {e % 360:.1f}")
v.wcs = None   # the fallback is what runs when the header has no celestial WCS
n, e, _ = v.get_north_angle_base(); print(f"ROTPOSN fallback    : N {n % 360:.1f}  E {e % 360:.1f}")
pa = 597.0 - 311.99999999
print(f"QL2 osiris_calc_pa : N {(90 - pa) % 360:.1f}  E {(180 - pa) % 360:.1f}")
```
```
from the WCS        : N 165.0  E 255.0
ROTPOSN fallback    : N 15.0  E 105.0
QL2 osiris_calc_pa : N 165.0  E 255.0
```

### Root cause
`get_north_angle_base` sets `theta_n_base = 90.0 + north_pa` (`:1345`) and `theta_e_base =
theta_n_base + 90.0`. That gives an East-counter-clockwise-of-North parity, but rotates North the
opposite way from the PA. QL2 draws North at `-pa + 90` and East at `-(pa - 90) + 90`.

### Fix (proposed)
Set `theta_n_base = 90.0 - north_pa` and keep `theta_e_base = theta_n_base + 90.0`. That agrees with
the WCS on the example cube and with QL2 for both the `spec` and `imag` branches.

### Regression test (proposed)
`tests/test_position_angle.py::test_rotposn_fallback_matches_the_wcs`: the header above, asserting
that the fallback angles (with `wcs` set to `None`) equal the WCS angles to within 0.1° for
`ROTPOSN` values giving PA 0, 45, 285 and 300.

---

## R57. Display → Scaling (the menu) hides the timeline and discards the manual levels in Collapse or Boxcar mode

- **Status:** ✅ FIXED — `pyql3/gui/main_window.py` (`set_scaling` calls `ImageViewer.apply_contrast()`, the Scale combo box's path), covered by `test_scaling_menu_keeps_the_collapse_display` (`tests/test_menu_actions.py`)
- **Severity:** low — no wrong numbers, but the timeline strip with the collapse range disappears and the user's Min/Max is replaced
- **Files:** `pyql3/gui/main_window.py:756-763`, `pyql3/gui/viewers/image_viewer.py:864-876`
- **Related:** R5. This is another redisplay path that does not take the z mode into account.

### Symptom
With a collapse displayed and manual levels Min 25 / Max 35, choosing **Display → Scaling → Sqrt**
hides the timeline strip (with the yellow range and the wavelength axis) and resets Min/Max to the
automatic 30.000 / 31.000. The timeline stays hidden after later changes through the Scale combo
box. Choosing the same scale with the Scale combo box keeps both the timeline and the levels.

### Reproduction
```python
import os, tempfile, numpy as np
from PySide6.QtWidgets import QApplication
from astropy.wcs import WCS
app = QApplication([])
import pyql3.services.config as config_module                  # keep ~/.pyql3/config.json untouched
config_module._shared_config = config_module.ConfigManager(os.path.join(tempfile.mkdtemp(), "config.json"))
from pyql3.gui.main_window import MainWindow

w = WCS(naxis=3); w.wcs.ctype = ['WAVE', 'DEC--TAN', 'RA---TAN']
w.wcs.crval = [2.2, 34.0, 242.0]; w.wcs.cdelt = [0.0005, 1e-4, 1e-4]; w.wcs.cunit = ['um', 'deg', 'deg']
cube = np.zeros((16, 12, 20), np.float32); cube[:] = np.arange(20)
win = MainWindow(); win.resize(900, 900); win.show(); v = win.image_viewer
v.set_data(cube, w.to_header())
v.combo_collapse.setCurrentText("Sum"); v.txt_zmin.setText("4"); v.txt_zmax.setText("8"); v.radio_range.setChecked(True)
v.txt_min.setText("25"); v.txt_max.setText("35"); v.apply_contrast(); app.processEvents()
print("collapsed, manual levels  : timeline", v.imv.ui.roiPlot.isVisible(), "| Min/Max", v.txt_min.text(), v.txt_max.text())
win.set_scaling("Sqrt"); app.processEvents()                    # Display > Scaling > Sqrt
print("after Display>Scaling>Sqrt: timeline", v.imv.ui.roiPlot.isVisible(), "| Min/Max", v.txt_min.text(), v.txt_max.text())
v.combo_scale.setCurrentText("Linear"); app.processEvents()     # the Scale combo box
print("after Scale combo Linear  : timeline", v.imv.ui.roiPlot.isVisible())
win.close()
```
```
collapsed, manual levels  : timeline True | Min/Max 25 35
after Display>Scaling>Sqrt: timeline False | Min/Max 30.000 31.000
after Scale combo Linear  : timeline False
```

### Root cause
`MainWindow.set_scaling` calls `update_image_display()` with its defaults (`main_window.py:763`).
That passes the 2-D collapsed plane to `imv.setImage`, so pyqtgraph decides there is no time axis
and hides the timeline strip, and it recomputes automatic levels. The Scale combo box instead goes
through `ImageViewer.apply_contrast` (`image_viewer.py:864-876`), which passes `bypass_imv` and
`use_manual_levels`.

### Fix (proposed)
Have `set_scaling` call `self.image_viewer.apply_contrast()` after setting the combo box, so the
menu and the combo box share one path.

### Regression test (proposed)
`tests/test_menu_actions.py::test_scaling_menu_keeps_the_collapse_display`: collapse a cube, set
manual levels, call `set_scaling("Sqrt")`, and assert that `imv.ui.roiPlot.isVisible()` is still
true and that `txt_min` / `txt_max` are unchanged.

---

## R58. Recent Files is stale in every other window, and two running copies overwrite each other's list

- **Status:** open
- **Severity:** low — files opened elsewhere are missing from the menu, and recent entries are silently lost between processes
- **Files:** `pyql3/gui/main_window.py:639-640`, `pyql3/gui/main_window.py:652-678`, `pyql3/services/config.py:17-20`, `pyql3/services/config.py:83-101`, `pyql3/services/cli_install.py:124`
- **Related:** `get_config()` was introduced to fix the in-process version of the lost update. This is the cross-process version.

### Symptom
- **Within one process:** opening `x.fits` in window A rebuilds A's Recent Files menu (`1. x.fits`) but not B's, which still says `No Recent Files` until B loads something itself.
- **Across processes:** the `quicklook3` launcher is `exec ... "$@"` (`cli_install.py:124`), so each run while the application is open is a second process with its own `ConfigManager`, read once at startup. When the first process adds a recent file and the second later saves any setting, the second process's older snapshot replaces the file. Measured: `recent_files` on disk was `None` after process 1 added `x.fits` and process 2 set `polling_interval`.
- The `window_manager.py` docstring says `quicklook3 cube.fits` while running goes to the most recently used window. It does not, for the same reason.

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import json, tempfile, numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication
import pyql3.services.config as cfg
app = QApplication([]); d = tempfile.mkdtemp()
cfg._shared_config = cfg.ConfigManager(d + "/config.json")
from pyql3.gui.main_window import MainWindow
fits.PrimaryHDU(np.ones((4, 4), "f4")).writeto(d + "/x.fits")
a, b = MainWindow(), MainWindow()
a.load_fits(d + "/x.fits")                                # File -> Open in window A
print("A:", [act.text() for act in a.recent_menu.actions()][:1])   # -> ['1. x.fits']
print("B:", [act.text() for act in b.recent_menu.actions()][:1])   # -> ['No Recent Files']
# Two processes, as two `quicklook3` runs are: each read the file once, at startup
first, second = cfg.ConfigManager(d + "/shared.json"), cfg.ConfigManager(d + "/shared.json")
first.add_recent_file(d + "/x.fits")
second.set("polling_interval", 5.0)                       # any later save from the other one
print(json.load(open(d + "/shared.json")).get("recent_files"))     # -> None
```

### Root cause
`load_fits` calls `update_recent_files_menu()` only on its own window (`main_window.py:639-640`), and the menu is otherwise built once at construction (`:121-122`). `ConfigManager` reads the file only in `__init__` (`config.py:17-20`). `set()` (`:83-85`) and `add_recent_file()` (`:93-101`) modify that snapshot and write the whole dict back.

### Fix (proposed)
Rebuild the menu from `recent_menu.aboutToShow`, as the Window menu does. In `ConfigManager`, re-read the file before each change and save: reload in `set()` and `add_recent_file()`, apply the change to the fresh dict, then save. Correct the `window_manager.py` docstring.

### Regression test (proposed)
`tests/test_config.py::test_two_managers_do_not_drop_each_others_recent_files`: two `ConfigManager`s on one path. Assert the file added by one survives a `set()` from the other. `tests/test_multi_window.py::test_recent_files_menu_is_current_in_every_window`: assert B's menu lists a file opened in A once `aboutToShow` is emitted.

---

## R59. Files arriving from outside the window: Finder multi-select loads into one window, and `--poll-dir` misses compressed frames

- **Status:** open
- **Severity:** low — two of three selected cubes are read and discarded, and `--poll-dir` shows nothing for a directory of `.fits.gz` frames
- **Files:** `pyql3/gui/file_open.py:35-49`, `pyql3/gui/window_manager.py:121-133`, `main.py:84-91`, `main.py:142`
- **Related:** M3 (suffixes the poller did not accept) and M6 (a command-line path ignored without a message).

### Symptom
- **Finder multi-select.** Selecting three cubes and choosing Open With → QuickLook 3 sends one open-document event per file. All three go to the same window, at cold launch and while running. Each is fully read and added to Recent Files, but only the last is displayed (`1 QuickLook 3 - cube_2.fits`). The command line opens one window per file.
- **`--poll-dir` initial file.** The initial file is chosen with `glob('*.fits') + glob('*.fit')`, which is case-sensitive and ignores `.fts` and `.gz`. A directory holding only `s251021_0001.fits.gz`, `s251021_0002.FITS` and `s251021_0003.fts` starts with an empty window, although the poller accepts all three (`is_fits_path` is True for each). A mixed directory can open an older `.fits` over a newer `.fits.gz`.
- **Missing `--poll-dir`.** A directory that does not exist is ignored with no message on stderr and no dialog. Polling is simply off.

### Reproduction
Run from the repository root with `QT_QPA_PLATFORM=offscreen uv run python -`:
```python
import os, tempfile, numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QFileOpenEvent
import pyql3.services.config as cfg
app = QApplication([]); d = tempfile.mkdtemp()
cfg._shared_config = cfg.ConfigManager(d + "/config.json")
from pyql3.gui.file_open import FileOpenHandler
from pyql3.gui.main_window import MainWindow
from pyql3.gui.window_manager import get_window_manager
handler = FileOpenHandler(app); app.installEventFilter(handler)
for i in range(3):                                        # three cubes selected in Finder
    fits.PrimaryHDU(np.full((4, 4, 4), float(i), "f4")).writeto(f"{d}/cube_{i}.fits")
    QApplication.sendEvent(app, QFileOpenEvent(f"{d}/cube_{i}.fits"))
win = MainWindow()                                        # what main.py does next
handler.set_loader(get_window_manager().open_path)
print(get_window_manager().count(), win.windowTitle())    # -> 1 QuickLook 3 - cube_2.fits
```
For `--poll-dir`: put only a gzipped cube in a directory and run `uv run python main.py --poll-dir DIR`. The window opens empty, with polling on. Run it again with a misspelled directory; nothing is printed.

### Root cause
`FileOpenHandler.set_loader` and `open_path` (`file_open.py:35-49`) pass each path to `WindowManager.open_path` (`window_manager.py:121-133`). That always targets `most_recent()`, which is the same window for every event in the batch. `main.py:89` uses its own glob instead of `poller.is_fits_path()`, and `main.py:86` has no `else` branch.

### Fix (proposed)
In `WindowManager.open_path`, send a path to a new window when the target window already received one in the current event batch, for example within the same event-loop pass, tracked with a `QTimer.singleShot(0, ...)` reset. In `main.py`, pick the initial file as the newest entry of `os.listdir(poll_dir)` for which `is_fits_path()` is true, and print `pyql3: no directory at ...` to stderr when the path is not a directory.

### Regression test (proposed)
`tests/test_file_open.py::test_several_finder_files_open_in_separate_windows`: queue three paths, call `set_loader`, assert three windows each showing one cube. `tests/test_main_window_and_poller.py::test_poll_dir_opens_the_newest_compressed_frame`: run `main.main()` with `QApplication.exec` patched to return at once and `--poll-dir` on a `.fits.gz`-only directory. Assert that the window title names that file. (The existing check in `tests/test_region_ui.py` only inspects the `main.py` source text.)

---

## R60. Changing any catalog control recentres the view on the selected source

- **Status:** open
- **Severity:** low — the view jumps back to an earlier selection, undoing the user's pan
- **Files:** `pyql3/gui/tools/plot_catalog.py:1000`, `pyql3/gui/tools/plot_catalog.py:1204-1221`,
  `pyql3/gui/main_window.py:783-792`

### Symptom
Select a row (the view centres on it, at (5.5, 7.5)), pan elsewhere (centre (40.0, 25.0)), then
change the marker size: the view moves back to (5.5, 7.5). The same happens for the marker shape,
colour, *Labels*, the label column, *Show All*, either coordinate column, and a DN/s to Total DN
change, which calls `update_plot` on every open tool. `clear_selection` is documented as leaving the
view where it is because moving it unexpectedly is a surprise (`plot_catalog.py:1162-1167`); this
path does the move without being asked.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.table import Table
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
viewer = ImageViewer(); viewer.resize(600, 600); viewer.show()
viewer.set_data(np.zeros((40, 60), np.float32))
dlg = PlotCatalogDialog(None, viewer)
dlg.set_catalog_table(Table({"name": ["a", "b"], "x": [5.0, 50.0], "y": [7.0, 30.0]}), "t")
view = viewer.imv.getView()
centre = lambda: (view.viewRect().center().x(), view.viewRect().center().y())
view.setRange(xRange=(0, 20), yRange=(0, 20), padding=0)
dlg.table.selectRow(0)
print("selected row 0:     ", centre())
view.setRange(xRange=(30, 50), yRange=(15, 35), padding=0)
print("user pans away:     ", centre())
dlg.spin_size.setValue(14)
print("marker size changed:", centre())
```
```
selected row 0:      (5.5, 7.5)
user pans away:      (40.0, 25.0)
marker size changed: (5.5, 7.5)
```

### Root cause
`update_plot` ends by calling `on_table_selection` (`plot_catalog.py:1000`) to redraw the
highlight, and `on_table_selection` also recentres the view on the selected source
(`:1204-1221`). Redrawing the highlight and moving the view are one function, so every refresh does
both.

### Fix (proposed)
Split `on_table_selection` into a highlight redraw and a recentre. `update_plot` calls only the
redraw; the recentre runs from `selectionChanged` and from *Center on Source*.

### Regression test (proposed)
`test_a_style_change_does_not_move_the_view` in `tests/test_plot_catalog.py`, following the snippet
above and asserting that the view centre after `spin_size.setValue` equals the centre before it.

## R61. On a 2-D image whose first FITS axis is Dec, every RA/Dec source lands off the image

- **Status:** open
- **Severity:** low — affects only 2-D images with the celestial axes in Dec, RA order, but then no source is placed
- **Files:** `pyql3/gui/tools/plot_catalog.py:873-874`, `pyql3/gui/tools/plot_catalog.py:875-893`

### Symptom
A 2-D image with `CTYPE1 = 'DEC--TAN'` and `CTYPE2 = 'RA---TAN'` is the order left behind by an
OSIRIS cube (`WAVE`, `DEC`, `RA`) collapsed along wavelength. An RA/Dec catalog on such an image
reports "1 sources | 0 plotted | 1 out of bounds" for a star that is on the image. The computed
pixel is (-1102756.5, -70569.7) instead of (10, 25).

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
from astropy.table import Table
from astropy.wcs import WCS
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
w = WCS(naxis=2)                                     # FITS axis 1 is Dec, axis 2 is RA
w.wcs.ctype = ['DEC--TAN', 'RA---TAN']; w.wcs.crval = [-29.0, 266.4]
w.wcs.crpix = [30, 20]; w.wcs.cdelt = [0.0001, 0.0001]
viewer = ImageViewer(); viewer.set_data(np.zeros((40, 60), np.float32), w.to_header())
dec, ra = w.pixel_to_world_values(10, 25)            # a star at 0-based pixel (10, 25)
dlg = PlotCatalogDialog(None, viewer)
dlg.set_catalog_table(Table({"name": ["s"], "ra": [float(ra)], "dec": [float(dec)]}), "t")
print(dlg.combo_coord_type.currentText(), "|", dlg.lbl_status.text())
print("2-D branch computes", w.world_to_pixel_values(float(ra), float(dec)), "expected (10, 25)")
```
```
World (RA/DEC) | Loaded: 1 sources | 0 plotted | 1 out of bounds
2-D branch computes (array(-1102756.52321578), array(-70569.70848442)) expected (10, 25)
```

### Root cause
For `wcs.naxis == 2`, `_row_to_display` calls `wcs.world_to_pixel_values(val_x, val_y)` with RA first
and Dec second (`plot_catalog.py:873-874`), which is correct only when FITS axis 1 is RA. The 3-D
branch directly below (`:875-893`) identifies the axes from `world_axis_physical_types`, as AGENTS.md
requires ("Never hardcode an axis index").

### Fix (proposed)
Remove the 2-D special case and use the `world_axis_physical_types` branch for every `naxis`;
`display_axis_indices()` already returns `(0, 1)` for a 2-D image.

### Regression test (proposed)
`test_world_catalog_on_a_dec_first_2d_image` in `tests/test_plot_catalog.py`, built as in the
snippet, asserting that the marker is at (10.5, 25.5) to within 0.01.

## R62. A catalog with a `SkyCoord` column raises in the column guess and leaves the X/Y selectors dead

- **Status:** open
- **Severity:** low — photutils tables written by astropy are affected; an exception reaches the terminal and the tool stops responding to column choices
- **Files:** `pyql3/gui/tools/plot_catalog.py:761-769`, `pyql3/gui/tools/plot_catalog.py:778-808`,
  `pyql3/gui/tools/plot_catalog.py:565-596`

### Symptom
A photutils `SourceCatalog.to_table()` holds `sky_centroid` as a `SkyCoord`. Written to FITS or ECSV
by astropy, it reads back as a `SkyCoord` column, not as two numbers. Two failures follow:

- The table loads as *FITS Pixels* on `xcentroid`/`ycentroid`, but switching to *World (RA/DEC)*
  prints `AttributeError: 'SkyCoord' object has no attribute 'dtype'`. The type box then reads World,
  the markers stay at their pixel positions, and `combo_x.signalsBlocked()` is `True`, so choosing
  a different X or Y column does nothing until the type is switched back.
- A table whose only position is the `SkyCoord` column does not load. The file label reads "Error
  loading file: 'SkyCoord' object has no attribute 'dtype'", the table still shows its 2 rows, and the
  X/Y selectors are left signal-blocked.

### Reproduction
```python
import os, tempfile
import numpy as np
import astropy.units as u
from PySide6.QtWidgets import QApplication
from astropy.coordinates import SkyCoord
from astropy.table import QTable
from pyql3.gui.viewers.image_viewer import ImageViewer
from pyql3.gui.tools.plot_catalog import PlotCatalogDialog

app = QApplication([])
viewer = ImageViewer(); viewer.set_data(np.zeros((40, 60), np.float32))
path = os.path.join(tempfile.mkdtemp(), "phot.fits")   # the shape of a photutils SourceCatalog table
QTable({"label": [1, 2], "xcentroid": [10.0, 30.0], "ycentroid": [20.0, 15.0],
        "sky_centroid": SkyCoord([266.40, 266.41] * u.deg, [-29.00, -29.01] * u.deg),
        "segment_flux": [1.0, 2.0]}).write(path)
dlg = PlotCatalogDialog(None, viewer); dlg.load_catalog_file(path)
print("loaded:", dlg.combo_coord_type.currentText(), dlg.combo_x.currentText(), "|", dlg.lbl_status.text())
dlg.combo_coord_type.setCurrentIndex(2)              # World: the slot raises
print("World: combo_x signals blocked =", dlg.combo_x.signalsBlocked())
before = [list(a) for a in dlg.scatter_item.getData()]
dlg.combo_x.setCurrentText("segment_flux")
print("picking a column changes the markers:", [list(a) for a in dlg.scatter_item.getData()] != before)
```
```
AttributeError: 'SkyCoord' object has no attribute 'dtype'      (traceback from the slot)
loaded: FITS Pixels xcentroid | Loaded: 2 sources | 2 plotted | 0 out of bounds
World: combo_x signals blocked = True
picking a column changes the markers: False
```

### Root cause
When no RA/Dec column name matches, `update_columns_for_type` falls back to
`_numeric_column_indices` (`plot_catalog.py:793-799`), which reads `.dtype` on every column
(`:768-769`). Mixin columns such as `SkyCoord` have no `dtype`. The exception is raised after
`combo_x`/`combo_y.blockSignals(True)` (`:778-779`) and before the matching `blockSignals(False)`
(`:806-807`), with no `try`/`finally` between them. On a load, `load_catalog_file` catches the
exception and shows it in the file label (`:592-596`) after the table has already been filled.

### Fix (proposed)
Test `getattr(column, 'dtype', None)` in `_numeric_column_indices` so that mixin columns are skipped,
and wrap the blocked section of `update_columns_for_type` in `try`/`finally`.

### Regression test (proposed)
`test_a_skycoord_column_does_not_break_the_column_guess` in `tests/test_plot_catalog.py`, built as in
the snippet, asserting that switching to World raises nothing and that
`combo_x.signalsBlocked()` is `False` afterwards.

## R63. A region with a channel range is always hidden on a 2-D image

- **Status:** open
- **Severity:** low — regions from a cube's region file disappear on an image of the same field, with nothing on screen saying why
- **Files:** `pyql3/gui/viewers/region_layer.py:596-599`, `:679-683`, `pyql3/gui/viewers/image_viewer.py:422-430`

### Symptom
A region with `z_range: [120, 180]`, loaded from a file saved on a cube or kept when a 2-D image is
loaded into the same window, is not drawn on the 2-D image. A 2-D image has no channels, but the
layer tests the range against channel 0. The region is still in the Region List and is saved, which
makes its absence from the image harder to explain.

### Reproduction
```python
import numpy as np
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.core.regions_model import Circle
from pyql3.gui.viewers.image_viewer import ImageViewer

v = ImageViewer(); v.set_data(np.zeros((30, 40), np.float32))       # a 2-D image
layer = v.region_layer
region = layer.add(Circle(x=10.0, y=10.0, radius=3.0, z_range=(120, 180), text="Br gamma knot"))
print("channel", layer._current_channel(), "| visible", layer.item_for(region).isVisible())
```
```
channel 0 | visible False
```

### Root cause
`_current_channel()` (`region_layer.py:596-599`) returns `viewer.current_z()`, which returns 0 for
anything that is not a 3-D cube (`image_viewer.py:429-430`). `_in_channel_range()` (`:679-683`) then
compares 0 against the region's range and hides it. Aggregate mode applies the same test in
`_draw_bulk()`.

### Fix (proposed)
Have `_current_channel()` return None when `transposed_data` is not 3-D, and have
`_in_channel_range()` return True for a None channel, so a channel range only restricts a region
where channels exist.

### Regression test (proposed)
`tests/test_region_layer.py::test_a_channel_range_does_not_hide_a_region_on_a_2d_image`: on a 2-D
viewer, add a circle with `z_range=(120, 180)` and assert its item is visible, in per-region mode and
in aggregate mode (with `interactive_limit` lowered).

## R64. Hand-parsed vectors ignore ds9's `global` line, and an exported arrow of width 2 omits `width=` under a library `global` line that sets another width

- **Status:** open
- **Severity:** low — line colour and width only, but arrows and shapes from the same file are drawn differently
- **Files:** `pyql3/core/ds9_regions.py:730-754`, `pyql3/core/ds9_regions.py:358-359`

### Symptom
- **Import:** with `global color=red width=3`, a circle loads red at width 3, while the vector on the next line loads green at width 2. ds9 draws both red at width 3. ds9's own files rely on this, since it writes per-region properties only when they differ from the `global` line.
- **Export:** a width-3 circle beside a width-2 arrow produces `global width=3 color=green` and a vector line with no `width=`. ds9 therefore draws the arrow at width 3, while our reader says 2.

### Reproduction
```python
from pyql3.core.regions_model import Arrow, Circle, RegionList
from pyql3.core.ds9_regions import to_ds9, from_ds9

text = ('# Region file format: DS9 version 4.1\n'
        'global color=red width=3\nimage\ncircle(11,11,3)\n# vector(21,21,10,45) vector=1\n')
print([(type(r).__name__, r.color, r.line_width) for r in from_ds9(text)[0]])
out, _ = to_ds9(RegionList(regions=[Circle(x=1.0, y=1.0, radius=2.0, line_width=3),
                                    Arrow(x=5.0, y=5.0, length=4.0, angle=0.0)]))
print(out)
```
```
[('Circle', 'red', 3), ('Arrow', 'green', 2)]
# Region file format: DS9 astropy/regions
global width=3 color=green
image
circle(2.00000000,2.00000000,2.00000000)
# vector(6.00000000,6.00000000,4.00000000,0.00000000) vector=1 color=green
```

### Root cause
`_attributes_from_properties` (`ds9_regions.py:730-754`) reads only the vector's own line. `regions` applies the `global` line to the shapes it parses, so the two paths diverge. On export, `_arrow_lines` writes `width=` only when it is not 2 (`:358-359`). The library's `global` line carries the shapes' width, and ds9 applies it to the arrow too.

### Fix (proposed)
Have `_extract_vectors` track the most recent `global` line and use its colour, width and dash as the base for `_attributes_from_properties`. Always write `width=` on vector lines.

### Regression test (proposed)
`tests/test_ds9_regions.py`:
- `test_a_vector_takes_the_global_line_like_a_shape`: assert the arrow's colour and width equal the circle's.
- `test_an_exported_arrow_states_its_width`.

R81–R83 were added after the review, from a Gemini NIFS reference cube (`ctfbrsnN20180513S0077.fits`,
an IRAF `nifcube` product; not in the repository). Its SCI header has no `BUNIT`, no celestial WCS
and an IRAF-style spectral axis, which none of the review's test files had.

## R81. A cube that is not from OSIRIS and has no celestial axis types opens with wavelength across the screen

- **Status:** open
- **Severity:** medium — the default view of every such cube (Gemini NIFS among them) shows wavelength against one spatial axis, and pages through the other spatial axis as if it were wavelength
- **Files:** `pyql3/gui/viewers/image_viewer.py:1117-1122` (default X axis in `set_data`)
- **Related:** R25 (the same default chooses the axes of a non-science JWST extension)

### Symptom
The NIFS cube (NAXIS1 = 60, NAXIS2 = 62, NAXIS3 = 2040 wavelength channels) opens with X = AXIS 3,
Y = AXIS 2, Z = AXIS 1: the image is a 2040 × 62 wavelength-by-y strip and the slice slider steps
through the 60 spatial columns. The user has to remap the AXIS combo boxes by hand for every file.

### Reproduction
```python
import numpy as np
from astropy.io import fits
from PySide6.QtWidgets import QApplication
app = QApplication([])
from pyql3.gui.viewers.image_viewer import ImageViewer

h = fits.Header()          # the SCI header of a NIFS cube, cut down
for k, v in [('DISPAXIS', 3), ('CTYPE1', 'LINEAR'), ('CTYPE2', 'LINEAR'), ('CTYPE3', 'LINEAR'),
             ('CD1_1', 0.05), ('CD2_2', 0.05), ('CD3_3', 2.1325798), ('CRVAL3', 19968.66),
             ('CRPIX3', 1.0), ('WAT1_001', 'wtype=linear axtype=xi'),
             ('WAT2_001', 'wtype=linear axtype=eta'), ('WAT3_001', 'wtype=linear axtype=wave')]:
    h[k] = v
data = np.zeros((40, 12, 10), dtype=np.float32)      # numpy (wave, y, x)
v = ImageViewer(); v.set_data(data, h)
print(v.current_x_axis, v.current_y_axis, v.current_z_axis, v.current_plane().shape)
# -> AXIS 3 AXIS 2 AXIS 1 (40, 12)        expected: AXIS 1 AXIS 2 AXIS 3 (10, 12)
```

### Root cause
The default X axis is chosen by testing whether `CTYPE1` contains `RA`; anything else is given
OSIRIS's layout (wavelength on AXIS 1, RA on AXIS 3). OSIRIS's layout is unusual among IFUs, and a
cube with no celestial axis types gives the test nothing to go on.

### Fix (proposed)
Choose the default axes in one Qt-free function, in this order:
1. The WCS has celestial axes (`wcs.wcs.lng`, `wcs.wcs.lat` ≥ 0): X = longitude axis, Y = latitude
   axis, Z = the remaining one. This keeps OSIRIS (X = AXIS 3, Y = AXIS 2) and JWST (X = AXIS 1,
   Y = AXIS 2) as they are today.
2. A spectral axis can be identified (R82): Z = that axis, X and Y = the other two in FITS order.
3. The file is from OSIRIS (`INSTRUME` or `CURRINST` is `OSIRIS`): OSIRIS's layout.
4. Otherwise assume FITS axes 1, 2, 3 are x, y and wavelength (decided 2026-10-21: most IFUs write
   `[x, y, wavelength]`; OSIRIS is the exception).

### Regression test (proposed)
`tests/test_header_info.py::test_default_axes`, parametrized: OSIRIS-ordered celestial WCS → X = 2,
Y = 1, Z = 0 (0-based FITS axes); JWST-ordered → 0, 1, 2; the NIFS header above → 0, 1, 2; a bare
header with `INSTRUME = 'OSIRIS'` → 2, 1, 0; a bare header with no instrument → 0, 1, 2.
`tests/test_image_viewer.py::test_a_nifs_cube_opens_with_wavelength_on_z`: the reproduction above
gives a (10, 12) plane and Z = AXIS 3.

## R82. A spectral axis written the IRAF way (`CTYPE = 'LINEAR'` with `WAT… axtype=wave` or `DISPAXIS`) is not recognised

- **Status:** open
- **Severity:** medium — NIFS cubes get no wavelength axis, no wavelength in the slice label and no line list
- **Files:** `pyql3/gui/viewers/image_viewer.py:1186-1199` (`'WAVE' in ctype`), `pyql3/gui/tools/depth_plot.py:1264`
- **Related:** R24 (`AWAV` missed by the same string test). R24's proposed fix, `wcs.wcs.spec`, does not cover this case: astropy reports no spectral axis (`spec == -1`) for `LINEAR` axes.

### Symptom
With the NIFS cube remapped so Z is AXIS 3, `wcs_z_idx` is None: the Depth Plot's x-axis is
"Slice Index (pixels)" and the line list cannot be drawn. The header does describe the axis:
`CRVAL3 = 19968.66`, `CD3_3 = 2.13`, `WAT3_001 = 'wtype=linear axtype=wave'`, `DISPAXIS = 3`. It
gives no unit; the values are Ångström by Gemini's convention (1.997 µm at channel 0, and the
primary header's `WAVELENG = 22000.0` is in Å), and astropy's linear transform evaluates them
correctly.

### Reproduction
The R81 snippet, then `v.combo_x.setCurrentText('AXIS 1'); v.combo_y.setCurrentText('AXIS 2')` (or
with R81 fixed, nothing) and `print(v.wcs_z_idx, v.get_wavelength_for_slice(0))` → `None None`.

### Root cause
Spectral axes are found only by `'WAVE' in ctype`. IRAF records the axis type in the `WATn_001`
string (`axtype=wave`) and the dispersion axis in `DISPAXIS`, not in `CTYPEn`.

### Fix (proposed)
`header_info.spectral_axis(wcs, header)`: `wcs.wcs.spec` when ≥ 0; else the axis whose `WATn_001`
contains `axtype=wave`; else `DISPAXIS − 1` when the header has it and that axis is not celestial;
else None. The unit comes from `CUNITn`, or from `units=` in `WATn_001`. With neither, the unit is
unknown: the axis is labelled "Wavelength (Unknown)" and shows the header's numbers as they are, and
the line list says it needs a known wavelength unit rather than guessing one (decided 2026-10-21:
unknown units are labelled "Unknown", never inferred). The user can then state the unit themselves —
a *Wavelength unit* choice (Å / nm / µm) beside the Depth Plot's line list, labelled "set by you"
wherever it is shown (plan Task 23).

### Regression test (proposed)
`tests/test_header_info.py::test_spectral_axis_from_iraf_cards` (WAT, DISPAXIS alone, neither) and
`::test_a_missing_spectral_unit_is_unknown`. `tests/test_image_viewer.py::test_an_iraf_spectral_axis_has_wavelengths`:
the R81 header gives a spectral Z axis whose slice 0 value is 19968.66 and whose label reads
"Wavelength (Unknown)"; with `WAT3_001 = 'wtype=linear axtype=wave units=angstroms'` slice 0 is
1.99687 µm.

## R83. "As Total DN" is offered whatever the data's unit is, and ignores `EXPTIME`

- **Status:** open
- **Severity:** medium — for JWST and NIFS cubes the Total DN view shows the native numbers relabelled as Total DN, which reads as a conversion that never happened
- **Files:** `pyql3/gui/viewers/image_viewer.py` (`data_multiplier`, `update_colorbar_label`), `pyql3/gui/main_window.py` (Units menu, `set_display_unit`), `pyql3/core/header_info.py`
- **Related:** R1 (the OSIRIS integration time), R25 (exposure keywords in another HDU)

### Symptom
The unit each file declares:

| File | `BUNIT` | Exposure keywords |
|---|---|---|
| OSIRIS `s180722_a005002_Kn3_035.fits` | `'ADU per second per coadd'` (not a standard unit string) | `ITIME` (ms), `TRUITIME` (s), `COADDS` |
| JWST NIRSpec / NIRCam `*_s3d.fits`, `*_cal.fits` | `'MJy/sr'` | `EFFEXPTM` (primary), `XPOSURE` (SCI) |
| Gemini NIFS `ctfbrsn*.fits` | none, in either header | `EXPTIME`, `COADDS` (primary only) |

Only OSIRIS's data are a per-second count rate. For the JWST cubes, multiplying MJy/sr by an
exposure time has no meaning; for the NIFS cube the unit is undeclared, and its values (median 326,
99.9th percentile ~11,700 over 600 s) look like counts already summed over the exposure. In both,
"As Total DN" is still offered, the multiplier is 1.0 because no `ITIME` is in the header the viewer
receives, and every label switches to "Total DN". The labels say "DN/s" for all three files.

### Reproduction
Load any JWST s3d cube, choose Units → As Total DN: the values are unchanged and the colorbar
reads "Total DN". The "DN/s" text is hard-coded (13 places in `pyql3/`, among them
`update_colorbar_label` and the Units menu's "As DN/s"). The R81 snippet with `v.disp_as_dn = True` gives `data_multiplier == 1.0`.

### Root cause
The DN/s → Total DN conversion assumes every cube is a per-second count rate and never reads
`BUNIT`.

### Fix (proposed)
Read the unit once (`header_info.data_unit(header)`): OSIRIS's text, or a standard unit string
parsed with `astropy.units`, decides whether the data are a per-second rate. Offer Total DN only for
a rate with a known integration time (`TRUITIME` / `ITIME` × `COADDS`, else `EXPTIME`); otherwise
disable the menu action and say why in its tooltip ("data are in MJy/sr", "the file does not state
its unit"). Label the colorbar and plots with the declared unit, or "Unknown" when the file declares
none.

### Regression test (proposed)
`tests/test_header_info.py::test_data_unit` (OSIRIS text, `MJy/sr`, `DN/s`, `electron/s`, missing).
`tests/test_menu_actions.py::test_total_dn_is_offered_only_for_a_count_rate`: an MJy/sr cube and a
cube without `BUNIT` leave "As Total DN" disabled with a tooltip; the OSIRIS fixture enables it.

## R65–R80, R84. Minor items

| # | File | Issue | Fix (proposed) |
|---|------|-------|-----|
| R65 | `pyql3/gui/viewers/image_viewer.py:1784-1794` | The colorbar shows the scaled render values but is labelled "Pixel Value (DN/s)". For data from 25 to 9825 it reads 0 to 99.0 under Sqrt, 0 to 3.99 under Logarithmic and 0 to 1 under HistEq. | Label the axis with the scale in use for non-linear modes, or map its ticks back to data values through the inverse of the scale. |
| R66 | `pyql3/gui/viewers/image_viewer.py:834-854` | The "1:1" buttons set the range to the whole image, so they behave like Fit. A 300×200 image in a 900×900 window shows 0.333 data px per screen px after 1:1 and 0.356 after Fit. QL2's 1:1 sets the zoom factor to 1. | Set the view range so that one data pixel covers one screen pixel (width and height of the ViewBox in screen pixels), centred on the current view. |
| R67 | `pyql3/gui/viewers/image_viewer.py:1389-1391` | ✅ FIXED — `pyql3/gui/viewers/image_viewer.py` (`_set_view_transform`), covered by `tests/test_image_viewer.py::test_view_rotation_pivots_on_the_plane_centre`. `apply_view_rotation` rotates about `((shape[-1]-1)/2, (shape[-2]-1)/2)`, which swaps x and y and drops the half-pixel offset of the ImageItem. On a 51×66 plane a 180° view rotation moves the image centre from (25.5, 33.0) to (39.5, 17.0). | Pivot on `(shape[-2] / 2, shape[-1] / 2)`, the centre of the ImageItem's bounding rect. |
| R68 | `pyql3/gui/viewers/image_viewer.py:1071`, `pyql3/gui/tools/rotate.py:95-116` | `set_data` resets `view_rotation` to 0 on every load, including each poller auto-load, but an open Rotate dialog is not told. After a 30.0° view rotation and a reload, the view is unrotated while the dialog shows 30.0 and "30.0° CCW from up". The Main Window review measured that after North Up, the next frame draws North at 55.0° with rotation 0 while the dialog still shows 35.0 and "North is Up". | Either keep the view rotation across a reload of the same field, or emit a signal from `set_data` that the Rotate dialog uses to reset its spin box and call `update_pa_info()`. |
| R69 | `pyql3/gui/main_window.py:723` | `load_from_memory` applies `os.path.basename` to the Arithmetic expression, so any division keeps only the text after the `/`: `(ActiveImage / 2.0)` is titled `QuickLook 3 -  2.0)`, and `(a001.fits / a002.fits)` is titled ` a002.fits)`, which reads as if the window held `a002.fits`. | Use the title as given for in-memory data, and apply `basename` only to real file paths. |
| R70 | `pyql3/services/poller.py:110-120`, `pyql3/core/fits_reader.py:259` | Saving the displayed file's header in place, inside a watched directory, replaces the file with `os.replace`. The poller re-announces it about one scan later, and the reload resets the view: a 5 x 5 px zoom on a 30 x 30 px frame came back as the full frame (a 31.5 x 32.4 px view). | In `MainWindow`, skip an auto-load whose path and `(st_size, st_mtime_ns, st_ino)` match what `fits_reader` itself just wrote. |
| R71 | `pyql3/core/fits_reader.py:67`, `:232-233`, `:256` | A header-only save (Header Editor → Save Header → Yes) re-encodes scaled integer data. A BITPIX 16 file with BSCALE 0.5, BZERO 100 and BLANK was rewritten as BITPIX −32 with all three keywords removed. Values are preserved, but the data type on disk changes. | Open with `fits.open(..., memmap=False, scale_back=True)` so `writeto` restores the original BITPIX and scaling. |
| R72 | `pyql3/gui/tools/fitting.py:12-15` vs `:22-23`, `:32-33` | The Gaussian model's cross term has the opposite sign to the Lorentzian and Moffat rotations. For one source with its major axis 30° CCW from +X, the fits report −120.0° (Gaussian), +120.1° (Lorentzian) and −60.0° (Moffat). The Gaussian angle is mirrored (−120 ≡ 60 mod 180, the others ≡ 120), so "Angle (deg)" depends on the model chosen. | Flip the sign of `b` in `gaussian_2d` so all three models share the CCW convention, and document which width "FWHM X" refers to. |
| R73 | `pyql3/services/cli_install.py:206-210`, `:238-239`, `:256-259` | When a `quicklook3` not written by QuickLook 3 already exists, `plan()` records `existing='foreign'` without raising, despite its docstring. The confirmation then says "Create: <path>" and "To undo it: rm <path>", which would delete someone else's file. Only after the user clicks OK does `install()` refuse ("already exists and was not created by QuickLook 3"). | Raise `CliInstallError` from `plan()` for a foreign file (or fall through to the next candidate directory), so nothing is offered that will be refused. |
| R74 | `pyql3/services/cli_install.py:61-76`, `:196-201` | Reasoned and simulated, not reproduced on a real system. macOS App Translocation runs a quarantined, unmoved bundle from a read-only `/private/var/folders/.../AppTranslocation/<UUID>/d/QuickLook3.app`. That path is not under any `MOUNT_ROOTS` entry, so `on_temporary_volume` is False, `plan()` accepts it, and the launcher's `exec` line names a path that is gone after reboot. | Also refuse when the resolved bundle path contains `/AppTranslocation/`, with the same "copy it to /Applications first" message. |
| R75 | `pyql3/gui/tools/plot_catalog.py:986-987`, `:1102` | The label-ceiling message ("10 labels in view — more than the 3 that can be drawn at once", with the limit lowered to 3) is replaced by the plain status line on the next `update_plot`, such as a marker-size change, while the labels remain refused; the user is left with a ticked *Labels* box, no labels and no explanation. `LabelDensityGuard.allows` reports only changes of verdict, so the message is not written again. | After `update_plot` writes its status, re-apply the ceiling message when `self._label_guard.suppressing` is still true. |
| R76 | `pyql3/gui/tools/region_list.py:257-259` | The Region List's **Delete All** button calls `layer.clear()` straight away, with no confirmation. **Region ➔ Delete All Regions** and the toolbar's clear button both ask first (`pyql3/gui/main_window.py:1199-1210`), and there is no undo. | Have `delete_all()` call the window's `delete_all_regions()` (as the toolbar does), or ask the same `QMessageBox.question` itself. |
| R77 | `pyql3/core/regions_io.py:248-271`, `pyql3/gui/main_window.py:1035` | `load_regions` returns the file's `style:` block, but the window keeps only the regions and `save_regions` takes no style. A file with `style: {color: red, line_width: 1}` is re-saved with the default block (green, 2), and `color: red` and `line_width: 1` are written on every region, so editing the block no longer restyles them. | Keep the loaded style with the layer and pass it through `save_regions` as `RegionList.style`. |
| R78 | `pyql3/core/regions_model.py:458-472` | `looks_like_ds9` needs ds9's header line, or a line that is exactly one of five frame words. Headerless files that ds9 accepts — `fk5;circle(...)` lines, a `j2000`/`fk4`/`b1950`/`ecliptic` frame line, or bare `circle(...)` lines — go to the YAML reader and fail with "should be a mapping with 'format' and 'regions' keys, found str". | Recognise any `_FRAME_RE` frame word, including inside `;`-separated statements, and a shape call in text that does not parse as a YAML mapping. |
| R79 | `pyql3/core/ds9_regions.py:426-428`, `:332`, `:355` | A label containing a newline (accepted from hand-edited YAML) is written verbatim into `text={...}` and splits the line in two. ds9 rejects a file with a syntax error whole, and our own reader returns the three labels truncated to "line", "tilted" and "", with three warnings. Shape labels written by `regions` break the same way. | Replace newlines and carriage returns with spaces in `_escape_braces` and in `_visual_and_meta` before the library sees the label, and note it in the Report. |
| R80 | `pyql3/gui/tools/depth_plot.py:549-562` (`_circular_mask`), called from `_cut_region` `:524` and the background box `:1665` | With Shape set to Circle, a cut (Horizontal/Vertical Cut through the aperture) or a background box that hangs off the image builds its circular mask from the box *after* `coords.box_index_slices` has clipped it to the image. The mask is centred on the clipped box and its radius is half the clipped box's shorter side, so it measures a smaller circle shifted toward the image interior instead of the part of the drawn circle that is on the image. Pre-existing; found in the Phase 1 review (2026-10-21), and reachable more often since R19 made an off-edge box measure its on-image part. | Build the mask from the unclamped circle (the drawn box's centre and radius, in the clipped box's index frame) and intersect it with the image, so the pixels kept are the drawn circle's on-image pixels. |
| R84 | `pyql3/gui/viewers/image_viewer.py` (`apply_z_range`, reached from `show_current_plane`) | An unparsable Z Min or Z Max ("abc") present during `set_data` or any redisplay in Z Range mode makes `apply_z_range` stop at "Collapsed: Invalid Range" after `_give_imageview_the_cube()` has already loaded the cube, so channel 0 stays painted with the previous image's levels and `display_data` is left as the full cube copy that `apply_axis_mapping` made, not the plane on screen. Measured: Sum over 4-8 of a cube whose channel k holds k + 100 (530.0, levels 530-531); Z Min set to "abc" and the cube reloaded ×10: the screen shows channel 0 (1000.0) at levels 530-531. No `plane_changed` is emitted, while `display_changed` is. Found in the Phase 2 review (2026-10-21). | Put a `QIntValidator` on Z Min, Z Max and the Boxcar field so an unparsable value cannot be entered, or fall back to the full range with a message in `lbl_slice_info`. |

## Working checklist — code review 2026-10-21

High
- [x] R1 Total DN 1000× too large for OSIRIS (ITIME in ms) — plan Task 1
- [x] R2 Apertures measured 0.5 px from the drawn circle (Depth Plot, Photometry, Strehl) — plan Task 2
- [x] R3 Save FITS As drops every other HDU and the primary header — plan Task 3
- [x] R4 Header editor + auto-load writes one frame's header onto another — plan Task 5
- [x] R5 Flip/rotate/unit/reload replaces a collapsed or boxcar plane with channel 0 — plan Task 6
- [x] R6 Hover readout ignores view rotation — plan Task 8
- [ ] R7 Depth Plot not recomputed on load, flip, rotation or remap — plan Task 12
- [ ] R8 Catalog markers not re-placed when the displayed image changes — plan Task 13
- [x] R9 Statistics and Peak Fit sample interpolated values, zeros off the image — plan Task 4
- [ ] R10 Sky anchors assume ICRS (galactic reload raises, FK4 angles, FK5/ICRS offset) — plan Task 35

Medium
- [x] R11 Auto-load retries never cancelled — plan Task 5
- [ ] R12 A failed load leaves `FitsReader` on a different file — plan Task 17
- [ ] R13 An unparseable WCS fails the load and leaves the viewer half-updated — plan Task 18
- [ ] R14 Statistics, Photometry, Peak Fit, Strehl never refresh; cuts miss Boxcar/Range — plan Task 11
- [ ] R15 Escape hides a tool without its teardown — plan Task 9
- [ ] R16 Tool dialogs fall outside `TOOL_DIALOG_ATTRS` — plan Task 10
- [ ] R17 Arrow-only ds9 export declares `icrs` whatever the WCS frame — plan Task 35
- [ ] R18 Photometry sky wrong at edges and with NaN; inverted annulus freezes — plan Task 24
- [x] R19 Depth Plot rectangle clamping measures shifted or edge pixels — plan Task 4
- [ ] R20 Region-mode background over-subtracts where aperture pixels are NaN — plan Task 22
- [ ] R21 Total is 0.0, not NaN, for a channel with no finite aperture pixel — plan Task 22
- [ ] R22 A sky annulus inside the aperture is accepted silently — plan Task 22
- [ ] R23 Arithmetic: 2-decimal Number, integer wrap, inconsistent header — plan Task 25
- [ ] R24 `AWAV` not recognised; stale WCS readout — plan Task 20
- [ ] R25 Non-science extension set up from its own header only — plan Task 21
- [ ] R26 Duplicate axis choice leaves `current_y_axis` wrong — plan Task 20
- [x] R27 Arrow keys / Home / End show a raw channel in collapse mode — plan Task 6
- [ ] R28 Depth Plot Y label not updated for Total DN — plan Task 12
- [ ] R29 Catalog recentre wrong under view rotation — plan Task 14
- [ ] R30 SExtractor `X_IMAGE`/`Y_IMAGE` plotted one pixel off — plan Task 28
- [ ] R31 RA/Dec catalogs take 9.5 s per refresh at 66k rows — plan Task 13
- [ ] R32 VizieR coordinate names not recognised — plan Task 29
- [ ] R33 Region properties dialog undoes a drag made while it is open — plan Task 31
- [ ] R34 Region List Delete can remove the wrong region — plan Task 32
- [ ] R35 Aggregate-mode labels never come back after a pan — plan Task 33
- [ ] R36 Channel-range visibility ignores Collapse Region mode — plan Task 15
- [ ] R37 Non-square pixels: region angles wrong and not round-trip stable — plan Task 36
- [ ] R38 Swapped display axes: box and arrow angles not converted — plan Task 36
- [ ] R39 Angle 0 vs 360 makes a file look moved on its own image — plan Task 37
- [ ] R40 Per-region `frame: image` ignored on load, overwritten on save — plan Task 37
- [ ] R41 A stale sky anchor is kept and undoes later moves — plan Task 37
- [ ] R42 Header Editor writes rows back by keyword name — plan Task 26
- [ ] R43 Restarting a watch loses seen-but-unannounced files — plan Task 19
- [ ] R44 A shape's caption stays behind when the shape is dragged — plan Task 33
- [ ] R45 View rotation ignored by Zoom To and label culling — plan Task 15
- [ ] R46 Region drawing mode cannot be cancelled — plan Task 34
- [ ] R47 ds9 vector frame tracking misses common forms — plan Task 38

Low
- [ ] R48 ds9 conversion drops information with an empty Report — plan Task 39
- [ ] R49 File → Exit closes only the current window — plan Task 40
- [ ] R50 Catalog labels return with Show All off — plan Task 30
- [ ] R51 Show Region Labels off: new labels shown, text regions vanish — plan Task 33
- [ ] R52 2-D image with a 3-axis WCS: readout and compass wrong — plan Task 20
- [ ] R53 Depth Plot Log X removes the line list and breaks the readout — plan Task 23
- [ ] R54 Line lists keyed by file name; empty parse leaves old status — plan Task 23
- [ ] R55 Depth Plot hidden rectangle spins go stale — plan Task 23
- [ ] R56 ROTPOSN fallback compass uses 90 + PA — plan Task 20
- [x] R57 Display → Scaling menu drops timeline and manual levels — plan Task 6
- [ ] R58 Recent Files stale across windows and processes — plan Task 40
- [ ] R59 Finder multi-select into one window; `--poll-dir` misses `.fits.gz` — plan Task 41
- [ ] R60 Catalog control changes recentre the view — plan Task 14
- [ ] R61 Catalog RA/Dec passed by position on a Dec-first 2-D image — plan Task 13
- [ ] R62 A `SkyCoord` catalog column leaves the X/Y selectors dead — plan Task 29
- [ ] R63 A region with a channel range is hidden on a 2-D image — plan Task 15
- [ ] R64 ds9 vectors ignore the `global` line; export omits `width=` — plan Task 38

Added after the review (R81–R83 from a NIFS reference cube, R84 from the Phase 2 review)
- [ ] R81 Non-OSIRIS cube without celestial axis types opens with wavelength on X — plan Task 20
- [ ] R82 IRAF-style spectral axis (`LINEAR` + `WAT axtype=wave` / `DISPAXIS`) not recognised — plan Task 20
- [ ] R83 Total DN offered whatever the data's unit; `EXPTIME` ignored — plan Task 44
- [ ] R84 Unparsable Z field in range mode leaves channel 0 with old levels — plan Task 43

Minor items (table)
- [ ] R65 Colorbar shows transformed values — plan Task 43
- [ ] R66 "1:1" zoom only fits the image — plan Task 43
- [x] R67 View-rotation pivot off-centre — plan Task 7
- [ ] R68 Rotate dialog stale after a reload; North Up dropped per frame — plan Task 16
- [ ] R69 Arithmetic result window title — plan Task 25
- [ ] R70 In-place header save in a watched directory re-announces the file — plan Task 19
- [ ] R71 Header-only save re-encodes scaled integer data — plan Task 26
- [ ] R72 Gaussian fit angle sign differs from Lorentzian/Moffat — plan Task 27
- [ ] R73 `cli_install` confirms a path it then refuses — plan Task 42
- [ ] R74 App Translocation path baked into the launcher (reasoned, not reproduced) — plan Task 42
- [ ] R75 Catalog label-ceiling message overwritten — plan Task 30
- [ ] R76 Region List Delete All has no confirmation — plan Task 32
- [ ] R77 Region `style:` block not preserved on re-save — plan Task 37
- [ ] R78 Headerless ds9 files not detected — plan Task 38
- [ ] R79 A newline in a label breaks the `.reg` line — plan Task 38
- [ ] R80 Circle-shaped box off the image masks around the clipped box — plan Task 23

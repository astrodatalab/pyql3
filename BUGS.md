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

## B15. Minor items

| # | File | Issue | Fix |
|---|------|-------|-----|
| M1 | `photometry.py:72` and `:92` | Two `closeEvent` definitions; the second silently overrides the guarded, `try/except`-wrapped first one. Currently latent (pyqtgraph's `removeItem` does remove the annuli), but the surviving version will raise if `roi_inner` was never created. | Delete the second; keep the guarded one and have it also `setParentItem(None)`. |
| M2 | `poller.py:34-45` | `self.watch_path = path` is assigned *before* the `os.path.exists` check, so a nonexistent directory is still persisted to `~/.pyql3/config.json` by `open_polling_config` (`main_window.py:585`). | Validate first, assign only on success. |
| M3 | `poller.py:14`, `:23` | Watches `.fits`/`.fit` only, while the Open dialog accepts `.fits.gz`. | Add `.gz` handling (check the full suffix chain). |
| M4 | `arithmetic.py:150-163` | The Active-Image operand is not cast to float, unlike the file operand (`:161`). Not currently reachable for integer wraparound (the other operand is always float), but the two paths differ in dtype/precision. | `return self.image_viewer.raw_data.astype(float), ...`. |
| M5 | `arithmetic.py:199-201` | `final_header = h1 if h1 is not None else ...` mutates the operand's header in place via `add_history`. For the file operand that object is fresh, but for `Active Image` it is a `.copy()` — fine today, fragile if the copy is ever dropped. | `final_header = (h1 or h2 or fits.Header()).copy()`. |
| M6 | ✅ FIXED — `main.py` | `--catalog` was not passed through `os.path.expanduser`, so `~/cat.csv` was silently ignored (unlike `--poll-dir`). | Done: expanded, and a missing file now says so on stderr instead of being ignored. `--regions` was added the same way, and `tests/test_region_ui.py` asserts every command-line path expands `~`. |
| M7 | `advanced_plots.py:44` | Downsample factor uses `img_data.shape[0]` only, so a 100×4096 array is not downsampled. | `ds = max(1, max(img_data.shape) // 256)`. |
| M8 | `advanced_plots.py:48`, `fitting.py:243` | `np.nan_to_num(data, nan=np.nanmedian(data))` warns and yields NaN for an all-NaN region. | Guard with `np.all(np.isnan(...))` and substitute 0. |
| M9 | `advanced_plots.py:134-138` | `ContourDialog.update_plot` returns early when *Show Contours* is unchecked, before `clear_contours()`, so contours from a previous slice can linger. | Clear first, then return. |
| M11 | ✅ FIXED — `main_window.py` | `create_menus` kept its `QMenu` objects in locals only, so nothing held a Python reference. Harmless while unwrapped, but any code calling `action.menu()` becomes a transient Python owner and PySide6 deletes the C++ menu **and every QAction in it** when that wrapper is collected. Confirmed while writing the Phase 4 region-menu tests: a helper that walked `menuBar().actions()` and called `.menu()` got back an action that was already dead. | Done: every menu is now stored on `self` (`self.file_menu`, `self.display_menu`, `self.scaling_menu`, `self.colormap_menu`, `self.units_menu`, `self.plot_menu`, `self.analysis_menu`, `self.region_menu`, `self.help_menu`, joining `self.window_menu` and `self.recent_menu`). **Reach a menu through its attribute, never through `action.menu()`** — see `tests/test_region_ui.py::menu_action`. |
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

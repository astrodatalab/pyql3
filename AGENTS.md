# AGENTS.md

Guidance for coding agents (Claude Code, and any tool that reads `AGENTS.md`) working in this
repository. `CLAUDE.md` and `.agents/AGENTS.md` are symlinks to this file.

`pyql3` / QuickLook 3 is a PySide6 + pyqtgraph viewer for integral field spectroscopy FITS
data, replacing the legacy IDL `ql2` GUI. It is tuned for Keck/OSIRIS cubes but must work for
other IFUs (JWST NIRSpec IFU, Gemini NIFS).

> **If this file contradicts the code, the code wins — fix this file in the same change.**
> These notes are maintained by hand and drift silently. Verify a claim before relying on
> it, and correct it when it is wrong rather than coding around it. This is not theoretical:
> the `real_osiris_fits` fixture was documented here as skipping when the reference cube is
> absent, when in fact a `pathlib` bug meant it *never* found the cube and the test had never
> run once.

## Commands

Dependencies are managed with `uv` — nothing is installed globally, so every Python
invocation must go through `uv run`. Do not assume packages are importable otherwise.

```bash
uv run python main.py                       # launch the GUI
uv run python main.py cube.fits --collapse-range 100 200
uv run pytest -v                            # full test suite (~60 s)
uv run pytest -q -n auto --dist loadfile    # the same suite in parallel (~15 s); what CI runs
uv run pytest tests/test_depth_plot.py -v   # one file
uv run pytest tests/test_cuts.py::test_name # one test
./build_app.sh                              # macOS/Linux bundle (.app + .dmg / .tar.gz)
build_app.bat                               # Windows bundle (.exe)
uv run mkdocs serve                         # docs site (mkdocs.yml)
```

Every test touches Qt. On a headless machine (or to keep windows from popping up during a
local run) prefix with `QT_QPA_PLATFORM=offscreen`.

**`--dist loadfile`, not plain `-n auto`.** The suite parallelises because each worker is a
separate process with its own `QApplication`, window manager, poller watch table and
`ConfigManager` — the process-wide singletons stay singletons *per worker*. `loadfile` keeps
every test in a file on one worker, so anything a file's tests share is where a serial run
puts it. Both modes pass today; the finer-grained default buys about a second and gives that
up. Debug serially — xdist swallows `print` and does not support `--pdb`.

Version numbers come from `setuptools_scm` via git tags — `pyql3/_version.py` is generated,
never edit it. Release binaries are built by `.github/workflows/release.yml`, triggered by
pushing a `v*` tag.

### Reference data (machine-local, never hardcode a path)

Two external resources are useful but live outside the repo, and their location differs per
machine. Point at them with environment variables — **do not commit absolute paths**, since
this repository is public:

| Variable | Contents |
|----------|----------|
| `PYQL3_QL2_REF` | Checkout of the original QL2 (IDL) reference implementation |
| `PYQL3_TEST_CUBE` | A real OSIRIS cube, e.g. `s150531_a025002_Kn5_035.fits` (Kn5, SSCALE 0.035, 465 channels) |

Set them in your shell profile, e.g.:

```bash
export PYQL3_QL2_REF="$HOME/path/to/ql2"
export PYQL3_TEST_CUBE="$HOME/path/to/s150531_a025002_Kn5_035.fits"
```

`tests/conftest.py` exposes the cube as the `real_osiris_fits` fixture, which returns `None`
(so dependent tests skip) when the variable is unset or the file is missing.

## Architecture

Entry point `main.py` builds the `QApplication` and a single `MainWindow`
(`pyql3/gui/main_window.py`), which owns:

- **`ImageViewer`** (`pyql3/gui/viewers/image_viewer.py`, the largest file) — the central
  `pyqtgraph.ImageView`, axis mapping, z-collapse, scaling, colormaps, WCS readout, PA
  compass, view rotation. Nearly everything else reads state off this object.
- **Tool dialogs** (`pyql3/gui/tools/*`) — modeless `QDialog`s subclassing `BaseToolDialog`
  (`base_tool.py`), each holding a reference to the shared `image_viewer` and typically an
  ROI it adds to / removes from the viewer's scene.
- **`RegionLayer`** (`pyql3/gui/viewers/region_layer.py`) — the drawn regions on that viewer,
  built last in `ImageViewer.__init__` and reached as `image_viewer.region_layer`. See
  "Regions" below.
- **`FitsReader`** (`pyql3/core/fits_reader.py`) — HDU list ownership, multi-extension
  image discovery, header edits, save.
- **`DirectoryPoller`** (`pyql3/services/poller.py`) — auto-loads new FITS files;
  **`ConfigManager`** (`services/config.py`) persists recent files to
  `~/.pyql3/config.json`.

**The poller scans; it does not use filesystem events (CRITICAL).** `watchdog`'s default
`Observer` is a kernel backend (FSEvents/inotify) that only sees changes made through the
*local* kernel. The OSIRIS DRP frequently writes from another host onto an NFS share, which
generates no local events at all, so `poller.py` uses `PollingObserver` deliberately. Do not
"optimise" it back to `Observer` — that silently breaks auto-load in the deployment it
exists for. A file is announced only once `(st_size, st_mtime_ns)` holds steady across
consecutive scans, and when several land at once only the newest is displayed. Because NFS
attribute caches can report a stale size, a failed parse means *retry later*, never
*corrupt*: `MainWindow._attempt_auto_load()` backs off and only warns once retries are
exhausted. A queued retry carries the `_auto_load_generation` it was scheduled under and does
nothing once that has moved on: a new detection, and every `load_fits` call made with the
default `cancel_auto_load=True`, advance it, so a late retry cannot replace a newer frame or a
file the user opened. **Only the auto-load passes `cancel_auto_load=False`.** The end of a watch
advances it too (`DirectoryPoller.polling_stopped`, emitted by `stop_polling()` and so also when
`start_polling()` moves to another directory or another poller takes this one over), so a retry
from the old directory cannot land after a restart. **Restarting the directory already watched is
not the end of a watch**: an `interval` change restarts the `PollingObserver` without emitting
`polling_stopped`, and keeps the files seen but not yet announced (`_pending` and the held file),
because the new observer's first scan counts them as pre-existing and would never offer them again
(`BUGS.md` R43). A takeover moves them to the new owner. An auto-load of the file the window
already holds, unchanged since it was read (`FitsReader.holds_current_copy()`, which compares
canonical paths), does nothing, and `on_file_detected()` returns before advancing the generation:
that is the window's own Save Header coming back from the poller, reloading it reset the view
(`BUGS.md` R70), and it must not cancel a retry queued for a real frame. A modal dialog that edits or saves the loaded file runs inside
`MainWindow.auto_load_held()`, as `edit_header()` and `save_file_as()` do — its `exec()` (or
`QFileDialog.getSaveFileName`) runs a nested event loop in which the poller keeps announcing
frames and queued retries keep firing. The hold covers both: an attempt that comes due stores
its path, and the newest is loaded when the hold ends. A file opened by hand still reaches
`load_fits` during the hold, so such a dialog also records `(fits_reader.filepath,
fits_reader.hdul)` when it opens and refuses to write if either has changed.

`MainWindow` caches each tool as a `self._<name>_dialog` attribute and reopens it only if
not already visible. Those attribute names are listed once, in `MainWindow.TOOL_DIALOG_ATTRS`,
which `close_tool_dialogs()` walks from `closeEvent`. **A new tool must be added to
`TOOL_DIALOG_ATTRS`** or it will be left on screen after its window closes. Every opener goes
through `MainWindow._open_tool()`, which raises a visible dialog and replaces (and deletes) a hidden
one, so a window never holds two copies of a tool.

**Tools follow the viewer through its three signals, connected once in `BaseToolDialog.__init__`
and disconnected in `tear_down()`** (`BUGS.md` R14). While the dialog is visible, `plane_changed`
calls `on_plane_changed()`, `display_changed` calls `on_geometry_changed()` and
`view_rotation_changed` calls `on_view_rotated(angle)`. The default `on_plane_changed()` calls
`on_roi_changed()`, which is the recompute for every measuring tool; the other two do nothing,
because every `display_changed` follows a `plane_changed` and recomputing on both does the work
twice per flip, load or unit change. Override `on_geometry_changed()` only for output that depends
on the geometry and not on the plane's values. A unit change also emits `display_changed` and
moves nothing, so an overlay that re-places items on it compares `ImageViewer.placement_key()` with
the key it last placed under and skips an unchanged one, as the region layer and the catalog do.
A unit change reaches the tools the same way, so
`set_display_unit()` calls none of them; do not connect a tool to `imv.sigTimeChanged`, which a
Z Range apply and a Boxcar width change at the same slider value never send, so a tool connected
to it misses those planes.

**A tool releases what it put on the viewer in `tear_down()`, never in `closeEvent`.** Escape
reaches `QDialog.reject()`, which hides the dialog without sending a close event, so cleanup in
`closeEvent` left ROIs, markers and a live Draw Region behind (`BUGS.md` R15).
`BaseToolDialog` calls `tear_down()` from both `closeEvent` and `reject()`; a Close therefore runs
it twice, and it is guarded by `_torn_down`. A subclass override returns at once when that flag is
set, releases its own items, then calls `super().tear_down()`. Do not make `reject()` call
`close()` instead: `QDialog.closeEvent` calls `reject()` itself, and the re-entered close leaves
the dialog on screen.

### Multiple windows (CRITICAL)

Several `MainWindow`s are open at once — **File ➔ New Window**, **Open in New Window...**, the
Arithmetic tool's result, and one per file named on the command line. A window owns everything
it displays: its `FitsReader`, `ImageViewer`, tool dialogs and `DirectoryPoller`. Tools take the
viewer as a constructor argument (`base_tool.py`) and never look it up, so independence is
automatic — do not reintroduce a lookup that walks up to "the" main window.

Three things are process-wide and must stay that way:

- **`get_window_manager()`** (`pyql3/gui/window_manager.py`) is the window list, plus the
  most-recently-used order. A path arriving with no window attached (a Finder open-document
  event) goes to `WindowManager.open_path()`, which targets the most recently used window and
  creates one if all are closed, or when that window already took a path in the same event-loop
  pass (several files selected in Finder arrive as one event each; `BUGS.md` R59).
  `quicklook3 cube.fits` does **not** arrive this way: the
  launcher ends in `exec` (`cli_install.shim_text()`) and nothing implements a single-instance
  hand-off, so every call is a separate process with its own window list and its own
  `ConfigManager` — two of them share `~/.pyql3/config.json`; each change re-reads the file
  before writing it, so neither drops the other's entries, though there is no lock (`BUGS.md` R58). **Never bind
  the open-document handler to a particular window's `load_fits`** — that was a crash waiting to
  happen, since closing that window left `FileOpenHandler` calling a method on a deleted C++
  object.
- **`get_config()`** (`services/config.py`) is one `ConfigManager` per process. A manager per
  window gave each one a private snapshot of `~/.pyql3/config.json`, so recent-files updates
  overwrote each other — atomically, and therefore invisibly.
- **One poller per directory.** `DirectoryPoller.start_polling()` takes the watch over from
  whoever holds it (`watcher_of()`), rather than adding a second `PollingObserver` that would
  double the scan traffic and load every frame twice. Auto-loaded frames go to the window that
  owns the watch — *not* to the most recently used window, so a watch following a reduction
  cannot hijack a window opened to compare something. `MainWindow.confirm_watch_takeover()` asks
  before moving a watch, and `PollingDialog` takes that as its `confirm_takeover` hook.

`MainWindow.closeEvent()` releases all three kinds of per-window state: it stops the poller
(else an orphaned observer thread keeps scanning), closes the tool dialogs (they are top-level
windows, so they otherwise stay on screen and keep the application alive past the last main
window), and closes the `FitsReader` (an open handle makes the file unreplaceable on Windows).
The **Window** menu lists every window of the application, grouping each window's tool dialogs
under it once more than one is open.

### Data state: `raw_data` vs `transposed_data` vs `display_data` (CRITICAL)

`ImageViewer` keeps three arrays and confusing them corrupts output files:

- `raw_data` — the untouched FITS array, original axis order. **All analytical calculations,
  data exports, and FITS header writes must use this.** Anything else produces cubes with
  permanently swapped physical axes and a broken WCS.
- `transposed_data` — axes reordered for pyqtgraph's (X, Y) convention, since NumPy provides
  (Y, X). `apply_axis_mapping()` builds it from the AXIS 1/2/3 combo boxes.
- `display_data` — a copy of `transposed_data` after `apply_transforms()` applies the DN
  multiplier, flip, and 90° rotations. This is what's on screen; it is not analysis input.

The DN/s → Total DN conversion is exposed as the `data_multiplier` property, whose value is
`header_info.total_dn_multiplier()`: `TRUITIME` (seconds) times `COADDS`. `ITIME` is used only when
there is no usable `TRUITIME`, because it is in milliseconds in OSIRIS cubes written since the
detector upgrade, and `EXPTIME` (not multiplied by `COADDS`) only when there is neither. Tools should
multiply plotted values by `data_multiplier` rather than recomputing. It is 1.0 whenever Total DN is
not shown, and Total DN is offered only when `ImageViewer.total_dn_available`: the file's `BUNIT`
(`header_info.data_unit()`) is a count per second — per second exactly, since the multiplier is in
seconds — and the header states an integration time. `set_data` switches Total DN off, before it
announces the new plane, for data that do not support it. **Every unit label goes through
`ImageViewer.value_unit_label()`** — "Total DN", else the file's own unit (in the file's own words
when astropy cannot read it, which is then never a rate), "Unknown" only when neither the
extension's header nor the primary header it may borrow from states a `BUNIT`; never write
"DN/s" into a label. The keywords these
read come from `ImageViewer.data_keywords`, which may have been taken from the primary header (see
"An extension borrows what its header lacks" below).

### FITS axis / WCS conventions (CRITICAL)

OSIRIS cubes put wavelength on FITS axis 1, Dec on axis 2, RA on axis 3:

- `CTYPE1`: Wavelength (`WAVE` / `AWAV`)
- `CTYPE2`: Declination (`DEC--TAN`)
- `CTYPE3`: Right Ascension (`RA---TAN`)

`astropy.io.fits.getdata()` reverses these into C-contiguous order, so `data.shape` is
`(RA, DEC, WAVE)`. Other instruments put wavelength on axis 3 instead. **Never hardcode an
axis index, and never find an axis by a `CTYPE` substring** — `'WAVE' in ctype` missed `AWAV`
(`BUGS.md` R24) and every IRAF-style `LINEAR` axis (R82). The axes are found through the WCS, in
`pyql3/core/header_info.py`: the celestial axes are `wcs.wcs.lng` / `wcs.wcs.lat`;
`spectral_axis()` is astropy's `wcs.wcs.spec`, else the axis whose `WAT<n>_001` says
`axtype=wave`, else `DISPAXIS` (Gemini NIFS writes `CTYPEn = 'LINEAR'` and names the axis only
there). `set_data()` takes the default axes from `default_axes()`: with celestial axes, X is
longitude and Y latitude; else a spectral axis is Z and the other two are X and Y in FITS order;
else an OSIRIS file (`INSTRUME` or `CURRINST`) gets OSIRIS's layout; else FITS axes 1, 2, 3 are
taken to be x, y and wavelength, as most IFUs write them — so a cube with no header opens as
numpy `(z, y, x)`.

**An extension borrows what its header lacks** (`BUGS.md` R25). A JWST ERR or DQ extension has no
WCS cards, an OSIRIS noise or quality extension has an empty header, and NIFS keeps `EXPTIME` and
`COADDS` in the primary header only. `FitsReader.reference_header()` names the header to borrow
from — for a missing WCS, the first other image extension of the same shape with a celestial or
spectral axis, else the primary; for a missing integration time, the primary — and
`MainWindow.load_fits()` passes it to `set_data(reference_header=...)`. The viewer uses its WCS only
when the extension's own WCS has neither kind of axis **and** the reference WCS has as many axes as
the data: a NIFS primary header carries the 2-axis RA/Dec WCS of the raw detector frame, which is
not the cube's. `ImageViewer.wcs_header` is the header the WCS came from, and is what
`spectral_axis()` / `spectral_unit()` read; `ImageViewer.header` stays the extension's own.
`header_info.data_keywords()` takes the integration time with its `COADDS`, and `BUNIT`, from the
extension's header where it states them, else from the reference, into `ImageViewer.data_keywords`
— **but only when the reference is the primary header and the data are not integers**. OSIRIS
writes the unit and time of its float noise extension in the primary; a sibling extension describes
only its own values (JWST's SCI is MJy/sr, its DQ, WMAP and VAR_* are not), and integer data are
flags (the OSIRIS quality extension, JWST DQ), which have no unit. Otherwise nothing is borrowed and
an extension that states no `BUNIT` reads "Unknown". The WCS is borrowed either way.

**A wavelength unit the file does not state is Unknown, never inferred.** `spectral_unit()`
returns a unit only when the header states one: in its own `CUNIT<n>` card, else in `units=` in
`WAT<n>_001`. The FITS default wcslib fills in for a standard spectral axis with no `CUNIT<n>`
(`m` for `WAVE`, `Hz` for `FREQ`) does not count. When `CUNIT<n>` is stated, the unit returned is
the one the WCS holds, because wcslib rescales a spectral axis to SI (`CUNIT1 = 'nm'` reads back as
`m`, in which the WCS's values are). With no stated unit, `ImageViewer.wavelength_unit()` is None:
the axis reads "Wavelength (Unknown)" in the header's own numbers and the Depth Plot's line list,
which is in µm, is not matched against it. A known unit is shown in µm
(`header_info.in_microns`); a spectral unit that is not a wavelength, such as the m/s of a `VELO`
axis, gets no wavelength axis and the line list says "Z-axis is not a wavelength". `wavelength_unit()`
gives the unit of the WCS's values, `displayed_wavelength()` is the one conversion to the numbers
shown, and `wavelength_unit_label()` the one label. The user can state the unit in the Depth
Plot's *Cube wavelength unit* combo, under Plot Axes rather than beside the line list because it is
the cube's unit (`ImageViewer.wavelength_unit_override`, set through
`set_wavelength_unit_override()`). The
choice describes the numbers the header writes, so they are shown as they are and labelled "Å, set
by you" (`displayed_wavelength()`); over a stated `CUNIT` it is scaled by
`header_info.header_value_scale` to the SI values wcslib holds. It is kept across loads, and cleared
by a load whose header states a unit other than the one it was chosen over.

The view supports rotation and flips (N up, E left); when mapping a screen coordinate back to
a numpy index or WCS position, apply the inverse transforms.

**That mapping lives in exactly one place: `pyql3/core/coords.py` (CRITICAL).** Use
`ImageViewer.orig_to_display()` / `.display_to_orig()`, or the pure functions behind them —
never re-derive the flip/`rot90` arithmetic inline. It was written three times before it was
written once, at three different levels of correctness, and the odd one out silently used one
axis length for both axes (`BUGS.md` B13/B14). `coords.orig_angle_to_display()` does the same
for directions, which a box's position angle or an arrow's heading needs: a flip maps `θ` to
`180 - θ` and each 90° step adds 90°, **in that order**, and `view_rotation` is added *after*
both because it transforms the already-flipped array. Composing that differently was `BUGS.md`
B20 — three copies that pointed the N/E compass backwards under a flip. Angles on screen come
from `ImageViewer.north_east_display_angles()`; do not re-derive them either.

"orig" means `transposed_data` indices, i.e. the coordinate along whichever FITS axis the AXIS
1/2/3 combos currently map to X or Y — that is what a WCS lookup wants. It is *not* `raw_data`
axis order. View rotation needs no arithmetic at all: `apply_view_rotation()` is a `QTransform`
on the ImageItem, so anything parented to the image inherits it. For the same reason, a position
in ImageItem coordinates is mapped to or from ViewBox coordinates only through
`ImageViewer.item_to_view()` / `view_to_item()`, never by treating the two as equal: R6, R29 and
R45 were each that mistake.

### Regions (CRITICAL)

ds9-style annotations — circle, box, arrow, text — drawn over the image, saved as YAML or ds9
`.reg`. The split is deliberate and load-bearing:

- **`pyql3/core/regions_model.py` / `regions_io.py` / `ds9_regions.py` import no Qt.** The model
  is the source of truth, the items on screen are derived from it, and the derivation runs one
  way only — except immediately after a drag, when the new geometry is read back. Keep new
  geometry, format and interop logic on the Qt-free side; it is what makes any of this testable
  without a display.
- **Region geometry is stored in orig coordinates, always** (see the previous section for what
  "orig" means). Display coordinates exist only inside `region_layer.py`, between a `coords`
  call and a `setPos`. A region written to a file in display coordinates would move when the user
  flipped the view.
- **A native file holds both frames, and `frame:` is always written.** The geometry is pixels and
  the `sky` block is degrees, so a file saved with a WCS carries every region twice; `frame:` says
  which is meant and is written even at its default (`regions_model.ALWAYS_WRITTEN`) so that
  "absent" never has to be read as "pixels".
- **The file records its own formatting in a `style:` block** — ds9's `global` line, in YAML. Every
  file states the colour, line width, dashing and font size that an omitted key means
  (`STYLE_FIELDS`, `default_style()`), and a region writes only what differs. Two reasons it is not
  just left implicit: a region that took the defaults would otherwise have its appearance decided
  by whatever this build defaults to when the file is read back, and editing the one block should
  restyle everything that did not override it, as editing ds9's `global` does. `_style_from_dict()`
  **raises** rather than collecting problems, unlike every other validator here — everything below
  the block is read against it, so a bad one makes the rest meaningless.
- **The `SkyAnchor` is read back, not just written.** `regions_io.placed_on_image()` places a
  `frame: sky` region from its RA/Dec on load, so a set drawn on one dither lands on the same stars
  in the next, as a ds9 `fk5` file does. Three consequences: the anchor is **recomputed** on save
  rather than preserved (the pixels are what the user dragged, so an anchor carried in from a file
  is stale the moment a region moves); a re-placement landing within `SAME_PLACE` of the stored
  pixels keeps the *stored* numbers, so loading onto the image a file was drawn on is exact rather
  than drifting by a WCS round trip per cycle; and `_from_sky_anchor()` is the inverse of
  `sky_anchor_for()` and has to stay that way.
- **When the two frames disagree, the user picks** — as ds9 asks. `load_regions(choose_frame=...)`
  takes the hook, `MainWindow.choose_region_frame` is it, and it is passed **only when `announce`**
  is set: a `--regions` startup takes the file's own frame rather than stopping on a modal. The
  question is skipped when both frames place everything within `SAME_PLACE`, which is every file
  loaded back onto its own image, and skipped when `frame=` names one outright. **A test that
  reaches the hook must stub it**, like `confirm_cli_install` — `QMessageBox.exec` blocks the suite.
- **Items are parented to the ImageItem**, so view rotation is inherited for free, and are
  removed with `ViewBox.removeItem()` — `setParentItem(None)` leaves them painted (`BUGS.md` B7).
  Anything that compares a region with the view goes through `item_to_view`: Zoom To through
  `RegionLayer.orig_to_view()`, label culling (both render modes) on each label's own anchor, not
  its region's centre, which a large circle's label is a radius away from. The overlay maps its
  anchors in one step with `ImageViewer.items_to_view()`; one `mapToView` per label cost 35 ms per
  redraw at 10,000 regions. The layer culls its labels again on `view_rotation_changed`, because a
  rotation moves them on screen without moving the view (`BUGS.md` R45); in aggregate mode only
  the labels are redrawn for it, since the shapes turn with the ImageItem.
- **A channel range is checked on every `plane_changed`**, not only on slider moves. Against a
  plane that combines channels — a Collapse Region range, or a Boxcar wider than one — a region
  shows if the two ranges overlap; on a 2-D image a channel range hides nothing
  (`RegionLayer._current_channel()` returns None there; `BUGS.md` R36, R63). The aggregate overlay
  is redrawn on `plane_changed` only when those channels changed, so a flip or a load draws it
  once when the channel on screen is unchanged.
- **Colour names are kept, not resolved, in the model and in files.** `resolve_color()` maps them
  to ds9's RGB only for painting, because ds9's `green` is `#00ff00` while Qt's is `#008000`;
  resolving early would write a colour ds9 never chose back out to a `.reg`.
- **Two render modes.** Above `INTERACTIVE_LIMIT` (500) regions the layer stops building one ROI
  per region and draws the set as a few aggregate items. Anything that assumes an `_Entry` has a
  `handle` must tolerate its absence. The measurements behind the limit are in the constant's
  docstring; do not raise it without repeating them.
- **`_region_list_dialog` is in `TOOL_DIALOG_ATTRS`**, like every other tool dialog.

Four Qt traps here each cost real time. They are not hypothetical:

1. **Angle conventions do not transfer between `pxMode=True` and transformed items.** An
   `ArrowItem` with `pxMode=True` gets `ItemIgnoresTransformations`, so its angle is in screen
   coordinates, where y runs *down* — the same number that aims a data-space line up aims the
   head down. `_arrow_head_angle()` is the conversion (`180 - direction`); note that a
   horizontal arrow is correct either way, so a test suite of horizontal cases proves nothing
   (`BUGS.md` M13).
2. **A modal `QMenu.exec` cannot be patched out in PySide6**, so a test that opens a context
   menu hangs the suite for its full timeout. Every context menu is therefore split into a
   `build_*_menu()` that returns the `QMenu` and a `show_*_menu()` that calls `exec` on it;
   tests call the builder. This was learned twice — the region menu, then the catalog menu.
3. **Dropping the last Python reference to a `QGraphicsItem` can segfault**, because GC may run
   during the construction of its replacement. `restyle()` therefore mutates items in place, and
   anything genuinely discarded goes to the `_retired` list, cleared from a
   `QTimer.singleShot(0, ...)` once the event loop is back. **Nothing may release that list from
   inside a destroy path** — flushing it at the start of `_destroy_items` meant `clear()`'s loop
   freed each entry's items while building the next, and put the crash straight back (`BUGS.md`
   M18). The release forces a `gc.collect()`, because these items are in reference cycles and
   dropping the list frees nothing on its own. The tool dialogs' counterpart,
   `BaseToolDialog._drop_retired_rois`, goes one step further and `shiboken6.delete`s each
   still-valid retired ROI *before* the `gc.collect()`. Left to the collector, freeing retired ROIs
   segfaulted inside `gc.collect()`, and whether it did depended on unrelated allocations (one extra
   `QLabel` in a dialog was enough; `BUGS.md` M31). lldb showed `callCppDestructor<QGraphicsObject>`
   called on a bad pointer; in the region layer's crashing collection the only `QGraphicsItem`s
   were the retired ROIs and their handles (`BUGS.md` R89). Whose pointer was bad, and why, is not
   established; deleting first means the collector never destroys a C++ object.
   `region_layer._drop_retired` does the same since the region layer showed the crash too; both use
   `pyql3/gui/scene_items.py` (`delete_items`).
   **Tool dialogs release `_retired_rois` synchronously, at the end of
   `BaseToolDialog.tear_down()`**, not from the timer. That is required: `MainWindow._open_tool()`
   deletes a hidden dialog it replaces, and a timer that fires after the dialog is gone never runs,
   which would leave the ROIs to the collector. It is not the pattern forbidden above, because
   nothing is built during the release — `_open_tool()` builds the replacement after `tear_down()`
   has returned. The region layer's rule is unchanged: `_retired` is released only from the timer.
   `retire_roi()` and the region layer's `_remove_from_scene()` also clear
   `GraphicsScene.dragItem` and `lastHoverEvent` when they name the item or one of its handles
   (`scene_items.release_scene_references`), so a deleted item is not reachable from the scene.
4. **`GraphicsScene.addParentContextMenus` walks up to the ImageItem**, whose `getContextMenus()`
   returns `[None]`, and a right-click then raises. `RegionItemInteraction.raiseContextMenu`
   overrides that walk (`BUGS.md` M12). The same mixin must check for an inherited
   `mouseClickEvent` before calling it — `pg.TextItem` has none (`BUGS.md` M16).

**Overlay label policy lives in `pyql3/gui/label_policy.py`, and only the policy (CRITICAL).**
Culling to the visible rect, hiding text while panning, and refusing to build an enormous set at
once are shared by the region layer and `plot_catalog.py`: the redraw delay, the cull itself
(`labels_in_view`, a rect test on anchors that each overlay has mapped into the view with
`ImageViewer.items_to_view`, grown by `grown_for_labels`) and the safety ceiling
(`LabelDensityGuard`) are defined there once. They were
written twice before, and the second copy was already missing the margin and the ceiling entirely —
the same drift `coords.py` exists to prevent. The module holds no Qt import, so it is testable
without a display (`tests/test_label_policy.py`).

**Making the items stays with each tool, and the two differ on purpose.** A region's label carries
that region's colour, font and offset; a catalog's carries a column value. More importantly the
*lifecycle* differs: a region's label is built once with its region and then only toggled, because
the region count is whatever the user drew — while a catalog is asked for one label per row, 66,196
in the reported case, so `plot_catalog` keeps a **pool sized to what is in view** and re-texts it.
Building one item per row would move the blowup from pan time to load time. Each tool passes its own
ceiling, because their per-label costs differ: `LABEL_SAFETY_LIMIT` for regions,
`CATALOG_LABEL_LIMIT` for the catalog. That cost is **superlinear** in the items already in the
scene — 0.22 ms per label at 1000, 0.85 ms at 20,000, because each `addItem` walks the ViewBox —
which is why a ceiling is needed at all, and why reusing items beats rebuilding them
(`BUGS.md` M30).

#### ds9 `.reg` interop, verified in ds9 itself

Checked empirically against ds9 with a one-construct-per-file ladder (2026-08-05), because ds9
rejects a region file *whole* — one bad line and nothing loads, with no indication of which line.
Do not "simplify" the writer past any of these:

- **A bare `vector(...)` is a syntax error that kills the file.** `vector` exists only in ds9's
  `#`-prefixed annotation grammar, so an arrow must be written `# vector(x,y,len,angle) vector=1`.
  (`text` is a real shape keyword, so *both* `text(...)` and `# text(...)` load.)
- **A comment whose first character after `#` is `-` kills the file** — `-` is ds9's exclude
  prefix, so `# --- section ---` parses as an excluded region. Parentheses in a comment are fine.
  Never emit a comment starting with a dash.
- `textangle=` on a text region round-trips, so a rotated label survives.
- **`regions.serialize(format='ds9')` output loads in ds9 unmodified**, header line included, so
  the export path is that output plus appended `# vector(...)` lines and a provenance comment —
  no rewriting.
- `regions` 0.12 **drops `vector(...)` silently on read** (a `UserWarning`, no error), which is
  why the arrow reader is ours. Its `PixCoord` is 0-based and it applies ds9's 1-based shift
  itself; do not shift again.
- **`regions` refuses the `physical` frame, and refusing a frame there drops every shape after
  it** — one word on line 4 of an ordinary GC region file cost all 29 regions (`BUGS.md` M20). The
  reader rewrites `physical` to `image` before parsing; the two differ only through IRAF `LTV`/
  `LTM` keywords, which no file we handle carries.
- **Arrows are hand-parsed, so every convention the library handles for shapes has to be handled
  again here** — and each one was wrong until it was tested (`BUGS.md` M21). ds9 declares seven
  sky frame names, writes equatorial positions in **sexagesimal hours** (`17:45:40` is 266.4°, not
  17.8°) but galactic and ecliptic sexagesimal in degrees, and puts the unit inside the length
  (`15"`, `15'`, `15d`, `15p`). Reading a tail as ICRS degrees whatever the file said put a `fk4`
  arrow 7000 px from the circle beside it. `_sky_tail()` and `_length_in_pixels()` own those
  rules; `CelestialMap.from_skycoord()` does the frame conversion, through the same
  `skycoord_to_pixel` the library uses, which is what keeps the two paths on one pixel.
  `_SKY_FRAMES` mirrors `regions.io.ds9.core.ds9_frame_map` and a test asserts they stay equal.
- **What the library refuses, it announces with `warnings.warn`** — invisible to a GUI user, and
  shown once per process, so the second attempt at a bad file is silent. `_parse_shapes()` catches
  those warnings into the `Report`; do not let a parse run outside it. A `#`-prefixed annotation
  (`compass`, `ruler`, ...) is worse still — a plain comment to `regions`, so no region and no
  warning — and is counted by `_report_annotations()` for the same reason.
- ds9's `image` frame means FITS axes 1 and 2, which for an OSIRIS cube is not what is displayed
  — so export writes sky coordinates whenever image coordinates would not line up in ds9
  (`frame="auto"`; `"image"` and `"sky"` force it, and say so in the report). Anything that
  cannot cross, either way, goes into a `Report` shown to the user rather than being dropped in
  silence.
- **In a sky frame ds9 measures angles from the sky axes**, so a box or arrow angle is not the
  image-frame angle: they differ by the field rotation. `pixel_angle_to_sky()` /
  `sky_angle_to_pixel()` convert, and a rotated field is the only case where the difference
  shows — check any change against `check_rotated.fits` in the ladder, not a north-up frame.

The ladder that established this is generated by
`agent_tests/probes/make_ds9_check_ladder.py` (scratch, not part of the suite); re-run it in ds9
if the writer changes shape.

### Qt slot gotcha

`QAction.triggered` is `triggered(bool checked=False)`, and PySide6 picks that overload for
any single-argument slot. A slot like `open_depth_plot(self, initial_center=None)` therefore
receives `False` from the menu bar but a real `(x, y)` from a context menu. Use
`as_center()` in `base_tool.py` to normalize such arguments (see `BUGS.md` B0).

### Qt object lifetime (CRITICAL)

**`close()` hides a window; it does not destroy it, and nothing else will.** Every
`lambda: self.something()` connected to a QAction is held by that action, which is held by a menu,
which is held by the widget — a reference cycle that runs through C++, where Python's collector
cannot follow it. Measured: **415 widgets survive every `MainWindow`, permanently**, and
`gc.collect()` reclaims none of them.

Two consequences, both real and both fixed:

- **A closed window kept its whole cube** — `raw_data`, `transposed_data` and `display_data` —
  for the life of the process (`BUGS.md` M19). `MainWindow.closeEvent()` therefore calls
  `ImageViewer.release_data()`. Anything else a window holds that is worth real memory belongs
  there too; do not assume Qt will collect it.
- **The test suite paid for it quadratically.** `ViewBox.__init__` calls
  `ViewBox.updateAllViewLists()`, which walks *every* live ViewBox in the process, so building a
  window costs O(live views): 0.037 s at ten leaked windows, 0.090 s at a hundred, 0.129 s at 150.
  `tests/conftest.py::destroy_leftover_windows` is an autouse fixture that `shiboken6.delete`s any
  window a test leaves behind — 150 s → 60 s for the suite.

The sweeper destroys **whole windows only** (`QMainWindow` and `ImageViewer`). pyqtgraph leaves
parentless helper widgets — context menus, a colour dialog, their frames — attached to a scene
that is still alive; deleting one of those and then its window segfaults, and deleting a
`ViewBoxMenu` makes pyqtgraph trip over its own deleted combo box the next time a view list
updates. Leave them.

`deleteLater()` is not a substitute: it frees under half the widgets, saved 11 MB of 193 in a
ten-window measurement, and leaves the Python wrapper (and so the cube) alive.

## UI design

The original IDL interface was highly utilitarian, with specific controls ("Set", "Fix",
"Log" for axes). Respect the modern PySide6/Qt aesthetic, but explicitly retain the layout
density and specific tool controls of the `ql2` reference implementation — dual axes for
world coordinates, coordinate trackers, locked scaling overrides — unless asked otherwise.

## Packaging (CRITICAL)

`QuickLook3.spec` is the single source of truth for PyInstaller across macOS, Linux, and
Windows. Always build with `uv run pyinstaller --noconfirm QuickLook3.spec` — **never** raw
`pyinstaller main.py` or inline `--add-data` parameters, in local scripts or in
`.github/workflows/release.yml`.

- Line lists (`pyql3/data/*.txt`), `pyql3/icon.png`, `cmcrameri` colormaps, `photutils`, and
  `regions` must be registered in the spec via `datas` / `collect_all()`. `regions` carries
  six compiled `_geometry` extension modules (`regions` 0.12) that PyInstaller's analysis does
  not find on its own, so ds9 import/export fails only in the frozen build if they are dropped.
  They arrive through `collect_all`'s **hiddenimports**, not its binaries — `collect_dynamic_libs`
  returns nothing for them.
- Both `build_app.sh` and CI run an explicit verification step that greps `dist/` for the
  bundled `*lines.txt`, `cmcrameri` and `regions/_geometry` assets before archiving. Keep that
  check in place, and keep it matching on the **extension suffix**
  (`importlib.machinery.EXTENSION_SUFFIXES`; `.pyd` on Windows, `.so` elsewhere): a bare
  `regions/_geometry/*` glob is satisfied by the `__init__.py` and the `tests/` directory
  collected alongside them, so it passes on a build containing no compiled extension at all.
- Runtime resource lookups must go through `pyql3.get_resource_path()`, which handles the
  frozen `sys._MEIPASS` case.
- Headless Linux CI needs system libs (`libegl1`, `libgl1`, `libglx-mesa0`, `libgl1-mesa-dri`,
  `libxcb-cursor0`, `libxkbcommon-x11-0`, `libdbus-1-3`, `xvfb`) and runs pytest under
  `xvfb-run --auto-servernum` with `QT_QPA_PLATFORM=offscreen`.

### Desktop and shell integration (macOS)

**`argv_emulation` stays `False`.** Finder does not pass a double-clicked file in `argv`; it
sends an open-document Apple Event that Qt delivers as `QEvent.Type.FileOpen`.
`pyql3/gui/file_open.py` handles that event — `main.py` installs it on the `QApplication`
*before* `MainWindow` exists, because a cold launch delivers the event first, and the handler
queues paths until `set_loader()` arrives. PyInstaller's `argv_emulation` is the competing
mechanism: it rewrites `sys.argv` from the same event, covers only the launching document,
and has a history of hanging. Turning it on duplicates the work and breaks the queue.

The `BUNDLE` call also owns two Finder-visible facts: a reverse-DNS `bundle_identifier`
(PyInstaller's `None` default writes the bare string `QuickLook3`, which makes `open -b` and
LaunchServices registration unreliable) and `CFBundleDocumentTypes`, which is what puts
QuickLook 3 in the **Open With** menu. `.fits.gz` cannot be listed there — LaunchServices
matches only the final extension, so claiming it means claiming every `.gz`.

`pyql3/services/cli_install.py` writes the `quicklook3` launcher (**Help ➔ Install 'quicklook3' Command
Line Tool...**, or `--install-cli`).

**The menu action must not install anything before the user agrees.** `plan()` computes the
target path and validates every precondition without touching the filesystem; the dialog is
rendered from `describe_plan()`, which states the file to be created, what it will run, how
to run the command, and the `rm` that undoes it; only then is `install(plan_=...)` called
with the very plan that was shown. Keep planning side-effect-free — a menu item that drops an
executable onto `PATH` on a single click, before saying where, is not acceptable. `--install-cli`
skips the prompt because typing the flag is the consent, but prints the same summary.

Any test that calls `MainWindow.install_cli_tool()` must stub `confirm_cli_install`, or the
real modal dialog blocks the suite forever. **That is not sufficient on Windows**, where
`plan()` refuses before the confirmation hook is ever reached and the refusal goes to
`QMessageBox.warning` — a modal with nothing to close it, offscreen or not. A test driving
the real `plan()` therefore belongs behind `tests/test_file_open.py::shell_launcher_only` (or
the module-level skip in `tests/test_cli_install.py`); a test of the Windows path must stub
`QMessageBox.warning` as well. This cost a six-hour Windows CI run (`BUGS.md` M22), which is
also why `release.yml` now sets `timeout-minutes` on the job and on each test step — keep them.

Three further constraints were each learned from a real failure, so do not simplify them away:

- **Never `resolve()` `sys.executable`.** A venv's `python` is a symlink to the base
  interpreter; a launcher pointing at the resolved path runs an interpreter with none of the
  project's dependencies and dies on `import PySide6`.
- **A quarantined bundle is killed with SIGKILL and no output**, so a launcher for a
  downloaded `.app` embeds an `xattr -p com.apple.quarantine` check. Without it, `quicklook3` looks
  like it does nothing at all.
- **Read-only alone does not mean "disk image".** Since Big Sur the macOS system volume is
  sealed, so `/bin` and friends report read-only while being permanent. Refusing to install
  requires read-only *and* a path under a mount root (`/Volumes`, `/media`, ...).

### Release workflow invariants

Three properties of `.github/workflows/release.yml` are deliberate. Preserve them:

- **Actions are pinned to commit SHAs**, with the version in a trailing comment. A floating
  `@v4` is mutable by whoever owns the action repository, and it runs in a job holding a
  token. Bump pins deliberately; do not "tidy" them back to tags.
- **`uv sync --frozen`**, never `uv add` — the build must come from the committed `uv.lock`.
  The workflow used to run `uv add --dev pyinstaller pillow`, which re-resolved and rewrote
  the lock, so published binaries could be built from unreviewed versions. Both packages are
  already in the locked `dev` group.
- **Least privilege**: top-level `permissions: contents: read`; only the `release` job
  escalates to `contents: write`.

Bundles are unsigned and un-notarized, so each release publishes `SHA256SUMS.txt` as the
only integrity check a user has. Keep that step.

## Repo conventions

- **Commit messages carry no AI attribution.** Do not append `Co-Authored-By: Claude ...`,
  "Generated with ...", or any similar trailer to commits or PR bodies.
- **This file is the canonical statement of the architecture, data-state, and WCS rules.**
  `docs/developer.md` is the human-facing guide on the published site; it links here for
  those invariants instead of restating them, and covers what this file does not (FitsReader
  reload semantics, the Windows file-locking asymmetry, z-slice and collapse helpers). Put a
  rule in exactly one of the two, never both.
- `BUGS.md` is a running code-review log with reproduction steps and fix status; `TODO.md`
  holds the feature backlog with a `# DONE` section appended to.
- `agent_tests/` is a scratch area of one-off exploratory scripts, not part of the suite —
  `pytest` only collects `tests/` (see `[tool.pytest.ini_options]`).
- `tests/conftest.py` provides `qapp`, synthetic `sample_2d_fits` / `sample_3d_fits` (built
  with a real OSIRIS-ordered WCS), and a `loaded_viewer` fixture — prefer these over
  hand-rolling FITS files in new tests.

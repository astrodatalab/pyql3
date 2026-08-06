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
uv run pytest -v                            # full test suite
uv run pytest tests/test_depth_plot.py -v   # one file
uv run pytest tests/test_cuts.py::test_name # one test
./build_app.sh                              # macOS/Linux bundle (.app + .dmg / .tar.gz)
build_app.bat                               # Windows bundle (.exe)
uv run mkdocs serve                         # docs site (mkdocs.yml)
```

Every test touches Qt. On a headless machine (or to keep windows from popping up during a
local run) prefix with `QT_QPA_PLATFORM=offscreen`.

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
exhausted.

`MainWindow` caches each tool as a `self._<name>_dialog` attribute and reopens it only if
not already visible. Those attribute names are listed once, in `MainWindow.TOOL_DIALOG_ATTRS`,
and two things walk the list: `update_tools_for_unit()` (calls each dialog's `update_plot()` /
`update_stats()` when the display unit changes between DN/s and Total DN, so labels and graphs
refresh instantly) and `close_tool_dialogs()` from `closeEvent`. **A new tool must be added to
`TOOL_DIALOG_ATTRS`** or it will silently go stale on a unit change *and* be left on screen
after its window closes.

### Multiple windows (CRITICAL)

Several `MainWindow`s are open at once — **File ➔ New Window**, **Open in New Window...**, the
Arithmetic tool's result, and one per file named on the command line. A window owns everything
it displays: its `FitsReader`, `ImageViewer`, tool dialogs and `DirectoryPoller`. Tools take the
viewer as a constructor argument (`base_tool.py`) and never look it up, so independence is
automatic — do not reintroduce a lookup that walks up to "the" main window.

Three things are process-wide and must stay that way:

- **`get_window_manager()`** (`pyql3/gui/window_manager.py`) is the window list, plus the
  most-recently-used order. A path arriving with no window attached (a Finder open-document
  event, `quicklook3 cube.fits` while the app is running) goes to `WindowManager.open_path()`,
  which targets the most recently used window and creates one if all are closed. **Never bind
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

The DN/s → Total DN conversion is exposed as the `data_multiplier` property
(`itime * coadds`); tools should multiply plotted values by it rather than recomputing.

### FITS axis / WCS conventions (CRITICAL)

OSIRIS cubes put wavelength on FITS axis 1, Dec on axis 2, RA on axis 3:

- `CTYPE1`: Wavelength (`WAVE` / `AWAV`)
- `CTYPE2`: Declination (`DEC--TAN`)
- `CTYPE3`: Right Ascension (`RA---TAN`)

`astropy.io.fits.getdata()` reverses these into C-contiguous order, so `data.shape` is
`(RA, DEC, WAVE)`. Other instruments put wavelength on axis 3 instead. **Never hardcode an
axis index** — parse `wcs.wcs.ctype[idx]` dynamically to identify WAVE/RA/DEC. `set_data()`
picks the default X-axis by sniffing whether `CTYPE1` contains `RA`.

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
on the ImageItem, so anything parented to the image inherits it.

### Qt slot gotcha

`QAction.triggered` is `triggered(bool checked=False)`, and PySide6 picks that overload for
any single-argument slot. A slot like `open_depth_plot(self, initial_center=None)` therefore
receives `False` from the menu bar but a real `(x, y)` from a context menu. Use
`as_center()` in `base_tool.py` to normalize such arguments (see `BUGS.md` B0).

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

- Line lists (`pyql3/data/*.txt`), `pyql3/icon.png`, `cmcrameri` colormaps, and `photutils`
  must be registered in the spec via `datas` / `collect_all()`.
- Both `build_app.sh` and CI run an explicit verification step that greps `dist/` for the
  bundled `*lines.txt` and `cmcrameri` assets before archiving. Keep that check in place.
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
real modal dialog blocks the suite forever.

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

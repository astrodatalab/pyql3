# AGENTS.md

Guidance for coding agents (Claude Code, and any tool that reads `AGENTS.md`) working in this
repository. `CLAUDE.md` and `.agents/AGENTS.md` are symlinks to this file.

`pyql3` / QuickLook 3 is a PySide6 + pyqtgraph viewer for integral field spectroscopy FITS
data, replacing the legacy IDL `ql2` GUI. It is tuned for Keck/OSIRIS cubes but must work for
other IFUs (JWST NIRSpec IFU, Gemini NIFS).

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
- **`DirectoryPoller`** (`pyql3/services/poller.py`) — watchdog observer that auto-loads new
  FITS files; **`ConfigManager`** (`services/config.py`) persists recent files to
  `~/.pyql3/config.json`.

`MainWindow` caches each tool as a `self._<name>_dialog` attribute and reopens it only if
not already visible. When the display unit changes (DN/s vs Total DN), `update_tools_for_unit()`
walks that hard-coded list of dialog attributes and calls each dialog's `update_plot()`, so
labels and graphs refresh instantly. **A new tool must be added to that list**
(`main_window.py:470`) or it will silently go stale.

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

## Repo conventions

- `docs/developer.md` restates the architecture and data-state rules for human readers; keep
  it in sync when those rules change.
- `BUGS.md` is a running code-review log with reproduction steps and fix status; `TODO.md`
  holds the feature backlog with a `# DONE` section appended to.
- `agent_tests/` is a scratch area of one-off exploratory scripts, not part of the suite —
  `pytest` only collects `tests/` (see `[tool.pytest.ini_options]`).
- `tests/conftest.py` provides `qapp`, synthetic `sample_2d_fits` / `sample_3d_fits` (built
  with a real OSIRIS-ordered WCS), and a `loaded_viewer` fixture — prefer these over
  hand-rolling FITS files in new tests.

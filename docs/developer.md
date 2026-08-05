# Developer & Architecture Guide

This document outlines key technical details, architectural decisions, and testing procedures for developers and automated coding agents working on `pyql3`.

---

## 🏗️ Core Architecture

```
pyql3/
├── core/
│   └── fits_reader.py        # FITS loading, WCS extraction, and multi-HDU management
├── analysis/
│   └── strehl.py             # Pupil mask / diffraction-limited PSF / Strehl computation
├── data/                     # Bundled spectral line lists (*.txt), see Packaging
├── gui/
│   ├── main_window.py        # Main PySide6 application window & menu dispatch
│   ├── viewers/
│   │   └── image_viewer.py   # 2D/3D image display, WCS, scaling, & colormaps
│   ├── tools/
│   │   ├── base_tool.py      # Base class for modeless tool dialogs
│   │   ├── depth_plot.py     # Spectral extraction & line list overlays
│   │   ├── cuts.py           # Spatial profile cuts
│   │   ├── fitting.py        # 2D Gaussian/Lorentzian/Moffat fitting
│   │   ├── photometry.py     # Aperture photometry
│   │   ├── statistics.py     # Region statistics
│   │   ├── strehl.py         # Strehl dialog (numerics live in analysis/strehl.py)
│   │   ├── arithmetic.py     # Image arithmetic between cubes/constants
│   │   ├── advanced_plots.py # 3D surface plot
│   │   ├── plot_catalog.py   # Source catalog overlay
│   │   └── rotate.py         # View rotation controls
│   └── dialogs/
│       ├── header_editor.py  # FITS header card editor
│       └── polling.py        # Directory polling setup dialog
└── services/
    ├── poller.py             # Directory scanning for new FITS files (NFS-safe)
    └── config.py             # ~/.pyql3/config.json, recent-files list
```

Tool dialogs embed their own `pyqtgraph.PlotWidget` rather than sharing a plot viewer class.

---

## ⚡ FITS Data Cubes and WCS Rules (CRITICAL)

!!! info "Canonical statement lives in `AGENTS.md`"
    The axis-order and data-state invariants are written down once, in
    [`AGENTS.md`](https://github.com/astrodatalab/pyql3/blob/main/AGENTS.md) — the file
    coding agents load automatically. They are summarised below for orientation only. **If
    the two ever disagree, `AGENTS.md` is authoritative** and this page needs updating.

### OSIRIS Datacube Axis Order
OSIRIS spectral datacubes map FITS Axis 1 to Wavelength (`WAVE` or `AWAV`), Axis 2 to
Declination (`DEC--TAN`), and Axis 3 to Right Ascension (`RA---TAN`). `astropy` reverses
that ordering into C-contiguous NumPy order, so `data.shape` is `(RA, DEC, WAVE)` —
wavelength is FITS axis 1 but the **last** NumPy axis.

### Instrument-Agnostic WCS Rule
Do **not** hardcode axis checks (such as assuming Axis 3 is wavelength). Datacubes from other instruments (e.g. JWST NIRSpec, Gemini NIFS) map wavelength to different axes. Always dynamically parse the `CTYPE` string using target axis indices (e.g., `wcs.wcs.ctype[z_idx]`).

### Reloading a File (live observing)

`FitsReader.load()` reopens the `HDUList` whenever the bytes on disk differ from the ones it
holds open — **including when the path is unchanged**. Staleness is keyed on
`(st_mtime_ns, st_size, st_ino)`, so an instrument or DRP that rewrites a path in place is
picked up, while a byte-identical reload reuses the handle. That reuse is load-bearing:
switching extensions goes through `load()`, and reopening every time would silently discard
Header Editor edits that have not been saved yet. Pass `force=True` for a guaranteed re-read
(**Display → Redisplay image** does).

!!! warning "`memmap=False` is deliberate"
    Files are opened with `memmap=False`. Reading through a mapping of a file the DRP is
    rewriting under us returns undefined data and can `SIGBUS` if the file shrinks, and
    `writeto()` back to the same path can fail while a mapping is open. The consequence for
    new code: **never probe `hdu.data` to inspect an extension** — that reads it into
    memory. Test the header instead, as `FitsReader._is_displayable()` does.

!!! danger "Windows: an open file cannot be unlinked or replaced"
    Windows opens files without `FILE_SHARE_DELETE`, so while we hold a FITS file open **no
    one** — not us, not another process — can delete or rename over that path. Writing
    *into* the existing file is fine. Two consequences:

    * **Never write over an open path with `writeto(..., overwrite=True)`.** astropy
      implements overwrite as `os.remove()` + create, which is refused. `FitsReader.save()`
      therefore materialises every HDU, closes the handle, writes a sibling temp file and
      swaps it in with `os.replace()`. Materialising *before* closing is required — lazy
      HDUs cannot be read once the handle is gone.
    * **In tests, never simulate an external rewrite with `writeto(overwrite=True)`** while
      anything holds the file open. Use the `rewrite_fits_in_place` fixture from
      `tests/conftest.py`; it writes with `r+b` and bumps the mtime explicitly, because the
      Windows clock ticks roughly every 15 ms and two writes inside one tick can share an
      mtime and defeat the staleness check.

    This asymmetry is only tested on Windows CI, so a change here can pass locally on macOS
    or Linux and fail the release build. See **B18** and **B19** in `BUGS.md`.

`_is_displayable(hdu)` (`is_image` **and** `NAXIS > 0`) is the single definition of an
extension the viewer can show, used both by `load()` and by `get_image_extensions()`, which
populates the Extension combo. `get_all_extensions()` is a different question and still lists
tables. A `BINTABLE` has `data is not None`, so that is never the right test.

### Internal Data State vs. Display Data State (CRITICAL)

`ImageViewer` keeps **three** arrays, not two — `raw_data`, `transposed_data`, and
`display_data`. Confusing them corrupts output files. The definitions are stated once, in
[`AGENTS.md`](https://github.com/astrodatalab/pyql3/blob/main/AGENTS.md#data-state-raw_data-vs-transposed_data-vs-display_data-critical).

!!! danger "Data State Safety Rule"
    All analytical calculations, data exports, and FITS header writes **must** operate on
    `raw_data`. Using `transposed_data` or `display_data` instead outputs cubes with
    permanently swapped physical axes and broken WCS headers!

### The Current Z Slice

`ImageViewer.slider_slice` is the single source of truth for which plane of a cube is on
screen. The two directions are kept in sync automatically: `on_slider_changed` drives
`imv.setCurrentIndex`, and `on_imv_time_changed` mirrors a user drag of the pyqtgraph
timeline back onto the slider. Both are wrapped in the `_syncing_slice` guard, because
`imv.setImage()` emits a spurious `sigTimeChanged(0)` that would otherwise reset the
slider on every redisplay.

!!! warning "Use `current_z()`, not `imv.currentIndex`"
    Tools that need the displayed z index must call `self.image_viewer.current_z()`. The
    ImageView's own `currentIndex` is only maintained while a single slice is displayed —
    the Boxcar and collapse-range paths render through `bypass_imv=True` and never update
    it, so it goes stale. `current_z()` returns the slider value in slice/boxcar mode and
    the middle of the range in collapse mode, clamped to the cube.

### Analysing the Displayed Plane

In **Boxcar** or **Z Range** mode the screen shows a *collapsed* plane that exists in no
single channel of the cube, so no `cube[z]` is the right data. Two accessors cover this:

- **`current_plane()`** — the 2-D plane on screen, oriented like `display_data` but
  **without** the DN multiplier. Use it when the caller multiplies by `data_multiplier`
  itself (as the Depth Plot does at plot time); using `display_data` there would apply the
  multiplier twice.
- **`display_data`** — the same plane *with* the multiplier already folded in. Tools that
  plot it directly want this, and because it is 2-D whenever a collapse is displayed, the
  common `if img.ndim == 3: img = img[...]` idiom is already correct.

The z-collapse arithmetic and range handling live in one place each, and new code should
reuse them rather than re-deriving the range:

| helper | purpose |
|---|---|
| `clamp_z_range(zmin, zmax, write_back=False)` | clamp **both** ends to the cube and order them |
| `z_range_from_fields(write_back=False)` | the Z Min / Z Max boxes, parsed and clamped; `None` if unparsable |
| `boxcar_width()` / `boxcar_range(z)` | the Boxcar setting and the window it averages |
| `collapse_plane(zmin, zmax, method=None)` | Median / Mean / Sum collapse over an inclusive range |
| `apply_spatial_transforms(arr)` | the flip/rotation half of `apply_transforms`, as a pure function on a 2-D plane or 3-D cube |

!!! note "Empty and dead planes"
    Clamping both ends matters: a reversed range slices an empty subcube and the
    nan-reductions then return an all-NaN plane. `update_image_display` detects a plane with
    no finite pixels, renders an empty frame with fixed `(0, 1)` levels instead of letting
    pyqtgraph raise `Cannot set range [nan, nan]`, and `update_slice_info` appends
    *"no valid data"* to the slice label so a dead channel is not mistaken for a display bug.

---

## 🧪 Running Unit Tests

QuickLook 3 uses `pytest` for regression testing. Run the test suite using `uv`:

```bash
uv run pytest -v
```

### Test Organization (`tests/`)
- `tests/test_fits_reader.py`: FITS loading, WCS extraction, multi-extension headers, in-place reload/staleness, and OSIRIS axis mapping.
- `tests/test_image_viewer.py`: `raw_data` / `transposed_data` / `display_data` separation, view rotations, display scaling, colormaps, and z-slice plane accessors.
- `tests/test_depth_plot.py`: Spectrum extraction, background subtraction, line list parsing, LaTeX label formatting, and Y-auto scaling.
- `tests/test_analysis_tools.py`: Smoke coverage that each analysis dialog opens and computes — cuts, fitting, statistics, photometry, Strehl, arithmetic, surface plots.
- `tests/test_cuts.py`: Diagonal/linear cut ROI ↔ spinbox round-tripping, cut width, and extraction on cubes.
- `tests/test_plot_catalog.py`: Catalog marker and text-label lifecycle — removal on close, no accumulation across open/close, idempotent and teardown-safe close — plus FITS-table ingest: extension enumeration and selection, vector-column and masked-coordinate handling, and WCS round-tripping of RA/Dec columns.
- `tests/test_menu_actions.py`: The `QAction.triggered` bool-vs-coordinate slot hazard and `as_center()` coercion (see the Qt slot gotcha in `AGENTS.md`).
- `tests/test_main_window_and_poller.py`: `MainWindow` tool lifecycle, 2D guards, and `DirectoryPoller` service.
- `tests/test_poller.py`: Settle detection for files still being written, burst coalescing, and the auto-load retry/backoff path.
- `tests/test_packaging_assets.py`: Bundled line lists and `cmcrameri` colormaps are present, and `QuickLook3.spec` registers every asset, the bundle identifier, and the FITS document types.
- `tests/test_cli_install.py`: The `quicklook3` launcher — side-effect-free planning, argument forwarding and quoting, venv interpreter preservation, quarantine guard, disk-image and foreign-file refusals, install directory choice.
- `tests/test_file_open.py`: Finder open-document routing (queueing before the window exists, real `QFileOpenEvent` delivery) and the Install Command Line Tool menu action, including that declining the confirmation writes nothing.

### Tests that need real instrument data

A few tests exercise a genuine OSIRIS cube, because the synthetic fixtures are built to the
same axis convention the code assumes and so cannot catch an axis regression. They **skip**
unless `PYQL3_TEST_CUBE` points at one:

```bash
export PYQL3_TEST_CUBE="$HOME/path/to/s150531_a025002_Kn5_035.fits"
uv run pytest -v
```

They always skip in CI, where no such cube exists. A path that is set but unreadable warns
rather than skipping silently, so a typo cannot masquerade as "not configured".

---

## 📦 Building Application Packages

Build standalone application bundles for macOS (`.app` / `.dmg`) or Windows (`.exe`):

```bash
# macOS Build (.app and .dmg)
./build_app.sh

# Windows Build (.exe)
build_app.bat
```

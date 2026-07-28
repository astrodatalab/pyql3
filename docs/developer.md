# Developer & Architecture Guide

This document outlines key technical details, architectural decisions, and testing procedures for developers and automated coding agents working on `pyql3`.

---

## 🏗️ Core Architecture

```
pyql3/
├── core/
│   └── fits_reader.py       # FITS loading, WCS extraction, and multi-HDU management
├── gui/
│   ├── main_window.py       # Main PySide6 application window & menu dispatch
│   ├── viewers/
│   │   ├── image_viewer.py  # 2D/3D image display, WCS, scaling, & colormaps
│   │   └── plot_viewer.py   # 1D line plot viewer
│   ├── tools/
│   │   ├── depth_plot.py    # Spectral extraction & line list overlays
│   │   ├── cuts.py          # Spatial profile cuts
│   │   ├── fitting.py       # 2D Gaussian/Lorentzian/Moffat fitting
│   │   ├── photometry.py    # Aperture photometry
│   │   ├── strehl.py        # Strehl ratio calculation
│   │   └── base_tool.py     # Base class for modeless tool dialogs
│   └── dialogs/
│       ├── header_editor.py # FITS header card editor
│       └── polling.py       # Directory polling setup dialog
└── services/
    └── poller.py            # Watchdog filesystem monitoring service
```

---

## ⚡ FITS Data Cubes and WCS Rules (CRITICAL)

### OSIRIS Datacube Axis Order
OSIRIS spectral datacubes map FITS Axis 1 to Wavelength (`WAVE` or `AWAV`), Axis 2 to Declination (`DEC--TAN`), and Axis 3 to Right Ascension (`RA---TAN`).

When loaded via `astropy.io.fits.getdata()`, NumPy reverses the axes into C-contiguous order:
- `data.shape[0]`: FITS Axis 3 (`RA`)
- `data.shape[1]`: FITS Axis 2 (`DEC`)
- `data.shape[2]`: FITS Axis 1 (`WAVE`)

### Instrument-Agnostic WCS Rule
Do **not** hardcode axis checks (such as assuming Axis 3 is wavelength). Datacubes from other instruments (e.g. JWST NIRSpec, Gemini NIFS) map wavelength to different axes. Always dynamically parse the `CTYPE` string using target axis indices (e.g., `wcs.wcs.ctype[z_idx]`).

### Internal Data State vs. Display Data State (CRITICAL)
- **`self.image_viewer.raw_data`**: Retains the original, un-transposed FITS array shape and orientation. All analytical calculations, data exports, or FITS header writes **must** operate on `raw_data`.
- **`self.image_viewer.transposed_data`**: Represents the data formatted for `pyqtgraph.ImageView`. Axes are transposed for compatibility with the PyQtGraph plotting engine.

!!! danger "Data State Safety Rule"
    Never use `transposed_data` to perform analytical calculations that save FITS outputs back to memory or disk. Doing so will output cubes with permanently swapped physical axes and broken WCS headers!

---

## 🧪 Running Unit Tests

QuickLook 3 uses `pytest` for regression testing. Run the test suite using `uv`:

```bash
uv run pytest -v
```

### Test Organization (`tests/`)
- `tests/test_fits_reader.py`: FITS loading, WCS extraction, multi-extension headers, and OSIRIS axis mapping.
- `tests/test_image_viewer.py`: `raw_data` vs `transposed_data` separation, view rotations, display scaling, and colormaps.
- `tests/test_depth_plot.py`: Spectrum extraction, background subtraction, line list parsing, LaTeX label formatting, and Y-auto scaling.
- `tests/test_analysis_tools.py`: Profile cuts, 2D peak fitting, statistics, photometry, Strehl ratio, and surface plots.
- `tests/test_main_window_and_poller.py`: `MainWindow` tool lifecycle, 2D guards, and `DirectoryPoller` service.

---

## 📦 Building Application Packages

Build standalone application bundles for macOS (`.app` / `.dmg`) or Windows (`.exe`):

```bash
# macOS Build (.app and .dmg)
./build_app.sh

# Windows Build (.exe)
build_app.bat
```

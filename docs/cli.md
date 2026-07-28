# Command Line & Live Directory Polling Guide

QuickLook 3 can be launched directly from the terminal or integrated into automated observing scripts with flexible command-line flags.

---

## 🚀 Command Line Flags

Launch PyQL3 with command-line arguments to automatically open specific FITS files, set display scaling, apply colormaps, or start directory polling:

```bash
uv run python main.py [OPTIONS] [FILEPATH]
```

### Options Reference

| Flag | Description | Default | Example |
| :--- | :--- | :--- | :--- |
| `filepath` | Path to a 2D or 3D FITS file to load immediately upon launch | `None` | `uv run python main.py data.fits` |
| `--poll-dir` | Path to directory to automatically monitor for new FITS files | `None` | `--poll-dir ~/data/observing_run` |
| `--scale` | Display scaling algorithm (`linear`, `log`, `sqrt`, `asinh`, `histeq`) | `linear` | `--scale log` |
| `--cmap` | Default colormap name (e.g. `viridis`, `cmc.oslo`, `grey`, `coolwarm`) | `cmc.oslo` | `--cmap viridis` |
| `--rot` | Image rotation angle in degrees (`0`, `90`, `180`, `270`) | `0` | `--rot 90` |
| `--flip` | Flip image horizontally | `False` | `--flip` |
| `--ext` | Initial FITS HDU extension index to load | `0` | `--ext 1` |
| `--collapse-mode` | Z-axis collapsing mode (`median`, `mean`, `sum`) | `median` | `--collapse-mode mean` |

---

## 📡 Live Directory Polling Workflow

During observing runs at the telescope, raw data cubes or reduced frames are written continuously to disk. QuickLook 3 features a background directory polling service built on `watchdog`.

### Enabling Directory Polling

#### Option A: Via Command Line
Start monitoring a folder immediately when launching PyQL3:

```bash
uv run python main.py --poll-dir /path/to/raw_data
```

#### Option B: Via GUI Menu
1. Open QuickLook 3.
2. Select **File ➔ Directory Polling...** from the top menu bar.
3. Click **Browse...** to pick the watch directory.
4. Click **Start Polling**.

### Behavior
- When a new file ending in `.fits` or `.fit` is created or moved into the watched directory, QuickLook 3 automatically loads the new dataset in real-time.
- Active analytical tools (Depth Plot, Profile Cuts, Statistics) automatically refresh to reflect the newly detected file.

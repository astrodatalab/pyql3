# QuickLook 3 (Python QuickLook v2)

QuickLook 3 is a modern, high-performance Python/Qt-based application designed to replace the legacy IDL `ql2` GUI for viewing and analyzing FITS data—specifically engineered for the OSIRIS instrument at the Keck Observatory.

It provides a rich graphical interface to interactively visualize both 2D images and 3D data cubes with real-time astronomical coordinate mapping, scaling, and advanced data extraction tools.

## Features

- **High-Performance Rendering**: Built on PySide6 and pyqtgraph for buttery smooth visualization of large FITS data cubes.
- **3D Cube Visualization**: View volumetric FITS cubes with an interactive slider. Extract depth spectra from specific spatial pixels.
- **Z-Axis Collapsing**: Collapse 3D ranges into 2D display slices using Median, Mean, or Sum algorithms on the fly.
- **Advanced Scaling & Displays**: Includes interactive Linear, Logarithmic, Square Root, AsinH, and Histogram Equalization scaling. Supports instant color map inversion and position angle compass overlays.
- **Astronomical Coordinates**: Seamless `astropy.wcs` integration ensures precise pixel-to-world (RA/Dec) coordinate translations at your mouse pointer. 
- **Array Transformations**: Instantly rotate and flip the data array for visual alignment while preserving perfect spatial coordinate integrity.
- **Analysis Tools**: Features built-in region cuts (horizontal, vertical, arbitrary lines), SNR estimates, Encircled Energy plots, and 2D Peak Fitting.
- **Live File Polling**: Monitor a directory for incoming OSIRIS data files and automatically load them in real-time.
- **Header Editor**: View and modify FITS header cards directly in the UI.

## Installation

PyQL3 manages its dependencies seamlessly using `uv`, an extremely fast Python package and project manager. `uv` will automatically download the correct Python version and all required libraries (`PySide6`, `pyqtgraph`, `astropy`, `scipy`, etc.) so you don't have to worry about complex virtual environments.

### 1. Install `uv`

**For macOS and Linux:**
```bash
curl -LsSf https://astral.sh/uv/install.sh | sh
```

**For Windows:**
Open PowerShell and run:
```powershell
powershell -ExecutionPolicy ByPass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

### 2. Launch PyQL3

Clone or navigate to the `pyql3` repository in your terminal/command prompt:
```bash
cd pyql3
```

Run the application through `uv`. It will automatically fetch dependencies and launch the GUI:
```bash
uv run python main.py
```

## Usage

### Launching the Application
You can launch QuickLook 3 directly from the terminal. 

```bash
uv run python main.py
```
You can also pass a FITS file path as an argument to instantly open it upon launch:
```bash
uv run python main.py /path/to/your/file.fits
```

### Basic Navigation
* **Open File**: `File -> Open File`
* **Polling**: `File -> Poll Directory` to auto-load new FITS files arriving in a specific folder.
* **Header**: `File -> View/Edit Header`

### Visual Controls
* **Slices & Slabs**: The bottom control panel allows you to switch between viewing a single Z-slice or a collapsed Z-range of a 3D datacube. Use the slider to navigate through the cube depth.
* **Scaling**: Adjust scaling limits dynamically using the intensity histogram gradient on the right side of the image, or select scaling algorithms (Logarithmic, Negative, etc.) via the `Display -> Scaling` menu or the bottom left dropdown menu.
* **Rotation**: `Display -> Rotate Image...` lets you orient the image properly.
* **Data Units**: Toggle between native `As DN/s` and Total DN (`As Total DN`) through the `Display` menu.

### Analysis Tools
Found under the **Plot** menu bar:
* **Horizontal/Vertical/Any Cut**: Draw lines or drag crosshairs across the image to generate 1D profile cuts. The cut tools support variable thickness for boxcar averaging.
* **Depth**: Click anywhere on a 3D dataset to plot the 1D spectrum along the Z-axis.
* **Peak Fit / Encircle / SNR**: Draw a rectangular ROI over a source to instantly calculate 2D Gaussian statistics, Encircled Energy radial profiles, or Signal-to-Noise. 
* **Surface Plot**: Pop out a 3D OpenGL topographical surface render of the image data.

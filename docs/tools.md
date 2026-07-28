# Astronomical Tools & Algorithms Guide

QuickLook 3 provides a suite of analytical tools tailored for Integral Field Unit (IFU) data cubes and 2D astronomical images. This guide details the usage and mathematical algorithms underlying each tool.

---

## 1. Depth Plot & Spectral Line Lists

The **Depth Plot** tool extracts 1D spectra along the wavelength ($Z$) axis of a 3D data cube.

![Depth Plot](images/depth_plot_example.png)

### Algorithms

#### A. Spatial Region Spectrum Extraction
For a user-selected Region of Interest (ROI) containing spatial pixels $(x, y)$, the spectrum value $S(z)$ at wavelength channel $z$ is computed using one of three calculation methods:

* **Mean (Average)**:
  $$S(z) = \frac{1}{N} \sum_{(x,y) \in \text{ROI}} I(x, y, z)$$
* **Median**:
  $$S(z) = \text{median}_{(x,y) \in \text{ROI}} \left( I(x, y, z) \right)$$
* **Total (Sum)**:
  $$S(z) = \sum_{(x,y) \in \text{ROI}} I(x, y, z)$$

#### B. Background Subtraction
When **Enable Background Subtraction** is checked, a secondary background ROI is defined. The background spectrum $S_{\text{bg}}(z)$ is calculated using the selected background method (Median, Mean, or Total), and the background-subtracted spectrum is computed as:

$$S_{\text{subtracted}}(z) = S_{\text{signal}}(z) - S_{\text{bg}}(z)$$

#### C. Wavelength Primary X-Axis & Dual Axis Display
When FITS WCS wavelength information is available, the primary bottom X-axis displays physical wavelengths $\lambda$ ($\mu\text{m}$). The top X-axis (`PixelIndexAxis`) dynamically renders the corresponding 0-indexed channel slice numbers ($z \in [0, N-1]$). 

This setup allows users to click the **Export...** button on the top control bar (or right-click the plot and select **Export...**) to save wavelength-resolved spectra ($xData = \lambda$, $yData = \text{Intensity}$) to CSV, vector graphics, or images.

Line list wavelengths $\lambda$ are mapped directly onto the plot in wavelength units. Overlays positioned outside the dataset's visible spectral range are automatically filtered out.

#### D. Spectral Line Overlay & Staggering
- Overlaid spectral line labels feature 90° vertical rotation.
- To prevent overlapping labels when spectral lines are closely spaced, label heights are automatically staggered across 4 discrete vertical offset levels.
- LaTeX mathematical expressions enclosed in `$$...$$` (e.g. `$$H_\alpha$$`, `$$P_{2f}6.5$$`) are automatically parsed and formatted.

#### E. Exporting Spectra & Figures (CSV, PNG, SVG)

Astronomers can export the extracted 1D spectra and plot graphics by clicking the **Export...** button on the top control bar (or right-clicking anywhere on the plot canvas and selecting **Export...**):

1. **Exporting Wavelength & Intensity Data to CSV**:
   - In the Export dialog, select **CSV Exporter** under **Item to export**.
   - Select the target curve (`PlotItem`, `Source`, `Background`, or `Subtracted`).
   - Click **Export** to save a `.csv` data table. When WCS is present, the first column contains physical wavelengths ($\mu\text{m}$) and subsequent columns contain corresponding flux/intensity values ($DN$ or $DN/s$).

2. **Exporting High-Resolution Images (PNG, JPG)**:
   - Select **Image Exporter** in the Export dialog.
   - Specify output image pixel dimensions ($W \times H$).
   - Click **Export** to save publication-quality plot figures.

3. **Exporting Vector Graphics (SVG)**:
   - Select **SVG Exporter** for vector graphics suitable for publication figures and vector editing software (e.g., Inkscape, Illustrator).

---

## 2. Spatial Profile Cuts

The **Cut Plot** tool extracts 1D spatial intensity profiles across slices of the image plane.

### Cut Types
* **Horizontal Cut**: A slice across a fixed $Y$ pixel coordinate range.
* **Vertical Cut**: A slice across a fixed $X$ pixel coordinate range.
* **Diagonal Cut**: An arbitrary linear cut defined by two endpoints $(x_0, y_0)$ and $(x_1, y_1)$.

### Algorithm
For multi-pixel cut widths, intensity values perpendicular to the cut vector are averaged or collapsed using Median, Mean, or Total sum.

---

## 3. 2D Peak & Line Fitting

The **Peak Fit** tool fits analytical 2D spatial surface models to stars, emission lines, or compact sources within a selected ROI.

### Mathematical Models

#### A. 2D Elliptical Gaussian
$$f(x, y) = z_0 + A \exp\left( -\left[ a (x - x_0)^2 + 2b (x - x_0)(y - y_0) + c (y - y_0)^2 \right] \right)$$

where:
$$a = \frac{\cos^2\theta}{2\sigma_x^2} + \frac{\sin^2\theta}{2\sigma_y^2}$$

$$b = -\frac{\sin 2\theta}{4\sigma_x^2} + \frac{\sin 2\theta}{4\sigma_y^2}$$

$$c = \frac{\sin^2\theta}{2\sigma_x^2} + \frac{\cos^2\theta}{2\sigma_y^2}$$

The Full-Width at Half-Maximum (FWHM) along major and minor axes is given by:
$$\text{FWHM}_x = 2 \sqrt{2 \ln 2} \cdot \sigma_x \approx 2.35482 \cdot \sigma_x$$
$$\text{FWHM}_y = 2 \sqrt{2 \ln 2} \cdot \sigma_y \approx 2.35482 \cdot \sigma_y$$

#### B. 2D Lorentzian
$$f(x, y) = z_0 + \frac{A}{1 + \left(\frac{x_{\text{rot}}}{\gamma_x}\right)^2 + \left(\frac{y_{\text{rot}}}{\gamma_y}\right)^2}$$

#### C. 2D Moffat Profile
$$f(x, y) = z_0 + A \left[ 1 + \left(\frac{x_{\text{rot}}}{\alpha_x}\right)^2 + \left(\frac{y_{\text{rot}}}{\alpha_y}\right)^2 \right]^{-\beta}$$

### Optimization
Fits are computed using non-linear Levenberg-Marquardt least-squares optimization (`scipy.optimize.curve_fit`).

---

## 4. Aperture Photometry

The **Aperture Photometry** tool computes integrated stellar fluxes and background sky noise.

### Algorithm
1. **Aperture Flux ($F_{\text{raw}}$)**: Sum of pixel intensities inside a circular aperture of radius $r_{\text{ap}}$.
2. **Sky Background ($I_{\text{sky}}$)**: Median pixel intensity computed inside an annular sky region between inner radius $r_{\text{in}}$ and outer radius $r_{\text{out}}$.
3. **Net Subtracted Flux ($F_{\text{net}}$)**:
   $$F_{\text{net}} = F_{\text{raw}} - \pi r_{\text{ap}}^2 \times I_{\text{sky}}$$

---

## 5. Strehl Ratio & Optical PSF Analysis

The **Strehl Ratio** tool evaluates Adaptive Optics (AO) performance by comparing the observed stellar Point Spread Function (PSF) against an ideal, diffraction-limited Airy pattern for a telescope of diameter $D$ at wavelength $\lambda$.

### Mathematical Model
The theoretical diffraction-limited Airy intensity pattern $I(\theta)$ is:

$$I(\theta) = I_0 \left( \frac{2 J_1(\pi D \theta / \lambda)}{\pi D \theta / \lambda} \right)^2$$

where $J_1(x)$ is the Bessel function of the first kind of order 1.

### Strehl Ratio Definition
$$\text{Strehl} = \frac{\left( \frac{I_{\text{peak}}}{F_{\text{total}}} \right)_{\text{observed}}}{\left( \frac{I_{\text{peak}}}{F_{\text{total}}} \right)_{\text{theoretical}}}$$

---

## 6. Datacube Arithmetic

The **Datacube Arithmetic** tool performs element-wise scalar or cube-to-cube mathematical operations ($+$, $-$, $\times$, $\div$) between open datasets:

$$D_{\text{result}}(x, y, z) = D_1(x, y, z) \odot D_2(x, y, z)$$

---

## 7. Catalog Plotting & World Coordinate Overlay

The **Plot Catalog** tool overlays external astronomical source catalogs (CSV, TXT, DAT) directly onto the 2D image plane or collapsed datacube slices.

![Plot Catalog](images/catalog_tool.png)

### Features & Coordinate Modes

Astronomers can select between three coordinate modes:

1. **World Coordinates (RA / DEC)**: Celestial Right Ascension ($\text{RA}$) and Declination ($\text{DEC}$) in degrees or sexagesimal notation.
2. **FITS Array Pixels**: 0-indexed or 1-indexed raw FITS array pixel coordinates $(x_{\text{fits}}, y_{\text{fits}})$.
3. **Display Pixels**: Direct display viewport pixel coordinates.

### Algorithms

#### A. WCS Celestial-to-Pixel Transformation
For catalogs specified in celestial World Coordinates $(\text{RA}, \text{DEC})$, celestial coordinates are converted to raw FITS pixel coordinates $(x_{\text{fits}}, y_{\text{fits}})$ using the dataset's 2D/3D FITS World Coordinate System:

$$(x_{\text{fits}}, y_{\text{fits}}) = \text{WCS}^{-1}(\text{RA}, \text{DEC})$$

#### B. Display Rotation & Flip Mapping
To ensure source overlays track the image viewport when the astronomer rotates or flips the view, original FITS coordinates $(x_0, y_0)$ are mapped to current display coordinates $(x_{\text{disp}}, y_{\text{disp}})$ via the transformation algorithm:

1. **Horizontal Flip Adjustment**:
   If horizontal flipping is enabled:
   $$x_1 = N_x - 1 - x_0, \quad y_1 = y_0$$

2. **90° Rotation Iterations**:
   For $k = \frac{\theta}{90^\circ}$ orthogonal rotations:
   $$(x_{i+1}, y_{i+1}) = (N_{y,i} - 1 - y_i, \; x_i)$$

#### C. Interactive Highlighting & Filtering
- **Interactive Source Selection**: Clicking any row in the catalog table highlights the target object on the image display with a target reticle ring.
- **Search & Filter**: Real-time text search filters the catalog table and dynamically updates plotted markers.
- **Custom Marker Styling**: Supports customizable marker shapes (Circles, Squares, Triangles, Diamonds, Crosses), sizes, colors, and text labels.

---

## 8. FITS Header Editor

The **Header Editor** allows astronomers to view, search, add, or modify FITS header cards across all HDU extensions in memory. Changes can be saved back to a new FITS file.

# Release Notes

[GitHub releases page](https://github.com/astrodatalab/pyql3/releases).

## v3.1.0


### Fixes that change your numbers

- **Total DN for OSIRIS was 1000× too large** on data taken since the detector upgrade, because
  `ITIME` is in milliseconds. Total DN now uses `TRUITIME`. It is offered only when the data are a
  count rate (DN/s) and the header gives an integration time.
- **Apertures were measured 0.5 pixel away from where they were drawn** in the Depth Plot,
  Aperture Photometry and Strehl tools. They now measure the drawn aperture.
- **Statistics and Peak Fit** now use whole pixels. They no longer use interpolated values, or
  count zeros where the box hangs off the image.
- **Tools now follow what is displayed:** a collapsed or boxcar-smoothed plane, flips, rotations
  and newly loaded files. Before, some tools kept showing channel 0 or the previous file.

### Saving FITS files

- **Save As now keeps every extension** and the primary header. Before, it wrote only the
  displayed image.
- **Saving keeps the stored data exactly.** This covers integer images with `BSCALE`/`BZERO` or
  `BLANK`, unsigned integers such as JWST DQ arrays, and tile-compressed images.

### Cubes from other instruments (JWST NIRSpec, Gemini NIFS, ...)

- **Axes come from the WCS.** A cube with no spatial WCS is read as [x, y, wavelength].
- **Units come only from the header.** A unit the header does not state is shown as "Unknown".
- **New in the Depth Plot: Plot Axes ➔ Cube wavelength unit.** If the header gives no wavelength
  unit (e.g. NIFS), choose Å, nm or µm so the spectral line list can be drawn.

### Regions and catalogs

- **Regions use the sky frame of each image's WCS** (FK5, ICRS, galactic). Regions saved on one
  image land on the same stars in another, including between OSIRIS (FK5) and JWST (ICRS) data.
- **ds9 export** keeps label angles and font sizes on rotated fields, and lists anything ds9 cannot
  represent.
- **Plot Catalog has a 0-based / 1-based pixel option.** SExtractor `X_IMAGE`/`Y_IMAGE` positions
  are no longer drawn one pixel off.

### Other

- **Auto-load** no longer misses files when you change the polling interval. It no longer
  replaces the image while Save As or the header editor is open.
- **File ➔ Exit** closes every window.

### Compatibility

- **Region files saved on an image with a WCS need v3.1.0 or later.** Older versions show a
  message asking you to upgrade. To share regions with older versions or other software, use
  **Region ➔ Export ds9 Regions…**.
- **Region files without sky positions** still open in older versions.

### Known limitations

- **Lossily compressed images** (quantised floating point or HCOMPRESS) change slightly every time
  they are saved.
- **In ds9, sky-frame arrows on non-square pixels are drawn in the wrong place.** The export report
  warns when this applies.

## v3.0.0 – v3.0.23

Earlier releases have no notes here. See each release on the
[GitHub releases page](https://github.com/astrodatalab/pyql3/releases).

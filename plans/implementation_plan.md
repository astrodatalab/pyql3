# Dynamic World Coordinate Axis Plan

This plan addresses edge cases and generic logic for the `Depth Plot` tool to ensure it works properly regardless of which axis is set as the Z display axis (e.g., slicing through X, Y, or Wavelength).

## Problem Analysis
Currently, the top axis in the Depth Plot is hardcoded as a `WavelengthAxis`. It only displays if the `CTYPE` of the sliced dimension contains `'WAVE'`. 
If a user changes the display axis in the Image Viewer (e.g., slicing through X or Y instead of Wavelength), they are essentially plotting intensity against a spatial axis. The `Depth Plot` should dynamically recognize this, rename its top axis to match the sliced dimension (e.g., `RA` or `DEC`), and display the correct world coordinates and units, rather than hiding the top axis entirely.

Furthermore, `wcs_pix2world` requires the full N-dimensional pixel coordinate to compute world coordinates accurately, especially for spatial axes with rotation. When slicing along Z, the X and Y coordinates must be locked to the center of the extraction ROI.

## Proposed Changes

### 1. Refactor `WavelengthAxis` to `WorldCoordinateAxis`
- Rename the custom pyqtgraph `AxisItem` in `depth_plot.py` to `WorldCoordinateAxis`.
- Update it to store not just `z_idx`, but also the constant pixel coordinates for the other dimensions (`fixed_coords`). 
- When `tickStrings()` is called, it will construct an N-dimensional coordinate array where the varying dimension is the pixel slice, and the other dimensions use the `fixed_coords` (derived from the center of the ROI).
- The tick strings will extract the specific world coordinate corresponding to `z_idx`.

### 2. Dynamic Axis Labeling in `update_plot`
- Check `self.image_viewer.wcs.wcs.ctype[z_idx]`.
- Extract the base type (e.g., `WAVE`, `RA`, `DEC`) by splitting on `-`.
- Extract the unit using `self.image_viewer.wcs.wcs.cunit[z_idx]`.
- Set the top axis label dynamically: e.g., `RA (deg)`, `DEC (deg)`, `Wavelength (µm)`.
- Pass the ROI center coordinates to `WorldCoordinateAxis.fixed_coords` so that spatial WCS projections are accurate for that specific region.

## Verification
- Test slicing through the `WAVE` axis to ensure Wavelength mapping is intact.
- Test changing the Image Viewer Z-axis to `x` or `y`, then using the Depth Plot to slice along the spatial dimensions. Verify the top axis correctly displays `RA` or `DEC` and formats values appropriately.

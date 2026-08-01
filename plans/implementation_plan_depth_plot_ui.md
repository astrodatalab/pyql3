# Depth Plot UI Overhaul

This plan outlines the changes to bring the pyql3 Depth Plot UI closer to the original IDL `ql2` UI, as requested.

## User Review Required
The original IDL UI stacked everything vertically (Controls on top -> Plot -> Axes Controls -> Region Controls). The current pyql3 UI uses a horizontal split (Plot on the left, Controls in a sidebar on the right) which is often better for widescreen monitors.
- **Decision:** Should I move all controls to be vertically stacked underneath the plot like the original IDL interface, or maintain the right sidebar but add the new "PLOT AXES" group box with the requested features? I will assume keeping the controls organized in the sidebar but grouping them properly (adding the PLOT AXES section) is preferred for modern screens, unless you explicitly want the exact vertical layout.

## Open Questions
- The IDL UI has a "Fix" checkbox. In `pyqtgraph`, pan/zoom is enabled by default. Should the "Fix" checkbox disable mouse panning/zooming (locking the view to the specified range), or just prevent the plot from auto-scaling when the ROI moves?
- The original UI has dual axes (Wavelength on top, Pixels on bottom). We currently support either Wavelength or Pixels on the bottom axis. Do you want the dual-axis feature implemented?

## Proposed Changes

### pyql3/gui/tools/depth_plot.py
- Add a new "PLOT AXES" `QGroupBox` to the controls layout.
- Add "X Range" with two `QLineEdit` (or `QDoubleSpinBox`) for min and max, a "Set" button, a "Rescale/Auto" button, and "Fix" / "Log" checkboxes.
- Add "Y Range" with similar controls.
- Add logic to the "Set" buttons to apply `setXRange` and `setYRange` to the `pg.PlotWidget`.
- Add logic to the "Rescale/Auto" buttons to call `enableAutoRange` on the plot axes, which automatically fits the view to the current data.
- Wire up the "Log" checkboxes to toggle logarithmic scaling (`setLogMode`) for the respective axes.
- Update the existing Region (X0, X1, Y0, Y1) controls to use a group box titled "INPUT DATA FROM CUBE" to match the original grouping.
- Add a cursor-tracking label under the plot that shows the data coordinates and value when hovering over the plot, similar to the IDL `X: ... Y: ... Plot at ...` status line.

## Verification Plan
- Launch the application and open the Depth Plot.
- Verify the new "PLOT AXES" controls appear and are functional.
- Test that manually entering values and clicking "Set" correctly updates the plot view.
- Test the "Rescale" buttons restore auto-scaling.
- Test the "Log" scale checkboxes.
- Verify the hover coordinates display correctly.

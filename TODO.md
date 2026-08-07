# To Do List

- Look into a tabbed interface for multiple viewers. The groundwork is the refactor multi-window
  did not need: extract the document (`FitsReader` + `ImageViewer` + its tool registry) out of
  `MainWindow` into a panel widget, so a window can hold several. The fiddly part is that the
  display state currently lives as **Display** menu checkmarks, which would have to be re-synced
  from the active tab on every tab switch.
- Zenodo integration of release versions so the tool is citeable
- In the Depth Plot tool, create a way to save the plotted spectrum into a 1D FITS file with the proper WCS information for the wavelengths. If the sky subtraction is being done, save the sky subtracted spectrum. Create an implementation plan for this for me to review. Try to add to the UI in a way that is compact.
# DONE
- Draw regions on the view like ds9 — circles, boxes, arrows and text, under a **Region** menu with
  an optional vertical toolbar and a right-click **New Region** submenu that spawns a default-sized
  region where you clicked. Double-click for a properties dialog (colour, line width, dashing, text
  size and angle, tag, visibility, and a channel range that has no ds9 equivalent), a **Region
  List** table, and **Send Regions to Plot Catalog...** for a large set. Saved as readable YAML or
  ds9 `.reg` — the format is read from the file's contents, not its name — with a report of
  anything a conversion could not carry, plus `--regions` on the command line. Geometry is stored
  in pixels with the sky position alongside, so regions survive flips and rotations. Above 500
  regions the set is drawn as one overlay instead of one item each: 20,000 load in 2 s rather than
  2 minutes, at the cost of per-region dragging. Labels are culled to the view, hidden while
  panning, and switchable off. The record of how it was built, with the measurements, is in
  [TODO_regions.md](TODO_regions.md); the durable rules are in `AGENTS.md`.- Clearing a row selection in the Plot Catalog tool: a **Clear Selection** button beside the search
  box, **Escape** in the table, and a **Clear Selection** context-menu entry. Qt's only built-in way
  out of a single-selection table is ctrl-clicking the selected row, which nobody finds — and since
  selecting also recentres the view, a stray click was awkward to undo.
- Multiple cubes at once, one per window: **File ➔ New Window** / **Open in New Window...** /
  **Close Window**, several filenames on the command line, and the Arithmetic result. Each window
  owns its reader, viewer, display settings, tool dialogs and directory watch. Files arriving
  without a window (Finder, `quicklook3` while running) go to the most recently used window, or
  open a new one if none are left; a directory is watched by one window at a time, and moving a
  watch asks first. The **Window** menu groups each window's tools under it.
- Launch from a shell like ds9: **Help -> Install 'quicklook3' Command Line Tool...** (or `--install-cli`) writes a `quicklook3` launcher to /usr/local/bin or ~/.local/bin on macOS and Linux, with a quarantine guard for downloaded .app bundles. The menu action shows the exact path, the run command and the uninstall command, and installs only after confirmation. The macOS bundle also registers FITS document types, so Finder's Open With and double-click work.
- Load FITS tables (binary or ASCII) in the Plot Catalog tool, with an extension chooser for multi-table files, `--catalog-hdu` on the command line, vector columns dropped, masked/undefined coordinates skipped, and photutils/SExtractor column names auto-detected.
- Add the ability to overplot a spectral line list on the Depth Plot tool. Loaded from the data directory with default linelists and custom CSV support. Drawn as vertical dotted lines with line names anchored at the bottom, dynamically filtered by visible x-axis region.
- Enabled Extension Selector (`Extension:`) under "Advanced Data Cube Controls" for 2D images while disabling 3D-only slice/collapse controls.
- Added Extension Selector dropdown (`Extension: 0: PRIMARY`, `1: SCI`, etc.) to the Edit FITS Header dialog for inspecting and editing headers across all HDU extensions.
- Position Angle (N/E) compass vectors dynamically scale and position relative to the visible view window during zoom and pan.
- Right-clicking on the viewer adds "Plot Depth..." and "Gaussian Fit..." context menu options, instantiating the selection box centered at the cursor position.
- Add a "Recent Files" dropdown menu under "File" for quick access to recently opened files.
- Main window title should be just the filename without the path.
- Add option to rotate the view so that North is up. Automatically updates N/E compass vectors via QTransform view rotation. Available under Display -> Rotate Image... when WCS/PA info is present. Displays live angular offset of North relative to up.
- Ability to define a background region to subtract the background spectrum from the source spectrum in the Depth Plot tool. By default, the background spectrum should be the median spectrum inside the background aperture. The user should be able to define the background aperture using the same tools as the profile cut tools. The background spectrum should be subtracted from each pixel's spectrum in the source data. The background spectrum should also be shown. 



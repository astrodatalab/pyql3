# To Do List

- Allow the ability to spawn new windows
- Look into a tabbed interface for multiple viewers 
- Should be some way to reset the highlighting of rows in the plot catalog tool
- Zenodo integration of release versions so the tool is citeable
- Ability to parsh and load ds9 region files
- Ability to draw arrows, circles, squares, and custom regions on images like ds9
- In the Depth Plot tool, create a way to save the plotted spectrum into a 1D FITS file with the proper WCS information for the wavelengths. If the sky subtraction is being done, save the sky subtracted spectrum. Create an implementation plan for this for me to review. Try to add to the UI in a way that is compact.
# DONE
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



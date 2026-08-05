import sys
import argparse
import os
import glob
from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import Qt
from pyql3 import get_resource_path
from pyql3.gui.file_open import FileOpenHandler
from pyql3.gui.main_window import MainWindow


def install_cli():
    """Handle `--install-cli`, printing what happened. Returns a process exit code.

    This runs before any `QApplication` exists so that the frozen application can act as
    its own installer: a user who dragged QuickLook3.app out of the .dmg can run
    `/Applications/QuickLook3.app/Contents/MacOS/QuickLook3 --install-cli` and get a
    working `quicklook3` command without a separate installer script.
    """
    from pyql3.services.cli_install import CliInstallError, describe_plan, install, plan

    try:
        proposed = plan()
        path = install(plan_=proposed)
    except CliInstallError as exc:
        print(f"quicklook3: {exc}", file=sys.stderr)
        return 1

    # The flag is the consent here, so this reports rather than asks -- but it prints the
    # same lines the GUI shows beforehand, including how to undo it. The menu action, where
    # nothing was typed deliberately, confirms first.
    print(f"Installed the '{path.name}' command.")
    for line in describe_plan(proposed, done=True):
        print(line)
    return 0


def main():
    parser = argparse.ArgumentParser(description="QuickLook 3")
    parser.add_argument("filename", nargs="?", help="Optional FITS file to load on startup")
    parser.add_argument("--collapsed", action="store_true", help="Start the app with collapsed view activated (defaults to full cube)")
    parser.add_argument("--collapse-range", nargs=2, type=int, metavar=('ZMIN', 'ZMAX'), help="Start collapsed over the specified range of channels (implies --collapsed)")
    parser.add_argument("--poll-dir", help="Directory to poll for new FITS files (initializes with the most recent one)")
    parser.add_argument("--catalog", help="Catalog file (.csv, .txt, .dat, or a FITS table) to load into the Plot Catalog tool on startup")
    parser.add_argument("--catalog-hdu", help="Table extension of a FITS catalog, as an index or EXTNAME (default: the first table extension)")
    parser.add_argument("--install-cli", action="store_true", help="Install a 'quicklook3' launcher on PATH so QuickLook 3 can be started from a shell, then exit")
    args = parser.parse_args()

    if args.install_cli:
        sys.exit(install_cli())

    app = QApplication(sys.argv)
    app.setApplicationName("QuickLook3")
    app.setApplicationDisplayName("QuickLook3")

    # Catch Finder "open with" requests, which arrive as events rather than argv. Installed
    # before the window exists because a cold launch delivers the event first; the handler
    # queues until set_loader() below.
    file_open_handler = FileOpenHandler(app)
    app.installEventFilter(file_open_handler)


    # Set the dock/application icon (especially important for macOS)
    icon_path = get_resource_path("pyql3/icon.png")
    app.setWindowIcon(QIcon(icon_path))
    
    # Show splash screen immediately
    splash = None
    if os.path.exists(icon_path):
        pixmap = QPixmap(icon_path).scaled(300, 300, Qt.AspectRatioMode.KeepAspectRatio, Qt.TransformationMode.SmoothTransformation)
        splash = QSplashScreen(pixmap, Qt.WindowType.WindowStaysOnTopHint)
        splash.showMessage("Loading QuickLook 3...", Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignCenter, Qt.GlobalColor.white)
        splash.show()
        app.processEvents()
        
    window = MainWindow()
    window.show()
    
    if args.poll_dir:
        poll_dir = os.path.expanduser(args.poll_dir)
        if os.path.isdir(poll_dir):
            window.poller.start_polling(poll_dir)
            if not args.filename:
                files = glob.glob(os.path.join(poll_dir, '*.fits')) + glob.glob(os.path.join(poll_dir, '*.fit'))
                if files:
                    args.filename = max(files, key=os.path.getmtime)
    
    if args.filename:
        window.load_fits(args.filename)
        
        # Only apply collapsed logic if a 3D cube was loaded successfully
        if window.image_viewer.transposed_data is not None and window.image_viewer.transposed_data.ndim == 3:
            if args.collapsed or args.collapse_range:
                if args.collapse_range:
                    window.image_viewer.txt_zmin.setText(str(args.collapse_range[0]))
                    window.image_viewer.txt_zmax.setText(str(args.collapse_range[1]))
                # This will trigger z_mode_changed which applies the range and updates the view
                window.image_viewer.radio_range.setChecked(True)

    if args.catalog and os.path.isfile(args.catalog):
        hdu = args.catalog_hdu
        if hdu is not None and hdu.lstrip('-').isdigit():
            hdu = int(hdu)
        window.open_plot_catalog()
        window._plot_catalog_dialog.load_catalog_file(args.catalog, hdu=hdu)
        
    # Anything Finder asked for outranks a file named on the command line, so this runs
    # last: a queued open-document request is applied on top of args.filename.
    file_open_handler.set_loader(window.load_fits)

    if splash:
        splash.finish(window)
        
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

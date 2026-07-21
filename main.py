import sys
import argparse
import os
import glob
from PySide6.QtWidgets import QApplication, QSplashScreen
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtCore import Qt
from pyql3 import get_resource_path
from pyql3.gui.main_window import MainWindow

def main():
    parser = argparse.ArgumentParser(description="OSIRIS QuickLook v3")
    parser.add_argument("filename", nargs="?", help="Optional FITS file to load on startup")
    parser.add_argument("--collapsed", action="store_true", help="Start the app with collapsed view activated (defaults to full cube)")
    parser.add_argument("--collapse-range", nargs=2, type=int, metavar=('ZMIN', 'ZMAX'), help="Start collapsed over the specified range of channels (implies --collapsed)")
    parser.add_argument("--poll-dir", help="Directory to poll for new FITS files (initializes with the most recent one)")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("QuickLook3")
    app.setApplicationDisplayName("QuickLook3")
    
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
    
    if args.poll_dir and os.path.isdir(args.poll_dir):
        window.poller.start_polling(args.poll_dir)
        if not args.filename:
            files = glob.glob(os.path.join(args.poll_dir, '*.fits')) + glob.glob(os.path.join(args.poll_dir, '*.fit'))
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
        
    if splash:
        splash.finish(window)
        
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

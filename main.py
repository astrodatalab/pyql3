import sys
import argparse
import os
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QIcon
from pyql3.gui.main_window import MainWindow

def main():
    parser = argparse.ArgumentParser(description="OSIRIS QuickLook v3")
    parser.add_argument("filename", nargs="?", help="Optional FITS file to load on startup")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    app.setApplicationName("QuickLook3")
    app.setApplicationDisplayName("QuickLook3")
    
    # Set the dock/application icon (especially important for macOS)
    icon_path = os.path.join(os.path.dirname(__file__), "pyql3", "icon.png")
    app.setWindowIcon(QIcon(icon_path))
    
    window = MainWindow()
    window.show()
    
    if args.filename:
        window.load_fits(args.filename)
        
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

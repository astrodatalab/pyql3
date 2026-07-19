import sys
import argparse
from PySide6.QtWidgets import QApplication
from pyql3.gui.main_window import MainWindow

def main():
    parser = argparse.ArgumentParser(description="OSIRIS QuickLook v3")
    parser.add_argument("filename", nargs="?", help="Optional FITS file to load on startup")
    args = parser.parse_args()

    app = QApplication(sys.argv)
    window = MainWindow()
    window.show()
    
    if args.filename:
        window.load_fits(args.filename)
        
    sys.exit(app.exec())

if __name__ == "__main__":
    main()

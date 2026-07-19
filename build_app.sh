#!/bin/bash
# Exit on error
set -e

echo "Ensuring pyinstaller and pillow are installed (Pillow is needed for PNG to ICNS conversion)..."
uv add --dev pyinstaller pillow

echo "Building QuickLook3.app bundle..."
# --noconfirm ensures it overwrites previous builds without prompting
uv run pyinstaller --name "QuickLook3" --windowed --icon "pyql3/icon.png" --collect-all photutils --noconfirm main.py

echo "Build complete! The application is located at dist/QuickLook3.app"

echo "Creating DMG package for release..."
ARCH=$(uname -m)
DMG_NAME="QuickLook3-${ARCH}.dmg"
hdiutil create -volname "QuickLook3" -srcfolder dist/QuickLook3.app -ov -format UDZO dist/${DMG_NAME}

echo "Release package created at dist/${DMG_NAME}"

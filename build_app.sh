#!/bin/bash
# Exit on error
set -e

echo "Ensuring pyinstaller and pillow are installed (Pillow is needed for PNG to ICNS conversion)..."
uv add --dev pyinstaller pillow

echo "Building QuickLook3.app bundle..."
# --noconfirm ensures it overwrites previous builds without prompting
uv run pyinstaller --noconfirm QuickLook3.spec

echo "Build complete! The application is located at dist/QuickLook3.app"

echo "Creating DMG package for release..."
ARCH=$(uname -m)
if [ "$ARCH" = "arm64" ]; then
    MAC_LABEL="macOS-AppleSilicon"
elif [ "$ARCH" = "x86_64" ]; then
    MAC_LABEL="macOS-Intel"
else
    MAC_LABEL="macOS-${ARCH}"
fi
VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "vUnknown")
DMG_NAME="QuickLook3-${VERSION}-${MAC_LABEL}.dmg"
hdiutil create -volname "QuickLook3" -srcfolder dist/QuickLook3.app -ov -format UDZO dist/${DMG_NAME}

echo "Release package created at dist/${DMG_NAME}"

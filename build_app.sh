#!/bin/bash
# Exit on error
set -e

echo "Ensuring pyinstaller and pillow are installed..."
uv add --dev pyinstaller pillow

echo "Building QuickLook3 with PyInstaller..."
uv run pyinstaller --noconfirm QuickLook3.spec

echo "Verifying bundled assets in dist..."
uv run python -c "
import pathlib, sys
dist_dir = pathlib.Path('dist')
found_lines = list(dist_dir.glob('**/*lines.txt'))
if not found_lines:
    print('ERROR: No line list text files found in dist build artifact!')
    sys.exit(1)
found_cmc = list(dist_dir.glob('**/cmcrameri*')) + list(dist_dir.glob('**/cmc*'))
if not found_cmc:
    print('ERROR: No cmcrameri assets found in dist build artifact!')
    sys.exit(1)
# ds9 region support: seven compiled extensions PyInstaller only picks up via collect_all.
found_regions = list(dist_dir.glob('**/regions/_geometry/*'))
if not found_regions:
    print('ERROR: No regions/_geometry extensions found in dist build artifact!')
    sys.exit(1)
print('ALL PACKAGED ASSETS VERIFIED SUCCESSFULLY!')
"

VERSION=$(git describe --tags --abbrev=0 2>/dev/null || echo "vUnknown")
OS_TYPE=$(uname -s)

if [ "$OS_TYPE" = "Darwin" ]; then
    echo "Creating macOS DMG package for release..."
    ARCH=$(uname -m)
    if [ "$ARCH" = "arm64" ]; then
        MAC_LABEL="macOS-AppleSilicon"
    elif [ "$ARCH" = "x86_64" ]; then
        MAC_LABEL="macOS-Intel"
    else
        MAC_LABEL="macOS-${ARCH}"
    fi
    DMG_NAME="QuickLook3-${VERSION}-${MAC_LABEL}.dmg"
    hdiutil create -volname "QuickLook3" -srcfolder dist/QuickLook3.app -ov -format UDZO dist/${DMG_NAME}
    echo "Release package created at dist/${DMG_NAME}"

elif [ "$OS_TYPE" = "Linux" ]; then
    echo "Creating Linux tar.gz package for release..."
    TAR_NAME="QuickLook3-${VERSION}-Linux.tar.gz"
    tar -czvf dist/${TAR_NAME} -C dist QuickLook3
    echo "Release package created at dist/${TAR_NAME}"

else
    echo "Build completed successfully for system: ${OS_TYPE}."
fi

# -*- mode: python ; coding: utf-8 -*-
from PyInstaller.utils.hooks import collect_all

datas = [
    ('pyql3/icon.png', 'pyql3'),
    ('pyql3/data', 'pyql3/data'),
]
binaries = []
hiddenimports = []
tmp_ret = collect_all('photutils')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]
tmp_ret = collect_all('cmcrameri')
datas += tmp_ret[0]; binaries += tmp_ret[1]; hiddenimports += tmp_ret[2]


a = Analysis(
    ['main.py'],
    pathex=[],
    binaries=binaries,
    datas=datas,
    hiddenimports=hiddenimports,
    hookspath=[],
    hooksconfig={},
    runtime_hooks=[],
    excludes=[],
    noarchive=False,
    optimize=0,
    module_collection_mode={'cmcrameri': 'py'},
)
pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,
    name='QuickLook3',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=True,
    console=False,
    disable_windowed_traceback=False,
    # Deliberately off. argv_emulation intercepts macOS open-document Apple Events before
    # Qt starts and rewrites sys.argv from them; it only covers the launching document and
    # has a history of hanging. pyql3.gui.file_open.FileOpenHandler handles the event
    # inside Qt instead, which also catches files opened while the app is already running.
    argv_emulation=False,
    target_arch=None,
    codesign_identity=None,
    entitlements_file=None,
    icon=['pyql3/icon.png'],
)
coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    strip=False,
    upx=True,
    upx_exclude=[],
    name='QuickLook3',
)
app = BUNDLE(
    coll,
    name='QuickLook3.app',
    icon='pyql3/icon.png',
    # A real reverse-DNS identifier: with PyInstaller's default of None the Info.plist
    # gets CFBundleIdentifier = 'QuickLook3', which macOS accepts but which makes
    # `open -b`, LaunchServices registration, and per-app settings unreliable.
    bundle_identifier='edu.ucla.astro.pyql3',
    info_plist={
        # Registers QuickLook3 as an opener for FITS files, which is what puts it in
        # Finder's "Open With" menu and allows a double-click to launch it with the file.
        # LaunchServices matches only the final extension component, so `.fits.gz` cannot
        # be claimed here without also claiming every other `.gz`.
        'CFBundleDocumentTypes': [
            {
                'CFBundleTypeName': 'FITS Image',
                'CFBundleTypeRole': 'Viewer',
                'CFBundleTypeExtensions': ['fits', 'fit', 'fts', 'fz'],
                # Alternate, not Owner: astronomers usually have another FITS viewer
                # installed and it should not be displaced without being asked.
                'LSHandlerRank': 'Alternate',
            },
        ],
        'NSHighResolutionCapable': True,
    },
)

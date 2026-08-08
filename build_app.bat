@echo off
:: --frozen, never `uv add`. pyinstaller and pillow are already in the locked dev group,
:: which plain `uv sync` installs; `uv add` re-resolves and rewrites uv.lock, so building
:: here would quietly change the versions every later build and release is made from.
echo Installing locked dependencies (includes pyinstaller and pillow)...
uv sync --frozen

echo Building QuickLook3.exe bundle...
:: --noconfirm ensures it overwrites previous builds without prompting
uv run pyinstaller --noconfirm QuickLook3.spec

echo Build complete! The application executable is located in the dist\QuickLook3 folder.
pause

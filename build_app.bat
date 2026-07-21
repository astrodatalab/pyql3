@echo off
echo Ensuring pyinstaller and pillow are installed (Pillow is needed for PNG to ICO conversion)...
uv add --dev pyinstaller pillow

echo Building QuickLook3.exe bundle...
:: --noconfirm ensures it overwrites previous builds without prompting
uv run pyinstaller --noconfirm QuickLook3.spec

echo Build complete! The application executable is located in the dist\QuickLook3 folder.
pause

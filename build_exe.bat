@echo off
setlocal
cd /d %~dp0

echo ============================================
echo   DesktopPet v3.8.1 build script
echo ============================================
echo.
echo Rules:
echo   - Only update dist\DesktopPet\_internal and DesktopPet.exe
echo   - Never touch dist\DesktopPet\live2d or models inside it
echo   - Never create or overwrite a project-root live2d folder
echo.

REM 1) Stop running app
echo [1/8] Checking running app...
tasklist /FI "IMAGENAME eq DesktopPet.exe" 2>NUL | find /I /N "DesktopPet.exe">NUL
if "%ERRORLEVEL%"=="0" (
    echo Found running DesktopPet.exe, stopping it...
    taskkill /F /IM DesktopPet.exe >nul 2>&1
    timeout /t 2 >nul
)

REM 2) Ensure build + runtime dependencies (so every feature gets bundled)
echo [2/8] Checking build/runtime dependencies...
.venv\Scripts\python.exe -m pip show pyinstaller >nul 2>&1
if errorlevel 1 (
    echo Installing PyInstaller...
    .venv\Scripts\python.exe -m pip install pyinstaller
    if errorlevel 1 exit /b 1
)
REM send2trash powers "delete model to Recycle Bin". If it is not installed in the
REM venv, PyInstaller silently omits it (it is only a hiddenimport) and the packaged
REM exe cannot delete models. This auto-install is the guard against that exact bug.
.venv\Scripts\python.exe -c "import send2trash, send2trash.win" >nul 2>&1
if errorlevel 1 (
    echo Installing send2trash...
    .venv\Scripts\python.exe -m pip install send2trash
    if errorlevel 1 exit /b 1
)
REM Core runtime deps must import, or the build is incomplete/broken.
.venv\Scripts\python.exe -c "import PySide6, pygame, numpy, OpenGL, live2d" >nul 2>&1
if errorlevel 1 (
    echo [ERROR] Missing core runtime deps in venv.
    echo         Run: .venv\Scripts\python.exe -m pip install -r requirements.txt
    exit /b 1
)

REM 3) Clean temp build dirs only
echo [3/8] Cleaning temp build dirs...
if exist build (
    rmdir /s /q build >nul 2>&1
    if exist build (
        echo [WARN] build cleanup skipped ^(files are in use^).
    )
)
if exist dist_build (
    rmdir /s /q dist_build >nul 2>&1
    if exist dist_build (
        echo [ERROR] dist_build cleanup failed ^(files are in use^).
        exit /b 1
    )
)

REM 4) Build into temp output
echo [4/8] Building into dist_build...
.venv\Scripts\python.exe -m PyInstaller desktop_pet.spec --noconfirm --clean --distpath dist_build
if errorlevel 1 (
    echo [ERROR] PyInstaller build failed.
    exit /b 1
)

REM 5) Replace app files but keep models
echo [5/8] Updating packaged files...
if not exist dist\DesktopPet mkdir dist\DesktopPet
set "APP_DIST=dist\DesktopPet"
set "NEW_INTERNAL=%APP_DIST%\_internal_new"
set "OLD_INTERNAL=%APP_DIST%\_internal_old"
set "NEW_EXE=%APP_DIST%\DesktopPet.exe.new"

if not exist dist_build\DesktopPet\DesktopPet.exe (
    echo [ERROR] Built exe is missing: dist_build\DesktopPet\DesktopPet.exe
    exit /b 1
)
if not exist dist_build\DesktopPet\_internal\python312.dll (
    echo [ERROR] Built Python runtime is missing: dist_build\DesktopPet\_internal\python312.dll
    exit /b 1
)
if not exist dist_build\DesktopPet\_internal\base_library.zip (
    echo [ERROR] Built Python library archive is missing: dist_build\DesktopPet\_internal\base_library.zip
    exit /b 1
)

REM Verify the delete-model dependency (send2trash) actually got bundled. Pure-Python
REM modules may live in the embedded PYZ rather than as a folder, so treat a folder as
REM proof, otherwise fall back to PyInstaller's missing-module warning (best-effort).
if exist dist_build\DesktopPet\_internal\send2trash (
    echo   - send2trash bundled ^(folder^): OK
) else (
    findstr /S /M /C:"missing module named send2trash" build\warn-*.txt >nul 2>&1
    if not errorlevel 1 (
        echo [ERROR] send2trash was NOT bundled - packaged exe could not delete models.
        echo         Make sure 'import send2trash' works in .venv, then rebuild.
        exit /b 1
    )
    echo   - send2trash bundled ^(PYZ^): OK
)

if exist "%NEW_INTERNAL%" (
    rmdir /s /q "%NEW_INTERNAL%" >nul 2>&1
    if exist "%NEW_INTERNAL%" (
        echo [ERROR] Failed to remove stale %NEW_INTERNAL%
        exit /b 1
    )
)
if exist "%OLD_INTERNAL%" (
    rmdir /s /q "%OLD_INTERNAL%" >nul 2>&1
    if exist "%OLD_INTERNAL%" (
        echo [ERROR] Failed to remove stale %OLD_INTERNAL%
        exit /b 1
    )
)
if exist "%NEW_EXE%" del /f /q "%NEW_EXE%" >nul 2>&1

robocopy dist_build\DesktopPet\_internal "%NEW_INTERNAL%" /E /NFL /NDL /NJH /NJS /NC /NS >nul
if errorlevel 8 (
    echo [ERROR] Failed to stage dist_build\DesktopPet\_internal
    exit /b 1
)
if not exist "%NEW_INTERNAL%\python312.dll" (
    echo [ERROR] Staged _internal is incomplete: missing python312.dll
    exit /b 1
)
if not exist "%NEW_INTERNAL%\base_library.zip" (
    echo [ERROR] Staged _internal is incomplete: missing base_library.zip
    exit /b 1
)

copy /Y dist_build\DesktopPet\DesktopPet.exe "%NEW_EXE%" >nul
if errorlevel 1 (
    echo [ERROR] Failed to stage DesktopPet.exe
    exit /b 1
)
if not exist "%NEW_EXE%" (
    echo [ERROR] Staged DesktopPet.exe is missing
    exit /b 1
)

if exist "%APP_DIST%\_internal" (
    ren "%APP_DIST%\_internal" _internal_old
    if errorlevel 1 (
        echo [ERROR] Failed to move old _internal out of the way
        exit /b 1
    )
)
move /Y "%NEW_INTERNAL%" "%APP_DIST%\_internal" >nul
if errorlevel 1 (
    if exist "%OLD_INTERNAL%" ren "%OLD_INTERNAL%" _internal >nul 2>&1
    echo [ERROR] Failed to publish new _internal
    exit /b 1
)
copy /Y "%NEW_EXE%" "%APP_DIST%\DesktopPet.exe" >nul
if errorlevel 1 (
    echo [ERROR] Failed to publish DesktopPet.exe
    exit /b 1
)
del /f /q "%NEW_EXE%" >nul 2>&1
if exist "%OLD_INTERNAL%" rmdir /s /q "%OLD_INTERNAL%" >nul 2>&1

if exist voice_translations.json (
    copy /Y voice_translations.json dist\DesktopPet\ >nul
    if errorlevel 1 (
        echo [WARN] Failed to copy voice_translations.json
    )
)

REM 6) Ensure model dirs exist
echo [6/8] Ensuring model folders exist...
if not exist dist\DesktopPet\live2d mkdir dist\DesktopPet\live2d
powershell -NoProfile -Command "$favName = [string]([char]24120) + [char]29992; $fav = Join-Path 'dist\\DesktopPet\\live2d' $favName; if (-not (Test-Path -LiteralPath $fav)) { New-Item -ItemType Directory -Path $fav | Out-Null }"
if errorlevel 1 (
    echo [WARN] Failed to ensure favorites folder.
)

REM 7) Clean accidental empty root live2d shell only
echo [7/8] Checking project-root live2d shell...
if exist live2d (
    dir /a /b live2d > "%TEMP%\desktop_pet_live2d_entries.txt" 2>nul
    for %%I in ("%TEMP%\desktop_pet_live2d_entries.txt") do (
        if %%~zI EQU 0 (
            echo   - root live2d is empty, removing it.
            rmdir /s /q live2d
        ) else (
            echo   - root live2d has files or subfolders, keeping it.
        )
    )
    del /q "%TEMP%\desktop_pet_live2d_entries.txt" >nul 2>&1
)

REM 8) Copy docs and clean temp dirs
echo [8/8] Copying docs and cleaning temp dirs...
if exist CHANGELOG.md copy /Y CHANGELOG.md dist\DesktopPet\ >nul 2>&1
powershell -NoProfile -Command "$note = Get-ChildItem -File -LiteralPath . | Where-Object { $_.Name -match 'v\d+\.\d+\.\d+\.txt$' } | Sort-Object LastWriteTime -Descending | Select-Object -First 1; if ($note) { Copy-Item -LiteralPath $note.FullName -Destination 'dist\\DesktopPet' -Force }"
if exist app_icon.ico copy /Y app_icon.ico dist\DesktopPet\ >nul 2>&1
if exist build (
    rmdir /s /q build >nul 2>&1
    if exist build (
        echo [WARN] build cleanup skipped ^(files are in use^).
    )
)
if exist dist_build (
    rmdir /s /q dist_build >nul 2>&1
    if exist dist_build (
        echo [WARN] dist_build cleanup skipped ^(files are in use^).
    )
)

echo.
echo ============================================
echo   Build complete
echo ============================================
echo Output: dist\DesktopPet\DesktopPet.exe
echo Models: dist\DesktopPet\live2d
echo Favorites folder: dist\DesktopPet\live2d
echo.
exit /b 0

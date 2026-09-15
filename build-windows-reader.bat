@echo off
setlocal

REM Build this on Windows. End users only receive the signed executables in dist\.
REM Override WEDSTRIJDBLAD_PYTHON with a full python.exe path when building
REM x64 from Windows on ARM. The default remains the regular `py` launcher.
if not defined WEDSTRIJDBLAD_PYTHON set "WEDSTRIJDBLAD_PYTHON=py"
"%WEDSTRIJDBLAD_PYTHON%" -m pip install --upgrade pip
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" -m pip install -r requirements-wedstrijdblad-sync.txt
if errorlevel 1 exit /b 1
REM The two novice-facing executables use Tk.  Fail before packaging if this
REM Python distribution is the embeddable/minimal variant without Tcl/Tk.
"%WEDSTRIJDBLAD_PYTHON%" -c "import tkinter; print('Tk', tkinter.TkVersion)"
if errorlevel 1 (
  echo Tk is missing. Install the regular Python 3.11+ runtime from python.org, then retry.
  exit /b 1
)
REM Windowed executables do not have a reliable stdout/stderr stream for
REM unittest output. Exercise their source entry points while this console is
REM available; the packaged console tools are checked again below.
"%WEDSTRIJDBLAD_PYTHON%" wedstrijdblad_wizard.py --self-test
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" wedstrijdblad_dryrun_wizard.py --self-test
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" wedstrijdblad_desktop.py --self-test
if errorlevel 1 exit /b 1
REM --collect-all keeps Playwright's Node driver and package data inside each executable.
REM The installed Edge channel is used at runtime; no browser download is required.
"%WEDSTRIJDBLAD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --name wedstrijdblad-reader --collect-all playwright --hidden-import playwright.async_api wedstrijdblad_browser.py
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --name wedstrijdblad-adf-probe --collect-all playwright --hidden-import playwright.async_api wedstrijdblad_adf_probe.py
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --name wedstrijdblad-psd-lezer --collect-all playwright --hidden-import playwright.async_api psd_reader.py
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed --name wedstrijdblad-psd-wizard --collect-all playwright --hidden-import playwright.async_api wedstrijdblad_wizard.py
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed --name wedstrijdblad-dryrun-wizard --collect-all playwright --hidden-import playwright.async_api --hidden-import wedstrijdblad_draft_apply --hidden-import wedstrijdblad_adf_draft_adapter --hidden-import wedstrijdblad_populated_sync --hidden-import wedstrijdblad_draft_executor --hidden-import wedstrijdblad_execution_guard --hidden-import wedstrijdblad_adf_staff_reader --hidden-import wedstrijdblad_adf_staff_draft_adapter --hidden-import wedstrijdblad_staff_sync --hidden-import wedstrijdblad_staff_executor --hidden-import wedstrijdblad_combined_sync wedstrijdblad_dryrun_wizard.py
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --name wedstrijdblad-dryrun --collect-all playwright --hidden-import playwright.async_api wedstrijdblad_dryrun.py
if errorlevel 1 exit /b 1
"%WEDSTRIJDBLAD_PYTHON%" -m PyInstaller --noconfirm --clean --onefile --windowed --name wedstrijdblad-controle wedstrijdblad_desktop.py
if errorlevel 1 exit /b 1

echo.
echo Built: dist\wedstrijdblad-reader.exe
echo Built: dist\wedstrijdblad-adf-probe.exe
echo Built: dist\wedstrijdblad-psd-lezer.exe
echo Built: dist\wedstrijdblad-psd-wizard.exe
echo Built: dist\wedstrijdblad-dryrun-wizard.exe
echo Built: dist\wedstrijdblad-dryrun.exe
echo Built: dist\wedstrijdblad-controle.exe
echo Running packaged non-interactive self-tests...
dist\wedstrijdblad-reader.exe --self-test
if errorlevel 1 exit /b 1
dist\wedstrijdblad-psd-lezer.exe --self-test
if errorlevel 1 exit /b 1
dist\wedstrijdblad-dryrun.exe --self-test
if errorlevel 1 exit /b 1
echo Packaged console self-tests passed. The windowed GUI entry points were source-tested above.
echo Perform the live Windows checklist before distribution.

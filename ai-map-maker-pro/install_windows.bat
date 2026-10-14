@echo off
REM Install Claude AI Map Maker Pro to QGIS on Windows.
setlocal EnableDelayedExpansion

set "SCRIPT_DIR=%~dp0"
if "%SCRIPT_DIR:~-1%"=="\" set "SCRIPT_DIR=%SCRIPT_DIR:~0,-1%"

set "PLUGIN_NAME=claude_qgis_pro"

set "BASE=%APPDATA%\QGIS"

set "TARGET="
if exist "%BASE%\QGIS4\profiles\default\python\plugins" (
  set "TARGET=%BASE%\QGIS4\profiles\default\python\plugins"
) else if exist "%BASE%\QGIS3\profiles\default\python\plugins" (
  set "TARGET=%BASE%\QGIS3\profiles\default\python\plugins"
)

if not defined TARGET (
  echo Could not find a QGIS profile folder under %BASE%.
  echo Launch QGIS once, then re-run this script.
  exit /b 1
)

if not exist "%TARGET%" mkdir "%TARGET%"
set "DEST=%TARGET%\%PLUGIN_NAME%"

if exist "%DEST%" (
  echo Removing existing install at %DEST%
  rmdir /s /q "%DEST%"
)

echo Copying plugin to: %DEST%
xcopy /E /I /Q /Y "%SCRIPT_DIR%" "%DEST%" >nul

echo.
echo Installed. Restart QGIS, then:
echo   Plugins -^> Manage and Install Plugins -^> Installed
echo   Enable 'Claude AI Map Maker Pro'.
endlocal

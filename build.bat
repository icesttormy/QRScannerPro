@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
echo ============================================
echo   打包 截图识码工具.exe
echo ============================================

REM 如果还没装依赖，先取消下面这行的注释运行一次
REM pip install -r requirements.txt

set "PYTHON_EXE=%~dp0.venv\Scripts\python.exe"
if not exist "%PYTHON_EXE%" (
    for /f "delims=" %%P in ('py -3 -c "import sys; print(sys.executable)"') do set "PYTHON_EXE=%%P"
)
if not exist "%PYTHON_EXE%" (
    echo [错误] 没有找到 Python，请先安装依赖
    goto :error
)
for %%P in ("%PYTHON_EXE%") do set "PYTHON_DIR=%%~dpP"

REM 隔离 PyInstaller 的 DLL 搜索路径。Codex 运行时缓存里有一套 poppler 的
REM ICU DLL，被误打包后会与 PySide6 的 Qt6Core 冲突，启动时报 DLL load failed。
set "PATH=%SystemRoot%\System32;%SystemRoot%;%SystemRoot%\System32\Wbem;%SystemRoot%\System32\WindowsPowerShell\v1.0;%PYTHON_DIR%"

set "DIST_DIR=dist_candidate"
set "WORK_DIR=build_candidate"
set "ROOT=%~dp0"

REM Tesseract 是可选的。存在时只提取运行所需的 DLL 和 eng.traineddata。
if exist "%ROOT%tesseract_bin\tesseract.exe" (
    "%PYTHON_EXE%" tools\prepare_tesseract_bundle.py
    if errorlevel 1 (
        echo [错误] 准备精简 Tesseract 运行包失败
        goto :error
    )
) else (
    echo [提示] 未找到 tesseract_bin，本次构建不内置 OCR 引擎
    if not exist "%ROOT%build_assets\tesseract_bin" mkdir "%ROOT%build_assets\tesseract_bin"
)

"%PYTHON_EXE%" -m PyInstaller ^
    --noconfirm ^
    --clean ^
    --onefile ^
    --windowed ^
    --name "截图识码工具" ^
    --icon "%ROOT%icon.ico" ^
    --add-data "%ROOT%icon.ico;." ^
    --add-data "%ROOT%build_assets\tesseract_bin;tesseract_bin" ^
    --collect-all pyzbar ^
    --hidden-import keyboard ^
    --distpath "%DIST_DIR%" ^
    --workpath "%WORK_DIR%" ^
    --specpath "%WORK_DIR%" ^
    main.py
if errorlevel 1 goto :error

"%PYTHON_EXE%" tools\check_pyinstaller_bundle.py "%WORK_DIR%\截图识码工具\PKG-00.toc"
if errorlevel 1 goto :error

echo.
echo 打包完成，exe 在 %DIST_DIR%\截图识码工具.exe
echo 旧目录 dist 和 installer_output 未被修改。
pause
exit /b 0

:error
echo.
echo 打包失败，请查看上面的错误信息。
pause
exit /b 1

@echo off
rem ============================================================================
rem  批量解压控制台 · 一键启动
rem  双击本文件：启动/复用本地服务 -> 等就绪 -> 在「系统默认外部浏览器」打开页面
rem  本文件以 GBK 编码保存（中文 Windows cmd 默认代码页 936），勿改成 UTF-8。
rem ============================================================================
chcp 936 >nul 2>&1
set "PYTHONIOENCODING=gbk"
setlocal EnableDelayedExpansion
cd /d "%~dp0"

rem ===== 可编辑配置区 =======================================================
set "PORT=8765"
rem 指定外部浏览器 exe；留空 = 系统默认浏览器
rem 例：set "BX_BROWSER=C:\Program Files\Google\Chrome\Application\chrome.exe"
set "BX_BROWSER="
rem 1 = 执行完保留窗口看输出；0 = 自动关闭窗口，只留浏览器页面
set "KEEP_CONSOLE=0"
rem 自动探测不到 Python 时，手动填完整路径：
rem set "BX_PYTHON=C:\Python313\python.exe"
rem ========================================================================

rem ---- 定位 Python：显式配置 > PATH 中的 python > py 启动器 > 常见安装位置 ----
set "PYEXE="
set "PYARGS="
if defined BX_PYTHON if exist "%BX_PYTHON%" set "PYEXE=%BX_PYTHON%"

if not defined PYEXE for /f "delims=" %%P in ('where python 2^>nul') do call :TRYP "%%P"
if not defined PYEXE for /f "delims=" %%P in ('where py 2^>nul') do call :TRYLAUNCH "%%P"
if not defined PYEXE for /d %%D in ("%USERPROFILE%\.workbuddy\binaries\python\versions\*") do if exist "%%D\python.exe" call :TRYP "%%D\python.exe"
if not defined PYEXE for /d %%D in ("%LOCALAPPDATA%\Programs\Python\Python3*") do if exist "%%D\python.exe" call :TRYP "%%D\python.exe"
if not defined PYEXE for /d %%D in ("C:\Python3*") do if exist "%%D\python.exe" call :TRYP "%%D\python.exe"

if not defined PYEXE (
  echo [错误] 未找到可用的 Python 3.8+。
  echo        请安装 Python，或编辑本文件顶部：set "BX_PYTHON=python.exe 完整路径"
  pause
  exit /b 1
)

echo [启动] Python: %PYEXE% %PYARGS%
"%PYEXE%" %PYARGS% "%~dp0launch.py" --port %PORT% %*
set "RC=%ERRORLEVEL%"
if not "%RC%"=="0" echo [失败] 启动未完成，详见上方提示。日志：%TEMP%\batch-extractor-server.log
if "%KEEP_CONSOLE%"=="1" pause
exit /b %RC%

rem ---- 候选解释器校验：跳过 WindowsApps 商店别名，且必须真的能执行 ----
:TRYP
if defined PYEXE goto :eof
set "CAND=%~1"
if not "%CAND:WindowsApps=%"=="%CAND%" goto :eof
"%~1" --version >nul 2>&1 || goto :eof
set "PYEXE=%~1"
goto :eof

:TRYLAUNCH
if defined PYEXE goto :eof
set "CAND=%~1"
if not "%CAND:WindowsApps=%"=="%CAND%" goto :eof
"%~1" -3 --version >nul 2>&1 || goto :eof
set "PYEXE=%~1"
set "PYARGS=-3"
goto :eof

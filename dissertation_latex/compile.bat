@echo off
REM =====================================================================
REM Скрипт компиляции диссертации (Windows)
REM Запуск: двойной клик на этот файл, или "compile.bat" в cmd
REM =====================================================================

echo.
echo ========================================
echo   Compiling Sekenov Dissertation 2026
echo ========================================
echo.

REM Добавляем MiKTeX в PATH (если нет)
set PATH=%PATH%;%LocalAppData%\Programs\MiKTeX\miktex\bin\x64

REM Шаг 1: первый pdflatex (создаёт .aux)
echo [1/4] First pdflatex pass...
pdflatex -interaction=nonstopmode main.tex > _compile.log 2>&1
if errorlevel 1 (
    echo ERROR: pdflatex failed. Check _compile.log for details.
    pause
    exit /b 1
)

REM Шаг 2: bibtex (формирует библиографию)
echo [2/4] Running bibtex...
bibtex main >> _compile.log 2>&1

REM Шаг 3: второй pdflatex (включает refs)
echo [3/4] Second pdflatex pass...
pdflatex -interaction=nonstopmode main.tex >> _compile.log 2>&1

REM Шаг 4: третий pdflatex (фиксит cross-references)
echo [4/4] Third pdflatex pass (final)...
pdflatex -interaction=nonstopmode main.tex >> _compile.log 2>&1

echo.
if exist main.pdf (
    echo ✓ SUCCESS: main.pdf created!
    echo.
    echo Opening PDF...
    start main.pdf
) else (
    echo ✗ FAILED: main.pdf not created. Check _compile.log
)

echo.
echo Cleaning up intermediate files...
del /Q *.aux *.log *.out *.spl *.toc *.lof *.lot *.bbl *.blg 2>nul
echo Done.
echo.
pause

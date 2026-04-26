@echo off
REM Live Glyph Reader — visualises what the OCR pipeline SEES vs READS
REM per glyph crop, with classifier confidence color-coded.
cd /d "%~dp0\.."
start "" "C:\Users\prjgn\AppData\Local\Python\pythoncore-3.14-64\pythonw.exe" "scripts\glyph_reader_viewer.py"

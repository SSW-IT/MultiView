@echo off
echo Building MultiView Portable...

:: 가상환경의 설치 경로를 자동으로 찾아서 개인정보(PC 이름 등) 노출 방지
set "PLAYWRIGHT_DIR=%CONDA_PREFIX%\Lib\site-packages\playwright\driver\package\.local-browsers"

pyinstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onedir ^
  --name "MultiView" ^
  --add-data "%PLAYWRIGHT_DIR%;playwright\driver\package\.local-browsers" ^
  --collect-all PySide6 ^
  --hidden-import PySide6.QtCore ^
  --hidden-import PySide6.QtGui ^
  --hidden-import PySide6.QtWidgets ^
  --hidden-import PySide6.QtNetwork ^
  --hidden-import requests ^
  main.py

echo Build Complete! Check the 'dist' folder.
pause
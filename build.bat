@echo off
REM Builds dist\MP4toMP3.exe (a single file, no Python needed to run it)
python -m venv .venv
call .venv\Scripts\activate
pip install -r requirements.txt pyinstaller
pyinstaller --noconfirm --onefile --windowed --name MP4toMP3 --collect-all customtkinter --collect-all tkinterdnd2 --collect-all imageio_ffmpeg app.py
echo.
echo Done. Your app is at dist\MP4toMP3.exe
pause

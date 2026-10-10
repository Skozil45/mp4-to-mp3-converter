# MP4 to MP3 Converter

A small Windows 11 desktop app that extracts the audio from video files and saves it as MP3.
Built with Python, CustomTkinter and FFmpeg (bundled through `imageio-ffmpeg`, so there is nothing else to install).

## Features
- Drag and drop or pick multiple videos (MP4, MOV, MKV, AVI, WebM and more)
- Choose 128, 192, 256 or 320 kbps
- Save next to each video or in a folder you choose
- Per-file and overall progress, cancel at any time
- Never overwrites: existing files become `name (1).mp3`

## Run from source
```bash
pip install -r requirements.txt
python app.py
```

## Build the .exe
Double-click `build.bat`, or push a tag like `v1.0.0` and GitHub Actions builds `MP4toMP3.exe`
and attaches it to a release.

## License
MIT

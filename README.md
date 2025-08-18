# Video Downloader

A modern desktop app for downloading online videos with a clean UI, live progress, size estimates, and robust FFmpeg detection.

Built with **Python + Tkinter** (optional: ttkbootstrap for a nicer theme).

---

## Requirements

- **Python 3.11** (recommended and tested)
- ffmpeg-python
- pillow
- ttkbootstrap *(optional, for nicer theme)*
- FFmpeg binary (either in PATH or in `./ffmpeg/bin/ffmpeg.exe` for Windows)

## How to Run

```bash
# Clone repo and enter folder
git clone https://github.com/YourUser/Video Downloader.git
cd Video Downloader

extract ffmpeg.rar

# (Optional) create virtual environment
python -m venv .venv
# Windows
.venv\Scripts\activate
# macOS/Linux
source .venv/bin/activate

# Install dependencies
pip install -r requirements.txt
# Run
python main.py
```

## How to Build (Windows)

```bash
pyinstaller --onefile --noconsole --icon=icon.ico --name "VideoToMP3" `  --add-binary ".\ffmpeg\bin\ffmpeg.exe;ffmpeg\bin"`
  --add-data "icon.ico;." `
  main.py
```

* Output will be in `dist/Video_Downloader.exe`
* `--add-binary` bundles FFmpeg so users don’t need to install it
* `--add-data` includes extra files like icons

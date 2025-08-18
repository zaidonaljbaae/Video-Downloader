# Vedio_Downloader.spec
# Build with:  pyinstaller --clean -y Vedio_Downloader.spec

import os
block_cipher = None

proj = os.path.dirname(os.path.abspath(__name__))

a = Analysis(
    ['main.py'],
    pathex=[proj],
    binaries=[
        # ضمّ FFmpeg داخل dist\Vedio_Downloader\ffmpeg\bin\
        (os.path.join(proj, 'ffmpeg', 'bin', 'ffmpeg.exe'), 'ffmpeg\\bin'),
        (os.path.join(proj, 'ffmpeg', 'bin', 'ffprobe.exe'), 'ffmpeg\\bin'),
    ],
    datas=[
        (os.path.join(proj, 'logo.ico'), '.'),
    ],
    hiddenimports=[
        'ttkbootstrap',
        'yt_dlp',
        # 'ttkbootstrap.themes'
    ],
    hookspath=[],
    runtime_hooks=[],
    excludes=[],
    noarchive=False
)

pyz = PYZ(a.pure, a.zipped_data, cipher=block_cipher)

exe = EXE(
    pyz,
    a.scripts,
    a.binaries,
    a.zipfiles,
    a.datas,
    name='Vedio_Downloader',
    debug=False,
    bootloader_ignore_signals=False,
    strip=False,
    upx=False,
    console=False,
    icon=os.path.join(proj, 'logo.ico')
)

coll = COLLECT(
    exe,
    a.binaries,
    a.zipfiles,
    a.datas,
    strip=False,
    upx=False,
    name='Vedio_Downloader'
)

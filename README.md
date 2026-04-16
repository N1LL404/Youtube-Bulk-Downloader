# YouTube Bulk Downloader

A desktop bulk YouTube downloader built with `PyQt6` and `yt-dlp`.

It is designed for downloading many YouTube links in one batch, with support for:

- Multiple video URLs at once
- Parallel video downloads
- Per-video progress bars
- Pause and stop controls
- Quality selection
- Local `ffmpeg` support for merging audio/video
- Windows `.exe` build with PyInstaller

## Files

- `youtube_downloader_gui.py` - main app
- `build_exe.bat` - builds the Windows executable
- `YouTubeBulkDownloader.spec` - PyInstaller spec file
- `ffmpeg/` - bundled `ffmpeg` files used by the app/build

## Requirements

Recommended environment:

- Windows
- Python 3.10+ or newer
- Internet connection

Python packages:

- `PyQt6`
- `yt-dlp`
- `psutil`
- `pyinstaller` for building the `.exe`

## Install Dependencies

Run this in the project folder:

```powershell
pip install PyQt6 yt-dlp psutil pyinstaller
```

If `pip` is not available, try:

```powershell
py -3 -m pip install PyQt6 yt-dlp psutil pyinstaller
```

## Run From Source

```powershell
python youtube_downloader_gui.py
```

Or:

```powershell
py -3 youtube_downloader_gui.py
```

## How To Use

1. Open the app.
2. Paste one YouTube link per line in the `Video Links` box.
3. Choose an output folder with `Browse`.
4. Choose a `Quality`.
5. Choose `Fragment Threads`.
6. Choose `Parallel`.
7. Click `Download Video`.

## What The Controls Mean

### Quality

Chooses the maximum video quality the app will try to download.

Examples:

- `Highest Quality`
- `720p`
- `1080p`
- `2K (1440p)`

### Fragment Threads

Controls how many fragments `yt-dlp` can download at once for a single video.

- Higher value can speed up one download
- Too high can use more network and CPU

Good starting range:

- `4` to `8`

### Parallel

Controls how many different videos download at the same time.

- Higher value = faster bulk downloads
- Too high can overload your internet or PC

Good starting range:

- `2` to `5`

## Status Meanings

Each row in the download sheet shows one of these states:

- `PENDING` - waiting to start
- `DOWNLOADING` - currently downloading
- `RETRYING` - retrying after a failed attempt
- `DONE` - download finished successfully
- `FAILED` - failed after retry
- `STOPPED` - manually stopped

## Buttons

- `Download Video` - starts the batch
- `Pause` - pauses active downloads
- `Stop` - stops active downloads
- `Clear` - clears the URL input and sheet when no batch is running

## Build The EXE

To build the Windows executable:

```powershell
build_exe.bat
```

What it does:

- checks that `youtube_downloader_gui.py` exists
- checks that `ffmpeg\bin\ffmpeg.exe` exists
- installs build tools
- cleans old build folders
- creates `dist\YouTubeBulkDownloader.exe`

Output file:

```text
dist\YouTubeBulkDownloader.exe
```

## Notes About ffmpeg

The app first looks for bundled `ffmpeg` inside:

```text
ffmpeg\bin\ffmpeg.exe
```

If it is not found, it tries system `ffmpeg` from `PATH`.

If `ffmpeg` is missing:

- some best-quality downloads may not merge properly
- progressive streams may still work

## Troubleshooting

### The app says `PyQt6 is not installed`

Install it with:

```powershell
pip install PyQt6
```

### The app says `yt-dlp is not installed`

Install it with:

```powershell
pip install yt-dlp
```

### The system stats do not work

Install:

```powershell
pip install psutil
```

### Downloads are too slow

Try:

- increasing `Parallel`
- increasing `Fragment Threads`
- using a lower quality
- checking your internet speed

### The app freezes or slows down with huge batches

Try:

- `Parallel = 2` or `3`
- `Fragment Threads = 4` or `5`
- splitting extremely large batches into smaller groups

## Disclaimer

Use this tool responsibly and make sure you follow YouTube's terms and the copyright laws in your country.

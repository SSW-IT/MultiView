# MultiView (Advanced Webtoon/Image Viewer & Downloader)

**A high-performance multi-image viewer that supports seamless local viewing and automated webtoon series downloading.**

<img width="276" height="298" alt="화면 캡처 2026-03-23 195346" src="https://github.com/user-attachments/assets/5efc51c5-aa96-49e4-832b-bb0bfe2129a2" />
<img width="1274" height="951" alt="화면 캡처 2026-03-23 195314" src="https://github.com/user-attachments/assets/8bc61dc5-8388-4e8e-8475-98266d527a19" />


##  Key Features

- **Non-blocking Background Downloading**
  - Continue browsing other content while downloading entire series in the background.

- **Virtualized Rendering Engine**
  - Efficiently handles large ZIP files (thousands of images) with dynamic loading/unloading.

- **Robust Web Scraping (Playwright)**
  - Uses real browser automation to bypass lazy loading and dynamic content issues.

- **Smart Episode Navigation**
  - Instantly jump to any episode inside merged ZIP files using numeric search.


##  Features

- Open local folders / ZIP files
- Open webtoon URLs
- Download current episode
- Full-series auto download (`Ctrl+Shift+D`)
- Series download manager UI (`Ctrl+Shift+Q`)
  - Queue list
  - Remaining tasks
  - Completed items
  - Failed logs
  - Progress bar
- Continue browsing during downloads
- Previous / Next episode navigation
- Episode search (`Ctrl+J`)
- Zoom & fit mode (`Ctrl + Wheel`, `Ctrl+F`)
- Slideshow mode
- Bookmark system
- Built-in help (`F1`)

##Project Overview

### What is this?

**MultiView** is a desktop application designed to unify fragmented webtoon viewing experiences.

It supports:
- Local images, folders, and ZIP files
- Remote webtoon URLs
- Background full-series downloading

All displayed in a **gapless vertical scrolling viewer** optimized for reading.

### Why was it built?

Existing viewers often suffer from:
- Freezing or memory overflow with large ZIP files
- Broken downloads due to lazy loading and anti-bot protections
- Lack of integration between viewing and downloading

MultiView was built to solve these issues at a structural level.

### How were these problems solved?

1. **Memory Optimization**
   - Only images within the visible viewport are loaded into memory
   - Off-screen images are immediately released

2. **Reliable Web Scraping**
   - Playwright simulates a real browser environment
   - JavaScript scrolling triggers lazy-loaded images properly

3. **Non-blocking UI**
   - Viewer and downloader run in separate threads
   - UI remains fully responsive during long downloads

## Project Structure

MultiView/
├─ main.py # Entry point
├─ viewer.py # UI and user interaction
├─ loader.py # Image loading (local/ZIP/remote)
├─ downloader.py # Download engine (single + series)
├─ scraper_bridge.py # Web scraping logic
├─ bookmark.py # Bookmark system
├─ requirements.txt
├─ README.md
└─ docs/
└─ screenshots/

## Tech Stack

- **Language:** Python 3.10+
- **GUI:** PySide6 (Qt)
- **Web Scraping:** Playwright, requests
- **Image Processing:** Pillow
- **Concurrency:** QThread, ThreadPoolExecutor

## Installation

```bash
pip install -r requirements.txt
playwright install
python main.py
```

## key Shortcuts
Ctrl+L : Open local files
Ctrl+Shift+R : Open webtoon URL
Ctrl+Shift+D : Start full-series download
Ctrl+Shift+Q : Open download manager
Ctrl+J : Jump to episode
[ / ] : Previous / Next episode
Ctrl+F : Toggle fit mode
Ctrl+. : Cancel loading
F1 : Help
F11 : Fullscreen

## Implementation Highlights
-Asynchronous Loading with QThread
	- Ensures smooth UI performance without blocking during heavy image loading.

-Viewport-based Image Loading
	- Only images near the visible area are loaded into memory.
	- Distant images are automatically released to reduce memory usage.
-Progressive Image Strategy
	-Thumbnails are loaded first for fast rendering.
	-Full-resolution images are requested only for visible regions.
-Cancelable Loading System
	-Ongoing image loading can be safely interrupted at any time.
-Flexible Remote Input Support
	-Supports multiple input formats:
		-Single image URL
		-List of URLs
		-HTTP directory index
		-UNC (network) folders
-Webtoon Episode Tracking & Series Download
	-Automatically detects and follows episode links from webtoon URLs.
	-Supports full-series downloading from a starting episode.
-Non-blocking User Experience
	-Users can continue browsing other content while downloads are running in the background.

## Build (Portable EXE)

pyinstaller ^
  --noconfirm ^
  --clean ^
  --windowed ^
  --onedir ^
  --name "MultiView" ^
  --icon=app.ico ^
  --collect-all PySide6 ^
  --hidden-import PySide6.QtCore ^
  --hidden-import PySide6.QtGui ^
  --hidden-import PySide6.QtWidgets ^
  --hidden-import PySide6.QtNetwork ^
  --hidden-import requests ^
  main.py

## Test Checklist

-Open folder
-Open ZIP
-Navigate episodes
-Open webtoon URL
-Download current episode
-Full-series download
-Background browsing during download
-Bookmark save/load
-Help (F1)


## Notes

Webtoon website structures may change over time, which can require updates to the URL parsing logic.
Features based on Playwright depend on the availability and correct installation of browser resources.
It is recommended not to upload dist/, build/, or virtual environment folders to GitHub.

Disclaimer

This project is intended for educational and research purposes only.
Downloading or distributing copyrighted content without permission may violate copyright laws.
The developer is not responsible for any misuse of this software.
Use responsibly and legally.

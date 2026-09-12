# SuperCut Studio

SuperCut Studio turns long recordings into keyword supercuts. It transcribes every
video with word-level timestamps (CrisperWhisper Turbo or faster-whisper), finds the
moments where your keywords are spoken, lets you audition and trim each hit, then
renders a hardware-accelerated highlight reel with optional karaoke captions,
subtitles, thumbnails and NLE timelines.

Version 3 ships as a native Windows desktop application (`SuperCutStudio-Setup-3.0.0.exe`)
built from the web studio, plus browser and command-line modes.

## Highlights

- **Two speech engines** – CrisperWhisper 2.0 Turbo (verbatim, precise word timing) or any
  faster-whisper model (`large-v3-turbo`, `large-v3`, `distil-large-v3`, `medium`, ...). If
  CrisperWhisper is not installed the app falls back to Whisper automatically. Transcripts are
  cached, so re-running with new keywords is instant.
- **Keyword matrix** – phrases, plurals, exclusions, regex, fuzzy matching, sentence expansion,
  silence snapping, per-side padding, clip merging, max clip length, high-energy speech
  detection and one-click **Auto-discover** of the strongest topics in the audio.
- **Reviewer cockpit** – real audio waveform, precision scrubber, IN/OUT trimming with keyboard
  nudges, manual clips from markers or transcript segments, search/filter/sort, batch select,
  per-clip captions.
- **Transcript inspector** – word-level keyword highlighting, search, click-to-seek, TXT/SRT/JSON
  export.
- **Rendering** – NVENC (HEVC/H.264/AV1) with CPU fallback, quality presets, 16:9 / 9:16 blur
  canvas / 1:1 conforming, crossfade / dip-to-black / swipe transitions, EBU R128 loudness,
  **fast-seek rendering** (only the clip ranges are decoded, an order of magnitude faster on long
  sources), burned-in karaoke captions in five styles.
- **Deliverables** – master supercut, per-video reels, individual clips, SRT/VTT/ASS, full
  transcripts, Premiere/FCP XML, DaVinci EDL, thumbnails, contact sheets, JSON/CSV reports.
- **Desktop app** – native window (Edge WebView2), native file pickers, per-user settings and
  caches under `%LOCALAPPDATA%\SuperCutStudio`, renders default to `Videos\SuperCut Output`,
  rotating logs, single installer with bundled FFmpeg.

## Install (end users)

1. Run `SuperCutStudio-Setup-<version>.exe` (from `dist\` after building, see below).
2. Launch **SuperCut Studio** from the Start menu.
3. Pick a folder or file, add keywords, click **Start SuperCut**.

The first transcription downloads the selected Whisper model into the Hugging Face cache
(internet required once). CrisperWhisper Turbo needs a Python 3.12 environment with the
`crisperwhisper` package; the app finds `crisper-venv` next to it, the Video Dub
`2A-TST-Desktop\.omnivoice` environment, or the interpreter named in `VKS_CRISPER_PYTHON`.

An NVIDIA GPU is used automatically for both transcription (CUDA) and encoding (NVENC); everything
falls back to CPU when it is missing.

## Run from source

Prerequisites: Python 3.10+, FFmpeg on `PATH` (or set `VKS_FFMPEG`), Node.js (only for the
browser test suite).

```bat
setup.bat            :: creates venv and installs requirements.txt
run_desktop.bat      :: native desktop window
run_web_studio.bat   :: browser mode at http://127.0.0.1:8080
run_studio.bat       :: legacy CustomTkinter studio
```

Command line:

```bat
venv\Scripts\python cli.py -i "D:\Videos" -k "launch,rocket" -a 9:16 -t crossfade --subtitles
```

## Build the installer

```bat
build_installer.bat
```

`build\build_installer.ps1` runs the tests, copies FFmpeg into the bundle, runs PyInstaller
(`build\SuperCutStudio.spec`) and compiles `build\installer.iss` with Inno Setup
(`winget install JRSoftware.InnoSetup`). Output: `dist\SuperCutStudio-Setup-<version>.exe`.
Flags: `-SkipTests`, `-NoFfmpeg`, `-NoInstaller`, `-FfmpegDir <path>`.

## Tests

```bat
venv\Scripts\python -m pytest -q
node tests\test_browser_frontend.js
```

The pytest suite covers the matcher, boundary optimiser, subtitles, NLE exports, thumbnails,
encoder fallbacks, red-team robustness cases, a live FFmpeg end-to-end pipeline, the CustomTkinter
studio and the HTTP API. The Node suite drives the real inlined single-page app in JSDOM.

## Layout

```
desktop.py            native desktop shell (PyInstaller entry point)
web_app.py            FastAPI server + JSON/SSE API, serves static/ inlined
static/               index.html, app.css, app.js (offline, no CDN)
cli.py                command-line pipeline
studio_app.py         legacy CustomTkinter desktop studio
supercut/             engine: models, pipeline, asr/, audio/, search/, video/, export/, server/, paths.py
build/                PyInstaller spec, Inno Setup script, build script
tests/                pytest + JSDOM suites
```

Data locations: settings/caches/logs live in `%LOCALAPPDATA%\SuperCutStudio` for the installed
app and in `.supercut_data\` when running from source (override with `VKS_DATA_DIR`).

## Keyboard shortcuts (Reviewer)

`Space` play/pause · `J`/`L` ±2 s · `←`/`→` ±0.1 s · `S` toggle clip · `N`/`P` next/previous ·
`Enter` play clip · `I`/`O` set IN/OUT · `[`/`]` nudge · `Home`/`End` jump · `Delete` remove ·
`1`–`4` tabs · `Esc` stop job · `?` help

## License notes

CrisperWhisper model weights are licensed for non-commercial research by Nyra Labs. FFmpeg builds
bundled by the installer are GPL; source is available from the build's upstream (gyan.dev / BtbN).

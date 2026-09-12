# Project: VideoKeywordStitcher Web Studio Modernization

## Architecture
- **Backend**: FastAPI (`web_app.py`) providing REST API endpoints (`/api/config`, `/api/scan`, `/api/clips`, `/api/clips/update`, `/api/transcript`, `/api/result`), HTTP 206 Partial Content Range streaming (`/api/media`), and Server-Sent Events telemetry (`/api/events`).
- **Frontend**: Single Page Application embedded in `web_app.py` (`SPA_HTML`), rendered with Tailwind CSS dark-mode glassmorphic theme, pure Vanilla ES6+ JavaScript, 2D HTML5 Canvas waveform visualizer, and custom timeline scrubber.
- **Testing Architecture**: In-engine headless DOM & JavaScript verification suite (`tests/test_browser_frontend.js`) using Node.js and JSDOM, plus backend pytest suite (`tests/test_web_app.py`).

## Feature Inventory
| # | Feature | Description | Milestone | Source |
|---|---------|-------------|-----------|--------|
| 1 | Glassmorphic UI & 4-Tab Navigation | Responsive dark-mode cockpit with Setup, Reviewer, Transcript, and Export tabs preserving state | M1 | ORIGINAL_REQUEST §R1 |
| 2 | Input Mode & Path Sanitization | Dynamic toggle between Single File and Folder modes, auto-stripping semicolons/quotes | M1 | ORIGINAL_REQUEST §R1 |
| 3 | Drag-and-Drop Staging | File drop zone with click delegation preventing recursion loops | M1 | ORIGINAL_REQUEST §R1 |
| 4 | Multi-Delimiter Keyword Parsing | Dynamic chip additions splitting commas and semicolons | M1 | ORIGINAL_REQUEST §R1 |
| 5 | Non-Blocking Toast System | Light/animated toast container replacing all blocking `window.alert()` calls | M2 | ORIGINAL_REQUEST §R1 |
| 6 | Audio Waveform Visualizer | High-DPI 2D canvas rendering dynamic audio bars with active clip intervals | M3 | ORIGINAL_REQUEST §R2 |
| 7 | Precision Timeline Scrubber | Sub-second timeline scrubber (`step="0.001"`) with pointer capture and `MM:SS.mmm` timecodes | M3 | ORIGINAL_REQUEST §R2 |
| 8 | Clip Boundary Indicators | Overlay region marking `IN` / `OUT` bounds, clip range badges, and jump controls | M3 | ORIGINAL_REQUEST §R2 |
| 9 | Player Keyboard Navigation | Spacebar (play/pause), J/L (-2s / +2s jump), S (selection toggle with API sync) | M4 | ORIGINAL_REQUEST §R3 |
| 10 | Input Focus Isolation | Ignore player shortcuts when `<input>`, `<textarea>`, or `<select>` are focused | M4 | ORIGINAL_REQUEST §R3 |
| 11 | Headless DOM Verification Harness | 18 test cases in `tests/test_browser_frontend.js` passing cleanly with 0 errors | M5 | ORIGINAL_REQUEST §R4 |

## Milestones
| # | Name | Scope | Dependencies | Status |
|---|------|-------|-------------|--------|
| M1 | UI Structure & Tab State | Tabs, file/folder modes, drag-and-drop, keyword chips | none | DONE |
| M2 | Toast Notifications & Alert Routing | `#toast-container`, `showToast()`, `window.alert` hijacking | M1 | DONE |
| M3 | Audio Waveform & Precision Scrubber | Canvas waveform, timeline scrubber, `MM:SS.mmm` timecodes | M2 | DONE |
| M4 | Keyboard Navigation & Focus Isolation | Space, J, L, S shortcuts, input focus guard | M3 | DONE |
| M5 | Quality Hardening & Test Verification | Headless DOM tests (18/18 pass), pytest (12/12 pass), headless tailwind guard | M4 | DONE |

## Interface Contracts
### Player ↔ Scrubber
- `seekToPercentage(pct, smooth)`: Updates `player.currentTime = (pct / 100) * duration`, updates playhead position, calls `formatTimeSec(t)` for badge readouts.
- `updateScrubberUI()`: Synchronizes `#player-scrubber.value`, `#waveform-playhead.style.left`, `#scrubber-current-time.textContent`.

### Keyboard Handler ↔ API State
- Key `S`: Inverts `activePlayingClip.selected`, invokes `POST /api/clips/update` with `{ video_path, clip_index, selected, custom_title }`.

## Code Layout
- `web_app.py`: FastAPI server + `SPA_HTML` frontend (HTML, Tailwind CSS styles, Vanilla JS logic).
- `tests/test_browser_frontend.js`: In-engine headless DOM & JS test suite (18 tests).
- `tests/test_web_app.py`: Backend integration test suite (12 tests).

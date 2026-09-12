# Original User Request

## Initial Request — 2026-09-03T19:35:30-05:00

Modernize and harden the VideoKeywordStitcher Web Studio frontend into a high-performance, accessible, and responsive multimodal video cockpit with real-time clip scrubbing, waveform visualization, non-blocking toast notifications, and keyboard navigation.

Working directory: c:/Users/WontML/dev/VideoKeywordStitcher
Integrity mode: development

## Requirements

### R1. Responsive Web Studio UI & Non-Blocking Notifications
Modernize the single-page application interface (web_app.py) with responsive dark-mode glassmorphic aesthetics, high-contrast accessible controls, and lightweight visual toast/banner notifications that completely replace intrusive native browser lert() popups.

### R2. Interactive Audio Waveform & Precision Scrubber
Implement an interactive audio waveform visualizer and precision timeline scrubber bar for instant auditioning of keyword clips. Ensure smooth seeking, clear active clip boundary indicators, and accurate timecode displays (MM:SS.mmm).

### R3. Accessible Player Keyboard Navigation & Focus Isolation
Provide responsive keyboard shortcuts in the audition player:
- [Spacebar] toggle playback (play/pause).
- ['J'] / ['L'] jump backward / forward 2 seconds.
- ['S'] toggle selection of the active clip with immediate API state synchronization.
Ensure proper input focus isolation so typing into text inputs does not accidentally trigger player controls.

### R4. Automated In-Engine Browser DOM & State Verification
Maintain comprehensive headless DOM and state verification tests (	ests/test_browser_frontend.js) validating all interactive elements, mode toggles, path sanitization, keyboard shortcuts, and visual toasts with zero console syntax errors or stack overflows.

## Acceptance Criteria

### Browser DOM & Functional Verification
- [ ] Script parsing loads cleanly without JavaScript syntax errors or unescaped regular expression literals.
- [ ] Tab switching between Setup and Reviewer preserves state without page reloads.
- [ ] Single File and Folder mode toggles update input placeholders and scan configurations dynamically.
- [ ] Drag-and-drop zone triggers file upload cleanly without recursive event loops.
- [ ] Visual toast notifications render non-blockingly with distinct types (info, success, rror) and auto-dismiss.
- [ ] Window alerts are seamlessly routed to the non-blocking toast container.
- [ ] Timeline scrubber accurately seeks to relative percentage positions and updates current time displays.
- [ ] Player keyboard shortcuts ([Space], [J], [L], [S]) control audition playback and selection.
- [ ] Keyboard events are ignored when text input fields or textareas are focused.
- [ ] All 18 browser verification tests in 	ests/test_browser_frontend.js execute and pass with 0 errors.

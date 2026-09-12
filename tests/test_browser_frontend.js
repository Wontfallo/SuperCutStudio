/**
 * In-engine DOM & JavaScript verification for the SuperCut Studio front end.
 * Executes the real inlined SPA (HTML + CSS + JS as served by web_app.py) inside
 * JSDOM with mocked fetch / EventSource and drives it like a user would.
 */

const fs = require('fs');
const path = require('path');
const { JSDOM } = require('jsdom');

let passed = 0;
function pass(msg) { passed += 1; console.log(`[PASS ${passed}] ${msg}`); }
function assert(cond, msg) { if (!cond) throw new Error(msg); }
const tick = (ms = 20) => new Promise((r) => setTimeout(r, ms));

async function runBrowserTests() {
    console.log('--- SuperCut Studio: in-engine browser DOM & JS test suite ---');

    const { execSync } = require('child_process');
    const venvWin = path.join(__dirname, '..', 'venv', 'Scripts', 'python.exe');
    const venvPosix = path.join(__dirname, '..', 'venv', 'bin', 'python');
    let pythonExe = 'python';
    if (process.env.PYTHON && fs.existsSync(process.env.PYTHON)) pythonExe = process.env.PYTHON;
    else if (fs.existsSync(venvWin)) pythonExe = venvWin;
    else if (fs.existsSync(venvPosix)) pythonExe = venvPosix;
    const htmlContent = execSync(`"${pythonExe}" -c "import sys, web_app; sys.stdout.buffer.write(web_app.SPA_HTML.encode('utf-8'))"`, {
        cwd: path.join(__dirname, '..'),
        maxBuffer: 20 * 1024 * 1024,
    }).toString('utf8');

    assert(!htmlContent.includes('cdn.tailwindcss.com'), 'SPA must not depend on a CDN');
    assert(!htmlContent.includes('fonts.googleapis.com'), 'SPA must not depend on webfonts');
    assert(htmlContent.includes('<style>') && htmlContent.includes('initKeyboardShortcuts'), 'CSS and JS must be inlined');

    const fetchCalls = [];
    const serverClips = [];
    let serverConfig = { input_path: '', input_mode: 'Folder', keywords: ['quantum'], exclude_keywords: [], aspect_ratio: 'source', transition: 'cut', model: 'crisper-turbo', output_dir: 'C:/out', pre_pad: 1, post_pad: 1 };

    const dom = new JSDOM(htmlContent, { runScripts: 'dangerously', resources: 'usable', url: 'http://localhost:8080/', pretendToBeVisual: true });
    const { window } = dom;
    const { document } = window;

    // Silence expected JSDOM "not implemented" noise (media playback, scrollIntoView)
    const origError = console.error;
    console.error = (...args) => { const s = String(args[0] || ''); if (s.includes('Not implemented')) return; origError(...args); };
    window.HTMLMediaElement.prototype.play = async function () { this._paused = false; };
    window.HTMLMediaElement.prototype.pause = function () { this._paused = true; };
    window.HTMLMediaElement.prototype.load = function () {};
    window.HTMLElement.prototype.scrollIntoView = function () {};

    const json = (obj) => ({ ok: true, status: 200, json: async () => obj, text: async () => JSON.stringify(obj) });
    window.fetch = async (url, opts = {}) => {
        fetchCalls.push({ url, opts });
        const u = String(url);
        if (u.startsWith('/api/config') && (!opts.method || opts.method === 'GET')) return json(serverConfig);
        if (u.startsWith('/api/config')) { serverConfig = Object.assign({}, serverConfig, JSON.parse(opts.body)); return json({ status: 'ok', config: serverConfig }); }
        if (u.startsWith('/api/system')) return json({ app: { version: '3.0.0', frozen: false, platform: 'test', python: '3.14', data_dir: 'C:/data', logs_dir: 'C:/logs' }, ffmpeg: { available: true, path: 'ffmpeg', version: 'ffmpeg version 8.0' }, encoders: { h264_nvenc: true, libx264: true }, nvenc: true, cuda: true, gpu: 'RTX 4090', asr: { crisper_available: true, crisper_python: 'C:/py/python.exe', models: [{ id: 'crisper-turbo', label: 'CrisperWhisper Turbo', available: true, recommended: true, detail: 'best' }, { id: 'large-v3-turbo', label: 'Whisper turbo', available: true }] }, transcript_cache: { count: 2, size_bytes: 1024 }, state: { status: 'idle' }, subtitle_styles: ['karaoke_amber'] });
        if (u.startsWith('/api/status')) return json({ status: 'idle', stage: 'Idle', pct: 0, msg: 'Ready', has_result: false });
        if (u.startsWith('/api/logs')) return json({ count: 0, logs: [] });
        if (u.startsWith('/api/jobs')) return json({ jobs: [] });
        if (u === '/api/scan') {
            const body = JSON.parse(opts.body || '{}');
            return json({ input_path: body.input_path, is_file: true, count: 1, total_duration: 12.5, files: [{ path: body.input_path, filename: 'test_video.mp4', resolution: '1920x1080', duration: 12.5, fps: 30, has_audio: true, video_codec: 'h264', size_bytes: 1000, readable: true }] });
        }
        if (u === '/api/clips') {
            const selected = serverClips.filter((c) => c.selected);
            return json({ count: serverClips.length, selected_count: selected.length, selected_footage: selected.reduce((a, c) => a + c.duration, 0), videos: serverClips.length ? [{ video_path: 'C:/sample.mp4', filename: 'sample.mp4', duration: 60, clips: serverClips.length, hits: serverClips.length }] : [], clips: serverClips });
        }
        if (u === '/api/clips/update') {
            const body = JSON.parse(opts.body);
            const c = serverClips.find((x) => x.clip_index === body.clip_index);
            if (c) { if (body.selected !== undefined) c.selected = body.selected; if (body.start !== undefined) c.start = body.start; if (body.end !== undefined) c.end = body.end; if (body.custom_title !== undefined) c.custom_title = body.custom_title; c.duration = c.end - c.start; }
            return json({ status: 'ok', clip: c });
        }
        if (u.startsWith('/api/clips/batch_toggle')) { const sel = u.includes('select_all=true'); serverClips.forEach((c) => { c.selected = u.includes('invert=true') ? !c.selected : sel; }); return json({ status: 'ok' }); }
        if (u === '/api/clips/delete') { const body = JSON.parse(opts.body); const i = serverClips.findIndex((c) => c.clip_index === body.clip_index); if (i >= 0) serverClips.splice(i, 1); return json({ status: 'ok', remaining: serverClips.length }); }
        if (u.startsWith('/api/transcript')) return json({ count: 1, transcripts: [{ video_path: 'C:/sample.mp4', filename: 'sample.mp4', duration: 60, language: 'en', model_name: 'crisper-turbo', word_count: 5, full_text: 'we love quantum physics here', cached: false, segments: [{ start: 1.0, end: 3.0, text: 'we love quantum physics', words: [{ text: 'we', start: 1.0, end: 1.2, confidence: 0.9 }, { text: 'love', start: 1.2, end: 1.5, confidence: 0.9 }, { text: 'quantum', start: 1.5, end: 2.0, confidence: 0.95 }, { text: 'physics', start: 2.0, end: 2.6, confidence: 0.9 }] }, { start: 10, end: 12, text: 'something else', words: [] }] }] });
        if (u.startsWith('/api/result')) return json({ has_result: false });
        if (u.startsWith('/api/waveform')) return json({ buckets: 4, peaks: [0.2, 0.8, 0.5, 0.1], duration: 60 });
        if (u === '/api/analyze' || u === '/api/pipeline/start' || u === '/api/render' || u === '/api/discover' || u === '/api/stitch-folder-only') return json({ status: 'started' });
        if (u === '/api/cancel') return json({ status: 'cancelled' });
        if (u === '/api/pick') return json({ status: 'ok', path: 'C:/picked/folder' });
        return json({});
    };

    const sseListeners = {};
    window.EventSource = class MockEventSource {
        constructor(url) { this.url = url; setTimeout(() => { if (this.onopen) this.onopen(); this.emit('init', { status: 'idle', stage: 'Idle', pct: 0, msg: 'Ready' }); }, 5); window.__sse = this; }
        addEventListener(event, cb) { (sseListeners[event] = sseListeners[event] || []).push(cb); }
        emit(event, data) { (sseListeners[event] || []).forEach((cb) => cb({ data: JSON.stringify(data) })); }
        close() {}
    };

    await new Promise((resolve) => { if (document.readyState === 'complete') resolve(); else window.addEventListener('DOMContentLoaded', resolve); });
    await tick(80);
    pass('DOMContentLoaded fired and inlined script parsed without syntax errors');

    // Config loaded into the form & chips rendered
    assert(document.querySelectorAll('#keyword-chips .chip').length === 1, 'Keyword chips should render from config');
    assert(document.getElementById('output-dir').value === 'C:/out', 'Output dir should be populated from config');
    pass('Configuration hydrated into form controls and keyword chips');

    // Model dropdown populated from /api/system
    const modelSel = document.getElementById('setting-model');
    assert(modelSel.options.length === 2, 'Model select should be populated from system info');
    assert(modelSel.value === 'crisper-turbo', 'Model select should keep configured model');
    pass('Speech model catalogue populated from system capabilities');

    // Tabs
    const tabBtnReviewer = document.getElementById('tab-btn-reviewer');
    const tabBtnSetup = document.getElementById('tab-btn-setup');
    tabBtnReviewer.click();
    assert(document.getElementById('tab-setup').classList.contains('hidden'), 'Setup hidden after switching');
    assert(!document.getElementById('tab-reviewer').classList.contains('hidden'), 'Reviewer visible');
    assert(tabBtnReviewer.classList.contains('active'), 'Reviewer tab button active');
    tabBtnSetup.click();
    assert(!document.getElementById('tab-setup').classList.contains('hidden'), 'Setup visible again');
    pass('Tab navigation (Setup ↔ Reviewer) preserves state without reload');

    // Mode toggle
    const btnFolder = document.getElementById('mode-folder-btn');
    const btnFile = document.getElementById('mode-file-btn');
    const inputPath = document.getElementById('input-path');
    btnFile.click();
    assert(btnFile.classList.contains('active') && !btnFolder.classList.contains('active'), 'File mode active');
    assert(inputPath.placeholder.includes('.mp4'), 'Placeholder updated for file mode');
    btnFolder.click();
    assert(btnFolder.classList.contains('active'), 'Folder mode active');
    assert(inputPath.placeholder.includes('Highlights'), 'Placeholder updated for folder mode');
    pass('Folder / Single File mode toggle updates state and placeholders');

    // Path sanitization + scan
    inputPath.value = '"G:\\1 - ClippedVideos\\input video\\Pixie Snow\\test.mp4"; ';
    await window.scanMediaSource();
    assert(!inputPath.value.includes(';') && !inputPath.value.includes('"'), 'Quotes/semicolon stripped');
    const scanCall = fetchCalls.find((c) => c.url === '/api/scan');
    assert(scanCall, '/api/scan called');
    const scanBody = JSON.parse(scanCall.opts.body);
    assert(scanBody.input_path === 'G:\\1 - ClippedVideos\\input video\\Pixie Snow\\test.mp4', 'Sanitized path sent: ' + scanBody.input_path);
    assert(!document.getElementById('scanned-assets-container').classList.contains('hidden'), 'Scanned assets visible');
    assert(document.getElementById('scanned-list').children.length === 1, 'Scanned file rendered');
    assert(btnFile.classList.contains('active'), 'Scan of a file switches to File mode');
    pass('Path sanitization, scan request and probed asset card verified');

    // Folder scan: remove / restore files
    window.fetch = ((orig) => async (url, opts) => {
        if (String(url) === '/api/scan') return json({ input_path: 'C:/vids', is_file: false, count: 2, active_count: 2, total_duration: 20, files: [{ path: 'C:/vids/a.mp4', filename: 'a.mp4', resolution: '1920x1080', duration: 10, fps: 30, has_audio: true, video_codec: 'h264', size_bytes: 10, readable: true, excluded: false }, { path: 'C:/vids/b.mp4', filename: 'b.mp4', resolution: '1920x1080', duration: 10, fps: 30, has_audio: true, video_codec: 'h264', size_bytes: 10, readable: true, excluded: false }] });
        if (String(url) === '/api/reset') { fetchCalls.push({ url, opts }); return json({ status: 'ok' }); }
        return orig(url, opts);
    })(window.fetch);
    inputPath.value = 'C:/vids';
    await window.scanMediaSource();
    let toggles = document.querySelectorAll('#scanned-list .file-toggle');
    assert(toggles.length === 2, 'Each folder file gets a remove button');
    toggles[1].click();
    assert(document.querySelectorAll('#scanned-list .file-item.excluded').length === 1, 'Removed file greyed out');
    assert(document.getElementById('scanned-count').textContent === '1 of 2', 'Count shows active of total');
    assert(window.collectCurrentConfig().excluded_files.includes('C:/vids/b.mp4'), 'Exclusion goes into config');
    document.querySelectorAll('#scanned-list .file-toggle')[1].click();
    assert(document.querySelectorAll('#scanned-list .file-item.excluded').length === 0, 'Restore puts it back');
    pass('Folder scan lets you remove and restore individual files');

    // Drop zone
    const dropZone = document.getElementById('drop-zone');
    const fileUploader = document.getElementById('file-uploader');
    let uploaderClicked = false;
    fileUploader.click = () => { uploaderClicked = true; };
    dropZone.click();
    assert(uploaderClicked, 'Drop zone click opens uploader');
    pass('Drop zone click triggers the file uploader without recursion');

    // Keyword chips
    const kwInput = document.getElementById('keyword-input');
    kwInput.value = 'viral, Highlight; blockbuster,viral';
    window.addKeywordFromInput();
    const chipTexts = Array.from(document.querySelectorAll('#keyword-chips .chip span')).map((s) => s.textContent.trim());
    assert(['quantum', 'viral', 'highlight', 'blockbuster'].every((k) => chipTexts.includes(k)), 'Chips: ' + chipTexts.join(','));
    assert(chipTexts.length === 4, 'Duplicates ignored');
    window.removeKeyword(0);
    assert(document.querySelectorAll('#keyword-chips .chip').length === 3, 'Chip removed');
    pass('Keyword chips: multi-delimiter add, case folding, dedupe and removal');

    // Exclude chips + autosave
    document.getElementById('exclude-input').value = 'sponsor; intro';
    window.addExcludeFromInput();
    assert(document.querySelectorAll('#exclude-chips .chip').length === 2, 'Exclude chips rendered');
    await tick(900);
    const saveCall = fetchCalls.filter((c) => c.url === '/api/config' && c.opts.method === 'POST').pop();
    assert(saveCall, 'Config autosaved');
    const savedBody = JSON.parse(saveCall.opts.body);
    assert(savedBody.exclude_keywords.includes('sponsor') && savedBody.keywords.includes('viral'), 'Autosave payload includes chips');
    pass('Exclude keywords and debounced autosave persisted to /api/config');

    // Toasts
    window.showToast('Test visual error notification', 'error');
    const toastItem = document.querySelector('#toast-container .toast-item');
    assert(toastItem && toastItem.textContent.includes('Test visual error notification'), 'Toast rendered');
    assert(toastItem.classList.contains('toast-error'), 'Toast has error theme');
    window.alert('Intercepted window alert message');
    assert(Array.from(document.querySelectorAll('#toast-container .toast-item')).some((t) => t.textContent.includes('Intercepted window alert message')), 'alert routed to toast');
    pass('Non-blocking toasts render and window.alert is intercepted');

    // Validation: analyze without input path
    inputPath.value = '';
    await window.triggerAnalyze();
    assert(!fetchCalls.some((c) => c.url === '/api/analyze'), 'Analyze blocked without input');
    assert(!document.getElementById('tab-setup').classList.contains('hidden'), 'Switched back to setup for validation');
    pass('Client-side validation blocks jobs without an input path');

    // Start a job -> running state, then SSE completes it
    inputPath.value = 'C:/sample.mp4';
    await window.trigger1ClickSuperCut();
    assert(fetchCalls.some((c) => c.url === '/api/pipeline/start'), '/api/pipeline/start called');
    assert(!document.getElementById('btn-cancel').classList.contains('hidden'), 'Stop button visible while running');
    assert(document.getElementById('btn-start-stitching').disabled, 'Start button disabled while running');
    window.__sse.emit('progress', { pct: 42.5, msg: 'Transcribing sample.mp4', stage: 'SuperCut', elapsed: 12, eta: 16 });
    assert(document.getElementById('telemetry-pct').textContent === '42.5%', 'Progress percent updated');
    assert(document.getElementById('telemetry-fill').style.width === '42.5%', 'Progress bar width updated');
    assert(document.getElementById('telemetry-msg').textContent.includes('Transcribing'), 'Progress message shown');
    window.__sse.emit('log', { level: 'hit', text: 'sample.mp4: 3 keyword hit(s)', time: '12:00:00' });
    assert(document.getElementById('log-console').textContent.includes('3 keyword hit(s)'), 'Log line appended');
    pass('Job start toggles running UI; SSE progress and log events update telemetry');

    // Analysis complete -> clips load
    serverClips.push(
        { video_path: 'C:/sample.mp4', video_filename: 'sample.mp4', video_index: 0, clip_index: 1, start: 4.0, end: 8.0, duration: 4.0, selected: true, primary_keyword: 'quantum', keywords: ['quantum'], matched_text: 'quantum physics', confidence: 0.95, custom_title: '' },
        { video_path: 'C:/sample.mp4', video_filename: 'sample.mp4', video_index: 0, clip_index: 2, start: 20.0, end: 23.5, duration: 3.5, selected: true, primary_keyword: 'viral', keywords: ['viral'], matched_text: 'went viral', confidence: 0.5, custom_title: '' },
    );
    window.__sse.emit('analyzed', { stats_count: 1, total_hits: 2, total_clips: 2 });
    await tick(60);
    assert(document.getElementById('btn-cancel').classList.contains('hidden'), 'Stop hidden after completion');
    assert(document.getElementById('badge-clip-count').textContent === '2', 'Clip badge count = 2');
    assert(document.querySelectorAll('#clips-tbody tr.clip-row').length === 2, 'Two clip rows rendered');
    assert(!document.getElementById('tab-reviewer').classList.contains('hidden'), 'Auto-switched to reviewer');
    pass('SSE "analyzed" loads clips into the reviewer table and switches tabs');

    // Filtering & sorting
    document.getElementById('clip-search').value = 'viral';
    window.renderClipsTable();
    assert(document.querySelectorAll('#clips-tbody tr.clip-row').length === 1, 'Search filter narrows rows');
    document.getElementById('clip-search').value = '';
    document.getElementById('clip-sort').value = 'confidence';
    window.renderClipsTable();
    const firstRowIdx = document.querySelector('#clips-tbody tr.clip-row').id;
    assert(firstRowIdx === 'clip-row-1', 'Sort by confidence puts the low-confidence clip first');
    document.getElementById('clip-sort').value = 'time';
    window.renderClipsTable();
    pass('Clip table search filter and sorting work');

    // Player mocks
    const studioPlayer = document.getElementById('studio-video-player');
    let playerPaused = true, playerTime = 0.0;
    Object.defineProperty(studioPlayer, 'paused', { get: () => playerPaused, configurable: true });
    Object.defineProperty(studioPlayer, 'currentTime', { get: () => playerTime, set: (v) => { playerTime = v; }, configurable: true });
    Object.defineProperty(studioPlayer, 'duration', { value: 60.0, writable: true, configurable: true });
    Object.defineProperty(studioPlayer, 'readyState', { value: 4, configurable: true });
    studioPlayer.play = async () => { playerPaused = false; };
    studioPlayer.pause = () => { playerPaused = true; };
    document.getElementById('player-waveform-canvas').getContext = () => ({ setTransform() {}, scale() {}, clearRect() {}, beginPath() {}, rect() {}, roundRect() {}, fill() {}, fillRect() {}, fillStyle: '' });

    // Audition a clip
    window.previewClip(0);
    await tick(30);
    assert(studioPlayer.currentTime === 4.0, 'Audition seeks to clip start');
    assert(studioPlayer.src.includes('/api/media?path=C%3A%2Fsample.mp4'), 'Player source set via media endpoint');
    assert(document.getElementById('clip-detail-keyword').textContent.includes('quantum'), 'Clip details show keyword');
    assert(!document.getElementById('waveform-clip-region').classList.contains('hidden'), 'Clip region overlay visible');
    assert(fetchCalls.some((c) => String(c.url).startsWith('/api/waveform')), 'Waveform peaks requested');
    assert(document.querySelector('#clips-tbody tr.clip-row.active').id === 'clip-row-0', 'Active row highlighted');
    pass('Audition loads media, seeks, requests real waveform and highlights the active row');

    // Scrubber seek & time formatting
    window.seekToPercentage(50, false);
    assert(studioPlayer.currentTime === 30, 'Seek to 50% = 30s');
    assert(document.getElementById('scrubber-current-time').textContent === '00:30.000', 'MM:SS.mmm formatting');
    assert(document.getElementById('scrubber-total-duration').textContent === '01:00.000', 'Duration formatted');
    pass('Timeline scrubber seeks precisely and formats MM:SS.mmm timecodes');

    // Keyboard shortcuts
    const key = (opts) => window.dispatchEvent(new window.KeyboardEvent('keydown', Object.assign({ bubbles: true }, opts)));
    playerPaused = true;
    key({ code: 'Space', key: ' ' });
    assert(!playerPaused, 'Space plays');
    key({ code: 'Space', key: ' ' });
    assert(playerPaused, 'Space pauses');
    studioPlayer.currentTime = 10.0;
    key({ code: 'KeyL', key: 'l' });
    assert(studioPlayer.currentTime === 12.0, 'L jumps +2s');
    key({ code: 'KeyJ', key: 'j' });
    assert(studioPlayer.currentTime === 10.0, 'J jumps -2s');
    key({ code: 'ArrowRight', key: 'ArrowRight' });
    assert(Math.abs(studioPlayer.currentTime - 10.1) < 1e-6, 'Arrow steps 0.1s');
    pass('Keyboard: Space play/pause, J/L ±2s, arrow frame stepping');

    key({ code: 'KeyS', key: 's' });
    await tick(30);
    const clipUpdateCall = fetchCalls.filter((c) => c.url === '/api/clips/update').pop();
    assert(clipUpdateCall && JSON.parse(clipUpdateCall.opts.body).selected === false, 'S toggles selection via API');
    assert(document.getElementById('curated-count').textContent === '1', 'Curated count updated after toggle');
    pass('Keyboard: S toggles the active clip with immediate API sync');

    key({ code: 'KeyN', key: 'n' });
    await tick(30);
    assert(window.activePlayingClip.clip_index === 2, 'N moves to next clip');
    key({ code: 'KeyP', key: 'p' });
    await tick(30);
    assert(window.activePlayingClip.clip_index === 1, 'P moves back');
    pass('Keyboard: N/P navigate between clips');

    // Trim via I/O markers and nudges
    studioPlayer.currentTime = 5.0;
    key({ code: 'KeyI', key: 'i' });
    await tick(30);
    assert(window.activePlayingClip.start === 5.0, 'I sets IN at playhead');
    key({ code: 'BracketRight', key: ']' });
    await tick(30);
    assert(Math.abs(window.activePlayingClip.end - 8.5) < 1e-6, '] nudges OUT +0.5');
    assert(document.getElementById('clip-detail-duration').textContent.includes('3.50s'), 'Details reflect new duration');
    pass('Clip trimming: IN marker and OUT nudge update the clip through the API');

    // Focus isolation
    kwInput.focus();
    studioPlayer.currentTime = 20.0;
    kwInput.dispatchEvent(new window.KeyboardEvent('keydown', { code: 'KeyL', key: 'l', bubbles: true }));
    assert(studioPlayer.currentTime === 20.0, 'Typing in inputs must not trigger shortcuts');
    kwInput.blur();
    pass('Input focus isolation: shortcuts ignored while typing');

    // Transcript inspector
    window.addKeyword('quantum');
    window.switchTab('transcript');
    window.renderTranscriptForSelectedVideo();
    const lines = document.querySelectorAll('.transcript-line');
    assert(lines.length === 2, 'Transcript lines rendered');
    assert(document.querySelectorAll('.transcript-line .w.hit').length === 1, 'Keyword word highlighted');
    document.getElementById('transcript-search-input').value = 'physics';
    window.filterTranscriptLines();
    assert(document.getElementById('transcript-match-count').textContent === '1 line match', 'Search filters transcript');
    assert(document.querySelectorAll('.transcript-line:not(.hidden)').length === 1, 'Non-matching line hidden');
    document.getElementById('transcript-search-input').value = '';
    window.filterTranscriptLines();
    assert(document.getElementById('transcript-match-count').textContent === '2 lines · 1 keyword hit', 'Empty search shows line and hit counts');
    lines[1].click();
    await tick(30);
    assert(studioPlayer.currentTime === 10, 'Clicking a line seeks the player');
    assert(!document.getElementById('tab-reviewer').classList.contains('hidden'), 'Seek switches to reviewer');
    pass('Transcript inspector: keyword highlighting, search and click-to-seek');

    // Render result via SSE
    window.fetch = ((orig) => async (url, opts) => {
        if (String(url).startsWith('/api/result')) return json({ has_result: true, primary_output: 'C:/out/FINAL.mp4', batch_output_file: 'C:/out/FINAL.mp4', batch_filename: 'FINAL.mp4', stitched_files: [], total_duration_sec: 12.3, total_hits: 2, total_clips_count: 2, total_clip_footage_sec: 7.5, report_json_path: 'C:/out/r.json', output_dir: 'C:/out', encoder_used: 'hevc_nvenc', artifacts: [{ path: 'C:/out/FINAL.mp4', filename: 'FINAL.mp4', kind: 'master', label: 'Master', size_bytes: 1000, exists: true }, { path: 'C:/out/a.srt', filename: 'a.srt', kind: 'subtitle', label: 'Subs', size_bytes: 10, exists: true }], per_video: [{ video: 'sample.mp4', hits: 2, clips: 2, transcribe_s: 1.2, encode_s: 3.4, output: 'sample_stitched.mp4' }] });
        return orig(url, opts);
    })(window.fetch);
    window.__sse.emit('rendered', { batch_file: 'C:/out/FINAL.mp4' });
    await tick(60);
    assert(!document.getElementById('render-result-banner').classList.contains('hidden'), 'Result banner visible');
    assert(document.getElementById('btn-download-master').href.includes('/api/download?path=C%3A%2Fout%2FFINAL.mp4'), 'Master download link');
    assert(document.querySelectorAll('#artifacts-list .artifact').length === 2, 'Artifacts listed');
    assert(document.getElementById('btn-download-srt').href.includes('a.srt'), 'SRT download wired');
    assert(!document.getElementById('tab-export').classList.contains('hidden'), 'Switched to Deliver tab');
    assert(!document.getElementById('setup-completion-card').classList.contains('hidden'), 'Setup completion card shown');
    pass('SSE "rendered" populates the delivery cockpit with artifacts and downloads');

    // Error + cancel paths
    window.__sse.emit('job_started', { kind: 'render' });
    window.__sse.emit('error', { error: 'ffmpeg exploded' });
    assert(document.getElementById('telemetry-stage').textContent === 'FAILED', 'Error sets FAILED stage');
    assert(Array.from(document.querySelectorAll('#toast-container .toast-item')).some((t) => t.textContent.includes('ffmpeg exploded')), 'Error toast shown');
    window.__sse.emit('job_started', { kind: 'render' });
    window.__sse.emit('cancelled', { msg: 'stopped' });
    assert(document.getElementById('telemetry-stage').textContent === 'CANCELLED', 'Cancel sets CANCELLED stage');
    assert(document.getElementById('btn-cancel').classList.contains('hidden'), 'Stop hidden after cancel');
    pass('SSE error and cancelled events reset the running UI with feedback');

    // Start over clears the session
    await window.resetSession();
    await tick(40);
    assert(fetchCalls.some((c) => c.url === '/api/reset'), '/api/reset called');
    assert(document.getElementById('badge-clip-count').textContent === '0', 'Clip badge reset');
    assert(document.getElementById('render-result-banner').classList.contains('hidden'), 'Result banner hidden');
    assert(!document.getElementById('tab-setup').classList.contains('hidden'), 'Back on Setup');
    pass('Start over resets clips, results and returns to Setup');

    // Modals
    window.openShortcutsModal();
    assert(!document.getElementById('shortcuts-modal').classList.contains('hidden'), 'Shortcuts modal opens');
    key({ code: 'Escape', key: 'Escape' });
    assert(document.getElementById('shortcuts-modal').classList.contains('hidden'), 'Escape closes modal');
    await window.openSystemModal();
    await tick(30);
    assert(document.getElementById('system-body').textContent.includes('RTX 4090'), 'System modal shows GPU');
    window.closeModals();
    pass('Shortcut and system modals open, render diagnostics and close with Escape');

    console.error = origError;
    console.log(`\n======================================================`);
    console.log(` ALL ${passed} IN-ENGINE BROWSER DOM & JS VERIFICATION TESTS PASSED!`);
    console.log(`======================================================\n`);
}

runBrowserTests().catch((err) => {
    console.error('\n[FATAL BROWSER TEST ERROR]:', err);
    process.exit(1);
});

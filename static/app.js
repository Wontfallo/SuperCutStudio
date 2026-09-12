/* SuperCut Studio front-end (vanilla ES2020, no build step, works offline). */

'use strict';

// ---------------------------------------------------------------------------
// State
// ---------------------------------------------------------------------------
let activeTab = 'setup';
let appConfig = {};
let systemInfo = null;
let scannedFiles = [];
let detectedClips = [];
let clipVideos = [];
let allTranscripts = [];
let activePlayingClip = null;
let eventSource = null;
let jobRunning = false;
let jobStartedAt = 0;
let elapsedTimer = null;
let latestResult = null;
let markers = { in: null, out: null };
let waveformPeaks = [];
let waveformForPath = '';
let lastTelemetry = { pct: 0, msg: '', stage: 'IDLE' };
let configDirty = false;
let saveTimer = null;
let sseRetryDelay = 1000;
let sseInitDone = false;

const $ = (id) => document.getElementById(id);

// ---------------------------------------------------------------------------
// Utilities
// ---------------------------------------------------------------------------
function escapeHtml(s) {
  return String(s == null ? '' : s)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
}

function formatTimeSec(seconds) {
  if (isNaN(seconds) || seconds < 0) seconds = 0;
  const m = Math.floor(seconds / 60);
  const s = Math.floor(seconds % 60);
  const ms = Math.floor((seconds - Math.floor(seconds)) * 1000);
  return `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}.${String(ms).padStart(3, '0')}`;
}

function formatShort(seconds) {
  if (isNaN(seconds) || seconds < 0) seconds = 0;
  const m = Math.floor(seconds / 60);
  const s = (seconds % 60).toFixed(1).padStart(4, '0');
  return `${String(m).padStart(2, '0')}:${s}`;
}

function formatClock(seconds) {
  seconds = Math.max(0, Math.floor(seconds || 0));
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = seconds % 60;
  return h ? `${h}:${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`
           : `${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}`;
}

function formatDuration(seconds) {
  seconds = Math.max(0, seconds || 0);
  if (seconds < 60) return `${seconds.toFixed(1)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m ${String(s).padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  return `${h}h ${String(m % 60).padStart(2, '0')}m`;
}

function formatBytes(bytes) {
  bytes = bytes || 0;
  const units = ['B', 'KB', 'MB', 'GB'];
  let i = 0;
  while (bytes >= 1024 && i < units.length - 1) { bytes /= 1024; i++; }
  return `${bytes.toFixed(i === 0 ? 0 : 1)} ${units[i]}`;
}

function sanitizePath(raw) {
  let p = String(raw || '').trim();
  p = p.replace(/^["'`]+|["'`;,\s]+$/g, '').trim();
  return p;
}

async function api(url, opts = {}) {
  const res = await fetch(url, opts);
  let data = null;
  try { data = await res.json(); } catch (_) { data = null; }
  if (!res.ok) {
    const detail = (data && (data.detail || data.error)) || res.statusText || 'Request failed';
    throw new Error(typeof detail === 'string' ? detail : JSON.stringify(detail));
  }
  return data;
}

function postJson(url, body) {
  return api(url, { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body || {}) });
}

// ---------------------------------------------------------------------------
// Toasts
// ---------------------------------------------------------------------------
function showToast(message, type = 'info', durationMs = 4000) {
  const container = $('toast-container');
  if (!container) return null;
  const icons = { info: 'i', success: '✓', warning: '!', error: '✕' };
  const toast = document.createElement('div');
  toast.className = `toast-item toast-enter toast-${type}`;
  toast.innerHTML = `<span class="ic">${icons[type] || 'i'}</span><div class="msg">${escapeHtml(message)}</div><button class="x" title="Dismiss">✕</button>`;
  toast.querySelector('.x').onclick = () => toast.remove();
  container.prepend(toast);
  while (container.children.length > 6) container.lastElementChild.remove();
  const timer = setTimeout(() => {
    toast.classList.remove('toast-enter');
    toast.classList.add('toast-exit');
    setTimeout(() => toast.parentElement && toast.remove(), 200);
  }, durationMs);
  toast.onclick = (e) => { if (e.target.tagName !== 'BUTTON') { clearTimeout(timer); toast.remove(); } };
  return toast;
}
window.showToast = showToast;
window.alert = (msg) => { showToast(String(msg), 'warning'); };

// ---------------------------------------------------------------------------
// UI scale (high-DPI / 4K comfort)
// ---------------------------------------------------------------------------
const UI_SCALE_MIN = 0.8, UI_SCALE_MAX = 2.0;
let uiScale = 1.0;

function loadUiScale() {
  uiScale = 1.0;
  try { localStorage.removeItem('supercut.uiScale'); } catch (_) {}
  const range = $('ui-scale-range');
  if (range) {
    range.addEventListener('input', () => { uiScale = parseInt(range.value, 10) / 100; applyUiScale(false); });
    range.addEventListener('change', () => { uiScale = parseInt(range.value, 10) / 100; applyUiScale(true); });
  }
  // Fullscreen video must not inherit the zoom, or it renders cropped without controls.
  document.addEventListener('fullscreenchange', () => {
    const scaled = $('app-scaled');
    if (!scaled) return;
    scaled.style.zoom = document.fullscreenElement ? '1' : String(uiScale);
  });
  applyUiScale(false);
}

function applyUiScale(persist = true) {
  uiScale = Math.round(Math.min(UI_SCALE_MAX, Math.max(UI_SCALE_MIN, uiScale)) * 100) / 100;
  const scaled = $('app-scaled');
  if (scaled && !document.fullscreenElement) scaled.style.zoom = String(uiScale);
  const label = $('ui-scale-label');
  if (label) label.textContent = `${Math.round(uiScale * 100)}%`;
  const range = $('ui-scale-range');
  if (range && parseInt(range.value, 10) !== Math.round(uiScale * 100)) range.value = String(Math.round(uiScale * 100));
  if (typeof drawWaveform === 'function') requestAnimationFrame(drawWaveform);
}

function changeUiScale(delta) {
  uiScale = delta === 0 ? 1.0 : uiScale + delta;
  applyUiScale(true);
}
window.changeUiScale = changeUiScale;
window.applyUiScale = applyUiScale;

// ---------------------------------------------------------------------------
// Tabs & modals
// ---------------------------------------------------------------------------
function switchTab(tabName) {
  activeTab = tabName;
  document.querySelectorAll('.tab-content').forEach((el) => el.classList.add('hidden'));
  document.querySelectorAll('.tab-btn').forEach((el) => el.classList.remove('active'));
  const section = $('tab-' + tabName);
  if (section) section.classList.remove('hidden');
  const btn = $('tab-btn-' + tabName);
  if (btn) btn.classList.add('active');
  if (tabName === 'reviewer') requestAnimationFrame(drawWaveform);
  if (tabName === 'export') loadJobHistory();
  try { localStorage.setItem('supercut.tab', tabName); } catch (_) {}
}
window.switchTab = switchTab;

function openModal(id) { $('modal-backdrop').classList.remove('hidden'); $(id).classList.remove('hidden'); }
function closeModals() { $('modal-backdrop').classList.add('hidden'); document.querySelectorAll('.modal').forEach((m) => m.classList.add('hidden')); }
function openShortcutsModal() { openModal('shortcuts-modal'); }
async function openSystemModal() { openModal('system-modal'); await loadSystemInfo(false); }
window.closeModals = closeModals; window.openShortcutsModal = openShortcutsModal; window.openSystemModal = openSystemModal;

// ---------------------------------------------------------------------------
// System info
// ---------------------------------------------------------------------------
async function loadSystemInfo(refresh) {
  try {
    systemInfo = await api('/api/system' + (refresh ? '?refresh=true' : ''));
    populateModelSelect();
    renderSystemModal();
    if (!systemInfo.ffmpeg || !systemInfo.ffmpeg.available) {
      showToast('FFmpeg was not found. Install FFmpeg or set VKS_FFMPEG.', 'error', 8000);
    }
  } catch (err) {
    const body = $('system-body');
    if (body) body.innerHTML = `<div class="bad">Failed to load system info: ${escapeHtml(err.message)}</div>`;
  }
}
window.loadSystemInfo = loadSystemInfo;

function renderSystemModal() {
  const body = $('system-body');
  if (!body || !systemInfo) return;
  const s = systemInfo;
  const yn = (v) => v ? '<span class="ok">yes</span>' : '<span class="bad">no</span>';
  const enc = Object.entries(s.encoders || {}).map(([k, v]) => `<div class="sys-row"><span>${k}</span><span class="v">${yn(v)}</span></div>`).join('') || '<div class="dim">not probed</div>';
  const models = (s.asr && s.asr.models || []).map((m) => `<div class="sys-row"><span>${escapeHtml(m.label)}</span><span class="v">${m.available ? '<span class="ok">ready</span>' : '<span class="bad">missing</span>'}</span></div>`).join('');
  const cache = s.transcript_cache || {};
  body.innerHTML = `
    <div class="sys-grid">
      <div class="sys-block"><h4>Application</h4>
        <div class="sys-row"><span>Version</span><span class="v">${escapeHtml(s.app.version)}</span></div>
        <div class="sys-row"><span>Mode</span><span class="v">${s.app.frozen ? 'installed build' : 'source'}${s.desktop ? ' · desktop' : ''}</span></div>
        <div class="sys-row"><span>Platform</span><span class="v">${escapeHtml(s.app.platform)} · Py ${escapeHtml(s.app.python)}</span></div>
        <div class="sys-row"><span>Data dir</span><span class="v" title="${escapeHtml(s.app.data_dir)}">${escapeHtml(s.app.data_dir)}</span></div>
      </div>
      <div class="sys-block"><h4>FFmpeg</h4>
        <div class="sys-row"><span>Available</span><span class="v">${yn(s.ffmpeg.available)}</span></div>
        <div class="sys-row"><span>Version</span><span class="v" title="${escapeHtml(s.ffmpeg.version || '')}">${escapeHtml((s.ffmpeg.version || 'n/a').replace('ffmpeg version ', ''))}</span></div>
        <div class="sys-row"><span>Path</span><span class="v" title="${escapeHtml(s.ffmpeg.path)}">${escapeHtml(s.ffmpeg.path)}</span></div>
      </div>
      <div class="sys-block"><h4>Hardware</h4>
        <div class="sys-row"><span>GPU</span><span class="v">${escapeHtml(s.gpu || 'none detected')}</span></div>
        <div class="sys-row"><span>CUDA (ASR)</span><span class="v">${yn(s.cuda)}</span></div>
        <div class="sys-row"><span>NVENC</span><span class="v">${yn(s.nvenc)}</span></div>
        ${enc}
      </div>
      <div class="sys-block"><h4>Speech models</h4>
        ${models}
        <div class="sys-row"><span>CrisperWhisper runtime</span><span class="v" title="${escapeHtml(s.asr.crisper_python || '')}">${s.asr.crisper_python ? escapeHtml(s.asr.crisper_python) : '<span class="bad">not found</span>'}</span></div>
        <div class="sys-row"><span>Transcript cache</span><span class="v">${cache.count || 0} files · ${formatBytes(cache.size_bytes || 0)}</span></div>
      </div>
    </div>`;
}

function populateModelSelect() {
  const sel = $('setting-model');
  if (!sel || !systemInfo || !systemInfo.asr) return;
  const current = sel.value || appConfig.model || 'crisper-turbo';
  sel.innerHTML = '';
  for (const m of systemInfo.asr.models) {
    const opt = document.createElement('option');
    opt.value = m.id;
    opt.textContent = `${m.label}${m.recommended ? ' ★' : ''}${m.available ? '' : ' (not installed)'}`;
    opt.dataset.detail = m.detail || '';
    opt.dataset.available = m.available ? '1' : '0';
    sel.appendChild(opt);
  }
  sel.value = current;
  if (sel.value !== current) sel.value = appConfig.model || 'crisper-turbo';
  updateModelHint();
}

function updateModelHint() {
  const sel = $('setting-model');
  const hint = $('model-hint');
  if (!sel || !hint) return;
  const opt = sel.selectedOptions[0];
  if (!opt) { hint.textContent = ''; return; }
  const avail = opt.dataset.available !== '0';
  hint.innerHTML = `${escapeHtml(opt.dataset.detail || '')}${avail ? '' : ' <span class="bad">Not installed - a Whisper model will be used instead.</span>'}`;
}

async function clearTranscriptCache() {
  try {
    const r = await postJson('/api/cache/clear');
    showToast(`Cleared ${r.removed} cached transcript(s).`, 'success');
    loadSystemInfo(false);
  } catch (err) { showToast('Cache clear failed: ' + err.message, 'error'); }
}
window.clearTranscriptCache = clearTranscriptCache;

async function openPath(path) {
  if (!path) return;
  try { await postJson('/api/open', { path }); } catch (err) { showToast('Could not open: ' + err.message, 'warning'); }
}
window.openPath = openPath;

// ---------------------------------------------------------------------------
// Configuration
// ---------------------------------------------------------------------------
const CONFIG_BINDINGS = [
  ['input-path', 'input_path', 'text'],
  ['output-dir', 'output_dir', 'text'],
  ['setting-output-name', 'output_name', 'text'],
  ['setting-model', 'model', 'text'],
  ['setting-language', 'language', 'text'],
  ['setting-aspect-ratio', 'aspect_ratio', 'text'],
  ['setting-transition', 'transition', 'text'],
  ['setting-transition-duration', 'transition_duration', 'number'],
  ['setting-encoder', 'encoder', 'text'],
  ['setting-quality', 'quality', 'text'],
  ['setting-subtitle-style', 'subtitle_style', 'text'],
  ['setting-subtitle-mode', 'subtitle_mode', 'text'],
  ['setting-burn-subtitles', 'burn_subtitles', 'bool'],
  ['setting-loudness', 'loudness_normalize', 'bool'],
  ['setting-fast-seek', 'fast_seek', 'bool'],
  ['setting-open-folder', 'open_folder', 'bool'],
  ['setting-batch-stitch', 'save_batch_stitched', 'bool'],
  ['setting-per-video-stitch', 'save_per_video_stitched', 'bool'],
  ['setting-individual-clips', 'save_individual_clips', 'bool'],
  ['setting-thumbnails', 'generate_thumbnails', 'bool'],
  ['setting-contact-sheet', 'contact_sheet', 'bool'],
  ['setting-export-srt', 'export_srt', 'bool'],
  ['setting-export-vtt', 'export_vtt', 'bool'],
  ['setting-export-ass', 'export_ass', 'bool'],
  ['setting-export-transcript', 'export_transcript', 'bool'],
  ['setting-export-fcpxml', 'export_fcpxml', 'bool'],
  ['setting-export-edl', 'export_edl', 'bool'],
  ['setting-sentence-mode', 'sentence_mode', 'bool'],
  ['setting-snap-silence', 'snap_to_silence', 'bool'],
  ['setting-high-energy', 'high_energy', 'bool'],
  ['setting-fuzzy', 'fuzzy_threshold', 'number'],
  ['setting-pre-pad', 'pre_pad', 'number'],
  ['setting-post-pad', 'post_pad', 'number'],
  ['setting-merge-gap', 'merge_gap', 'number'],
  ['setting-max-clip', 'max_clip_duration', 'number'],
];

function applyConfigToForm() {
  for (const [id, key, type] of CONFIG_BINDINGS) {
    const el = $(id);
    if (!el || appConfig[key] === undefined || appConfig[key] === null) continue;
    if (type === 'bool') el.checked = !!appConfig[key];
    else el.value = appConfig[key];
  }
  setInputMode(appConfig.input_mode === 'File' ? 'File' : 'Folder', false);
  updateSliderLabels();
  updateModelHint();
  renderKeywordChips();
  renderExcludeChips();
}

function collectCurrentConfig() {
  const cfg = Object.assign({}, appConfig);
  for (const [id, key, type] of CONFIG_BINDINGS) {
    const el = $(id);
    if (!el) continue;
    if (type === 'bool') cfg[key] = !!el.checked;
    else if (type === 'number') { const v = parseFloat(el.value); cfg[key] = isNaN(v) ? (appConfig[key] ?? 0) : v; }
    else cfg[key] = (el.value || '').trim();
  }
  cfg.input_path = sanitizePath(cfg.input_path);
  cfg.output_dir = sanitizePath(cfg.output_dir);
  cfg.input_mode = $('mode-file-btn') && $('mode-file-btn').classList.contains('active') ? 'File' : 'Folder';
  cfg.keywords = appConfig.keywords || [];
  cfg.exclude_keywords = appConfig.exclude_keywords || [];
  return cfg;
}
window.collectCurrentConfig = collectCurrentConfig;

async function loadConfig() {
  try {
    appConfig = await api('/api/config');
    if (!Array.isArray(appConfig.keywords)) appConfig.keywords = [];
    if (!Array.isArray(appConfig.exclude_keywords)) appConfig.exclude_keywords = [];
    applyConfigToForm();
  } catch (err) {
    console.error('Failed to load config:', err);
    showToast('Could not load settings: ' + err.message, 'error');
  }
}

async function saveCurrentConfig(announce = false) {
  const cfg = collectCurrentConfig();
  try {
    const res = await postJson('/api/config', cfg);
    appConfig = res.config;
    configDirty = false;
    if (announce) showToast('Settings saved.', 'success', 2000);
  } catch (err) {
    showToast('Failed to save settings: ' + err.message, 'error');
  }
}
window.saveCurrentConfig = saveCurrentConfig;

function scheduleSave() {
  configDirty = true;
  clearTimeout(saveTimer);
  saveTimer = setTimeout(() => saveCurrentConfig(false), 700);
}

function updateSliderLabels() {
  const fz = parseFloat($('setting-fuzzy').value) || 0;
  $('fuzzy-label').textContent = fz <= 0.01 ? 'off (exact)' : fz.toFixed(2);
  $('pre-pad-label').textContent = (parseFloat($('setting-pre-pad').value) || 0).toFixed(1) + 's';
  $('post-pad-label').textContent = (parseFloat($('setting-post-pad').value) || 0).toFixed(1) + 's';
  $('merge-gap-label').textContent = (parseFloat($('setting-merge-gap').value) || 0).toFixed(1) + 's';
  const mx = parseFloat($('setting-max-clip').value) || 0;
  $('max-clip-label').textContent = mx <= 0 ? 'unlimited' : mx.toFixed(0) + 's';
}

function bindConfigInputs() {
  for (const [id] of CONFIG_BINDINGS) {
    const el = $(id);
    if (!el) continue;
    el.addEventListener('change', () => { updateSliderLabels(); updateModelHint(); scheduleSave(); });
    if (el.type === 'range') el.addEventListener('input', updateSliderLabels);
  }
}

// ---------------------------------------------------------------------------
// Input mode, browse, scan, upload
// ---------------------------------------------------------------------------
function setInputMode(mode, save = true) {
  const folderBtn = $('mode-folder-btn');
  const fileBtn = $('mode-file-btn');
  const input = $('input-path');
  const isFile = mode === 'File';
  folderBtn.classList.toggle('active', !isFile);
  fileBtn.classList.toggle('active', isFile);
  if (input) input.placeholder = isFile ? 'e.g. C:\\Videos\\interview.mp4' : 'e.g. C:\\Videos\\Highlights';
  if (save) scheduleSave();
}
window.setInputMode = setInputMode;

async function browseInput() {
  const isFile = $('mode-file-btn').classList.contains('active');
  try {
    const r = await postJson('/api/pick', { kind: isFile ? 'file' : 'folder', initial: $('input-path').value });
    if (r.status === 'ok' && r.path) {
      $('input-path').value = r.path;
      scheduleSave();
      await scanMediaSource();
    }
  } catch (err) { showToast('Picker unavailable: ' + err.message, 'warning'); }
}
window.browseInput = browseInput;

async function browseOutput() {
  try {
    const r = await postJson('/api/pick', { kind: 'folder', title: 'Select output folder', initial: $('output-dir').value });
    if (r.status === 'ok' && r.path) { $('output-dir').value = r.path; scheduleSave(); }
  } catch (err) { showToast('Picker unavailable: ' + err.message, 'warning'); }
}
window.browseOutput = browseOutput;

function openOutputFolder() { openPath(sanitizePath($('output-dir').value)); }
window.openOutputFolder = openOutputFolder;

function openResultFolder() {
  if (latestResult && latestResult.output_dir) openPath(latestResult.output_dir);
  else openOutputFolder();
}
window.openResultFolder = openResultFolder;

let lastScannedPath = '';
async function scanMediaSource(force = false) {
  const inputEl = $('input-path');
  const inputPath = sanitizePath(inputEl.value);
  inputEl.value = inputPath;
  if (!inputPath) { showToast('Enter a folder or video file path first.', 'warning'); return; }
  if (!force && inputPath === lastScannedPath && scannedFiles.length) return;
  lastScannedPath = inputPath;

  const scanBtn = $('btn-scan');
  const scanBtnText = $('btn-scan-text');
  if (scanBtn) scanBtn.disabled = true;
  if (scanBtnText) scanBtnText.textContent = 'Scanning…';
  const container = $('scanned-assets-container');
  if (container) { container.classList.remove('hidden'); $('scanned-list').innerHTML = '<div class="file-item"><span class="muted">Checking media…</span></div>'; }
  try {
    const data = await postJson('/api/scan', { input_path: inputPath });
    scannedFiles = data.files || [];
    setInputMode(data.is_file ? 'File' : 'Folder', false);
    renderScannedFiles(data);
    if (scannedFiles.length > 0) {
      showToast(`Found ${scannedFiles.length} file(s), ${formatDuration(data.total_duration)} total.`, 'success', 3000);
    } else {
      showToast('No video files found at that path.', 'warning');
    }
    scheduleSave();
  } catch (err) {
    showToast('Scan failed: ' + err.message, 'error');
  } finally {
    if (scanBtn) scanBtn.disabled = false;
    if (scanBtnText) scanBtnText.textContent = 'Scan';
  }
}
window.scanMediaSource = scanMediaSource;

function renderScannedFiles(data) {
  const container = $('scanned-assets-container');
  const list = $('scanned-list');
  container.classList.remove('hidden');
  $('scanned-count').textContent = scannedFiles.length;
  $('scanned-total').textContent = scannedFiles.length ? `${formatDuration(data.total_duration)} · ${formatBytes(scannedFiles.reduce((a, f) => a + (f.size_bytes || 0), 0))}` : '';
  $('scanned-info').textContent = data.is_file ? 'single file' : 'folder';
  list.innerHTML = '';
  for (const f of scannedFiles) {
    const item = document.createElement('div');
    item.className = 'file-item' + (f.readable ? '' : ' unreadable');
    item.innerHTML = `
      <div class="file-name"><span class="dot ${f.has_audio ? 'dot-green' : 'dot-rose'}" title="${f.has_audio ? 'has audio' : 'no audio track'}"></span><span title="${escapeHtml(f.path)}">${escapeHtml(f.filename)}</span></div>
      <div class="file-meta"><span>${escapeHtml(f.resolution)}</span><span>${formatDuration(f.duration)}</span><span>${f.fps} fps</span><span>${escapeHtml((f.video_codec || '').toUpperCase())}</span><span>${formatBytes(f.size_bytes)}</span></div>`;
    list.appendChild(item);
  }
}

function initDropZone() {
  const dropZone = $('drop-zone');
  const fileUploader = $('file-uploader');
  const card = $('source-card') || dropZone;
  dropZone.addEventListener('click', (e) => { if (e.target !== fileUploader) fileUploader.click(); });
  card.addEventListener('dragover', (e) => { e.preventDefault(); card.classList.add('drag-over'); });
  card.addEventListener('dragleave', (e) => { if (!card.contains(e.relatedTarget)) card.classList.remove('drag-over'); });
  card.addEventListener('drop', (e) => {
    e.preventDefault();
    card.classList.remove('drag-over');
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) uploadMediaFile(e.dataTransfer.files[0]);
  });
}

function handleFileUpload(event) {
  if (event.target.files && event.target.files.length > 0) uploadMediaFile(event.target.files[0]);
  event.target.value = '';
}
window.handleFileUpload = handleFileUpload;

async function uploadMediaFile(file) {
  const dropZone = $('source-card') || $('drop-zone');
  const formData = new FormData();
  formData.append('file', file);
  dropZone.classList.add('uploading');
  showToast(`Uploading ${file.name} (${formatBytes(file.size)})…`, 'info', 3000);
  try {
    const res = await fetch('/api/upload', { method: 'POST', body: formData });
    if (!res.ok) throw new Error(await res.text());
    const data = await res.json();
    $('input-path').value = data.path;
    setInputMode('File', false);
    await scanMediaSource();
  } catch (err) {
    showToast('Upload failed: ' + err.message, 'error');
  } finally {
    dropZone.classList.remove('uploading');
  }
}

// ---------------------------------------------------------------------------
// Keywords
// ---------------------------------------------------------------------------
function splitTerms(raw) {
  return String(raw || '').split(/[,;\n\r]+/).map((s) => s.trim()).filter(Boolean);
}

function renderKeywordChips() {
  const container = $('keyword-chips');
  const list = appConfig.keywords || [];
  container.innerHTML = '';
  if (list.length === 0) {
    container.innerHTML = '<span class="placeholder">No keywords yet. Type one above and press Enter.</span>';
    return;
  }
  list.forEach((kw, idx) => {
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.innerHTML = `<span>${escapeHtml(kw)}</span><button title="Remove" onclick="removeKeyword(${idx})">×</button>`;
    container.appendChild(chip);
  });
}

function addKeyword(word) {
  const clean = String(word || '').trim();
  if (!clean) return;
  if (!appConfig.keywords) appConfig.keywords = [];
  const lower = clean.toLowerCase();
  if (appConfig.keywords.some((k) => k.toLowerCase() === lower)) return;
  const cs = $('setting-case-sensitive');
  appConfig.keywords.push(cs && cs.checked ? clean : lower);
  renderKeywordChips();
  scheduleSave();
}
window.addKeyword = addKeyword;

function addKeywordFromInput() {
  const input = $('keyword-input');
  splitTerms(input.value).forEach(addKeyword);
  input.value = '';
}
window.addKeywordFromInput = addKeywordFromInput;

function handleKeywordKeydown(e) {
  if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addKeywordFromInput(); }
}
window.handleKeywordKeydown = handleKeywordKeydown;

function removeKeyword(idx) {
  if (appConfig.keywords && appConfig.keywords[idx] !== undefined) {
    appConfig.keywords.splice(idx, 1);
    renderKeywordChips();
    scheduleSave();
  }
}
window.removeKeyword = removeKeyword;

function clearKeywords() { appConfig.keywords = []; renderKeywordChips(); scheduleSave(); }
window.clearKeywords = clearKeywords;

function renderExcludeChips() {
  const container = $('exclude-chips');
  const list = appConfig.exclude_keywords || [];
  container.innerHTML = '';
  if (list.length === 0) { container.innerHTML = '<span class="placeholder">No exclusions (optional).</span>'; return; }
  list.forEach((kw, idx) => {
    const chip = document.createElement('div');
    chip.className = 'chip';
    chip.innerHTML = `<span>${escapeHtml(kw)}</span><button title="Remove" onclick="removeExclude(${idx})">×</button>`;
    container.appendChild(chip);
  });
}

function addExcludeFromInput() {
  const input = $('exclude-input');
  if (!appConfig.exclude_keywords) appConfig.exclude_keywords = [];
  for (const term of splitTerms(input.value)) {
    const lower = term.toLowerCase();
    if (!appConfig.exclude_keywords.some((k) => k.toLowerCase() === lower)) appConfig.exclude_keywords.push(lower);
  }
  input.value = '';
  renderExcludeChips();
  scheduleSave();
}
window.addExcludeFromInput = addExcludeFromInput;
function handleExcludeKeydown(e) { if (e.key === 'Enter' || e.key === ',') { e.preventDefault(); addExcludeFromInput(); } }
window.handleExcludeKeydown = handleExcludeKeydown;
function removeExclude(idx) { appConfig.exclude_keywords.splice(idx, 1); renderExcludeChips(); scheduleSave(); }
window.removeExclude = removeExclude;

// ---------------------------------------------------------------------------
// Jobs
// ---------------------------------------------------------------------------
function setJobRunning(running, label) {
  jobRunning = running;
  document.body.classList.toggle('running', !!running);
  const cancelBtn = $('btn-cancel');
  const cancelPipe = $('btn-cancel-pipeline');
  cancelBtn.classList.toggle('hidden', !running);
  cancelPipe.classList.toggle('hidden', !running);
  ['btn-start-stitching', 'btn-run-analysis', 'btn-stitch-folder'].forEach((id) => {
    const b = $(id);
    if (b) b.disabled = running;
  });
  $('setup-status-indicator').className = 'dot ' + (running ? 'dot-cyan pulse' : 'dot-green');
  $('setup-status-text').textContent = label || (running ? 'Working…' : 'Ready');
  if (running) {
    if (!jobStartedAt) jobStartedAt = Date.now();
    if (!elapsedTimer) elapsedTimer = setInterval(tickElapsed, 1000);
  } else {
    clearInterval(elapsedTimer); elapsedTimer = null;
    jobStartedAt = 0;
  }
  tickElapsed();
}

function tickElapsed() {
  const el = $('setup-elapsed-timer');
  if (!el) return;
  el.textContent = jobStartedAt ? formatClock((Date.now() - jobStartedAt) / 1000) : '00:00';
}

async function startJob(url, body, label, opts = {}) {
  if (jobRunning) { showToast('A job is already running. Stop it first.', 'warning'); return false; }
  const cfg = collectCurrentConfig();
  if (opts.needInput !== false && !cfg.input_path) {
    showToast('Set an input folder or video file first.', 'warning'); switchTab('setup'); $('input-path').focus(); return false;
  }
  if (opts.needKeywords && (!cfg.keywords || cfg.keywords.length === 0) && !cfg.high_energy) {
    showToast('Add at least one keyword first.', 'warning'); switchTab('setup'); $('keyword-input').focus(); return false;
  }
  try {
    await postJson(url, Object.assign({ config: cfg }, body || {}));
    jobStartedAt = Date.now();
    setJobRunning(true, label);
    clearResultCards();
    return true;
  } catch (err) {
    showToast('Could not start: ' + err.message, 'error', 6000);
    return false;
  }
}

async function trigger1ClickSuperCut() {
  const cfg = collectCurrentConfig();
  if (await startJob('/api/pipeline/start', { input_path: cfg.input_path }, 'Running SuperCut pipeline…', { needKeywords: true })) {
    showToast('SuperCut started: transcribing, matching and rendering.', 'info', 3000);
    $('btn-start-text').textContent = 'Working…';
  }
}
window.trigger1ClickSuperCut = trigger1ClickSuperCut;

async function triggerAnalyze() {
  const cfg = collectCurrentConfig();
  if (await startJob('/api/analyze', { input_path: cfg.input_path }, 'Transcribing & detecting clips…', { needKeywords: true })) {
    showToast('Analysis started. Clips will appear in the Reviewer.', 'info', 3000);
    $('btn-run-text').textContent = 'Analyzing…';
    switchTab('reviewer');
    showAnalyzingStateInReviewer();
  }
}
window.triggerAnalyze = triggerAnalyze;

async function triggerStitchFolderOnly() {
  const cfg = collectCurrentConfig();
  const toolsPath = sanitizePath(($('tools-folder-path') || {}).value || '');
  const folder = toolsPath || cfg.input_path;
  if (!folder) { showToast('Pick a folder of videos first.', 'warning'); return; }
  if (await startJob('/api/stitch-folder-only', { input_path: folder }, 'Normalizing & joining folder…', { needInput: false })) {
    showToast('Folder stitch started.', 'info', 2500);
  }
}
window.triggerStitchFolderOnly = triggerStitchFolderOnly;

async function browseToolsFolder() {
  try {
    const r = await postJson('/api/pick', { kind: 'folder', title: 'Folder of videos to join', initial: $('tools-folder-path').value || $('input-path').value });
    if (r.status === 'ok' && r.path) $('tools-folder-path').value = r.path;
  } catch (err) { showToast('Picker unavailable: ' + err.message, 'warning'); }
}
window.browseToolsFolder = browseToolsFolder;

async function triggerRender() {
  if (jobRunning) { showToast('A job is already running.', 'warning'); return; }
  if (!detectedClips.some((c) => c.selected)) { showToast('Select at least one clip to render.', 'warning'); switchTab('reviewer'); return; }
  const cfg = collectCurrentConfig();
  try {
    await postJson('/api/render', { config: cfg });
    jobStartedAt = Date.now();
    setJobRunning(true, 'Rendering curated SuperCut…');
    switchTab('export');
    showToast('Render started.', 'info', 2500);
  } catch (err) {
    showToast('Render error: ' + err.message, 'error', 6000);
  }
}
window.triggerRender = triggerRender;

async function cancelJob() {
  try {
    await postJson('/api/cancel');
    showToast('Stopping job…', 'warning', 2000);
  } catch (err) { console.error('Cancel failed:', err); }
}
window.cancelJob = cancelJob;

function resetJobButtons() {
  $('btn-start-text').textContent = 'Start SuperCut (no review)';
  $('btn-run-text').textContent = 'Analyze & Review First';
}

function clearResultCards() {
  $('setup-completion-card').classList.add('hidden');
}

// ---------------------------------------------------------------------------
// Telemetry & log
// ---------------------------------------------------------------------------
function updateTelemetry(pct, msg, stage, elapsed, eta) {
  lastTelemetry = { pct: pct || 0, msg: msg || '', stage: stage || 'RUNNING' };
  $('telemetry-stage').textContent = (stage || 'RUNNING').toUpperCase();
  $('telemetry-pct').textContent = (pct || 0).toFixed(1) + '%';
  $('telemetry-fill').style.width = Math.min(100, Math.max(0, pct || 0)) + '%';
  $('telemetry-msg').textContent = msg || '';
  $('telemetry-msg').title = msg || '';
  let t = '';
  if (elapsed) t += formatClock(elapsed);
  if (eta != null && eta > 0) t += ` · ETA ${formatClock(eta)}`;
  $('telemetry-time').textContent = t;
  $('setup-eta').textContent = eta != null && eta > 0 ? `ETA ${formatClock(eta)}` : '';
  if (jobRunning && msg) $('setup-status-text').textContent = msg;
}

function appendLog(entry) {
  const box = $('log-console');
  if (!box) return;
  const hint = $('log-summary-hint');
  if (hint && entry.text) hint.textContent = '· ' + String(entry.text).slice(0, 90);
  const line = document.createElement('div');
  line.className = `log-line log-${entry.level || 'info'}`;
  line.innerHTML = `<span class="t">${escapeHtml(entry.time || '')}</span><span>${escapeHtml(entry.text || '')}</span>`;
  box.appendChild(line);
  while (box.children.length > 500) box.firstElementChild.remove();
  if ($('log-autoscroll').checked) box.scrollTop = box.scrollHeight;
}
function clearLog() { $('log-console').innerHTML = ''; }
window.clearLog = clearLog;

async function loadRecentLogs() {
  try {
    const d = await api('/api/logs?limit=150');
    clearLog();
    (d.logs || []).forEach(appendLog);
  } catch (_) {}
}

// ---------------------------------------------------------------------------
// SSE
// ---------------------------------------------------------------------------
function setConnection(state) {
  const ind = $('sse-indicator');
  const stat = $('sse-status');
  if (state === 'open') { ind.className = 'dot dot-green'; stat.textContent = 'Connected · live telemetry'; }
  else if (state === 'error') { ind.className = 'dot dot-rose pulse'; stat.textContent = 'Reconnecting…'; }
  else { ind.className = 'dot dot-amber pulse'; stat.textContent = 'Connecting to studio core…'; }
}

function initSSE() {
  if (eventSource) { try { eventSource.close(); } catch (_) {} }
  try {
    eventSource = new EventSource('/api/events');
  } catch (err) {
    console.warn('SSE unavailable:', err);
    return;
  }
  const parse = (e) => { try { return JSON.parse(e.data); } catch (_) { return {}; } };

  eventSource.onopen = () => { setConnection('open'); sseRetryDelay = 1000; };
  eventSource.onerror = () => {
    setConnection('error');
    // EventSource auto-reconnects; also poll status so a finished job is never missed.
    setTimeout(pollStatus, sseRetryDelay);
    sseRetryDelay = Math.min(10000, sseRetryDelay * 2);
  };

  eventSource.addEventListener('init', (e) => {
    const d = parse(e);
    applyStatusSnapshot(d);
    sseInitDone = true;
  });
  eventSource.addEventListener('progress', (e) => {
    const d = parse(e);
    updateTelemetry(d.pct, d.msg, d.stage, d.elapsed, d.eta);
  });
  eventSource.addEventListener('log', (e) => appendLog(parse(e)));
  eventSource.addEventListener('job_started', () => { if (!jobRunning) { jobStartedAt = Date.now(); setJobRunning(true, 'Working…'); } });
  eventSource.addEventListener('analyzed', async (e) => {
    const d = parse(e);
    if (!d.silent) { setJobRunning(false, 'Analysis complete'); resetJobButtons(); }
    await loadClips();
    await loadTranscripts();
    if (!d.silent) {
      switchTab('reviewer');
      if (d.total_clips > 0) showToast(`Analysis complete: ${d.total_clips} clip(s) from ${d.total_hits} hit(s) across ${d.stats_count} video(s).`, 'success', 5000);
      else showToast('Analysis finished but no keyword hits were found. Try other keywords or fuzzy matching.', 'warning', 7000);
      if (d.failed && d.failed.length) showToast('Failed: ' + d.failed.join(', '), 'error', 6000);
    }
  });
  eventSource.addEventListener('rendered', async (e) => {
    setJobRunning(false, 'Render complete');
    resetJobButtons();
    await loadLatestResult();
    await loadClips();
    await loadTranscripts();
    switchTab('export');
    showToast('SuperCut rendering complete. Master video ready.', 'success', 5000);
    loadJobHistory();
  });
  eventSource.addEventListener('cancelled', () => {
    setJobRunning(false, 'Stopped');
    resetJobButtons();
    updateTelemetry(0, 'Job stopped.', 'CANCELLED');
    showToast('Job stopped.', 'info', 3000);
    loadClips();
  });
  eventSource.addEventListener('error', (e) => {
    const d = parse(e);
    if (!d || !d.error) return;
    setJobRunning(false, 'Failed');
    resetJobButtons();
    showToast('Pipeline error: ' + d.error, 'error', 8000);
    updateTelemetry(0, 'Error: ' + d.error, 'FAILED');
  });
  eventSource.addEventListener('clip_updated', (e) => { const d = parse(e); if (d.clip) mergeClip(d.clip); });
  eventSource.addEventListener('clip_added', () => loadClips());
  eventSource.addEventListener('clip_deleted', () => loadClips());
  eventSource.addEventListener('clips_batch_toggled', () => loadClips());
  eventSource.addEventListener('config_updated', (e) => {
    const d = parse(e);
    if (d.config && !configDirty && document.activeElement && !['INPUT', 'SELECT', 'TEXTAREA'].includes(document.activeElement.tagName)) {
      appConfig = d.config; applyConfigToForm();
    }
  });
}

function applyStatusSnapshot(d) {
  if (!d) return;
  updateTelemetry(d.pct, d.msg, d.stage, d.elapsed, null);
  const running = ['analyzing', 'rendering', 'stitching'].includes(d.status);
  if (running && !jobRunning) { jobStartedAt = Date.now() - (d.elapsed || 0) * 1000; setJobRunning(true, d.msg); }
  if (!running && jobRunning) { setJobRunning(false, d.stage || 'Ready'); resetJobButtons(); }
  if (d.has_result) loadLatestResult();
}

async function pollStatus() {
  try { applyStatusSnapshot(await api('/api/status')); } catch (_) {}
}

// ---------------------------------------------------------------------------
// Clips reviewer
// ---------------------------------------------------------------------------
async function loadClips() {
  try {
    const data = await api('/api/clips');
    detectedClips = data.clips || [];
    clipVideos = data.videos || [];
    window.detectedClips = detectedClips;
    $('badge-clip-count').textContent = data.count;
    const summary = $('clips-summary');
    if (summary) {
      const hits = data.total_hits || 0;
      const b = data.boundaries || {};
      if (data.count > 0 && hits > data.count) {
        summary.textContent = `${hits} keyword hits merged into ${data.count} clip(s): hits closer together than the padding (${(b.pre_pad ?? 1).toFixed(1)}s before / ${(b.post_pad ?? 1).toFixed(1)}s after) are joined into one clip. Lower the padding in Setup for more, shorter clips.`;
      } else if (data.count > 0) {
        summary.textContent = `${hits} keyword hit(s) → ${data.count} clip(s). Review, audition and curate what gets stitched.`;
      } else {
        summary.textContent = 'Review, audition and curate what gets stitched.';
      }
    }
    $('curated-count').textContent = data.selected_count;
    $('curated-footage').textContent = formatDuration(data.selected_footage || 0);
    if (activePlayingClip) {
      const still = detectedClips.find((c) => c.video_path === activePlayingClip.video_path && c.clip_index === activePlayingClip.clip_index);
      activePlayingClip = still || null;
      window.activePlayingClip = activePlayingClip;
    }
    updateFilterOptions();
    renderClipsTable();
    updateActiveClipDetails();
  } catch (err) {
    console.error('Failed to load clips:', err);
  }
}
window.loadClips = loadClips;

function mergeClip(clip) {
  const idx = detectedClips.findIndex((c) => c.video_path === clip.video_path && c.clip_index === clip.clip_index);
  if (idx >= 0) detectedClips[idx] = clip; else detectedClips.push(clip);
  if (activePlayingClip && activePlayingClip.video_path === clip.video_path && activePlayingClip.clip_index === clip.clip_index) {
    activePlayingClip = clip; window.activePlayingClip = clip;
  }
  const selected = detectedClips.filter((c) => c.selected);
  $('curated-count').textContent = selected.length;
  $('curated-footage').textContent = formatDuration(selected.reduce((a, c) => a + c.duration, 0));
  renderClipsTable();
  updateActiveClipDetails();
  updateScrubberUI();
}

function updateFilterOptions() {
  const kwSel = $('clip-filter-keyword');
  const vidSel = $('clip-filter-video');
  const kwPrev = kwSel.value, vidPrev = vidSel.value;
  const kws = [...new Set(detectedClips.flatMap((c) => c.keywords && c.keywords.length ? c.keywords : [c.primary_keyword]))].sort();
  kwSel.innerHTML = '<option value="">All keywords</option>' + kws.map((k) => `<option value="${escapeHtml(k)}">${escapeHtml(k)}</option>`).join('');
  vidSel.innerHTML = '<option value="">All videos</option>' + clipVideos.map((v) => `<option value="${escapeHtml(v.video_path)}">${escapeHtml(v.filename)} (${v.clips})</option>`).join('');
  kwSel.value = kwPrev; vidSel.value = vidPrev;
}

function visibleClips() {
  const q = ($('clip-search').value || '').trim().toLowerCase();
  const kw = $('clip-filter-keyword').value;
  const vid = $('clip-filter-video').value;
  const selOnly = $('clip-filter-selected').checked;
  const sort = $('clip-sort').value;
  let list = detectedClips.filter((c) => {
    if (kw && !(c.keywords || [c.primary_keyword]).includes(kw)) return false;
    if (vid && c.video_path !== vid) return false;
    if (selOnly && !c.selected) return false;
    if (q && !(`${c.matched_text} ${c.primary_keyword} ${c.custom_title || ''}`.toLowerCase().includes(q))) return false;
    return true;
  });
  const cmp = {
    time: (a, b) => a.video_index - b.video_index || a.start - b.start,
    keyword: (a, b) => a.primary_keyword.localeCompare(b.primary_keyword) || a.start - b.start,
    duration: (a, b) => b.duration - a.duration,
    confidence: (a, b) => (a.confidence || 1) - (b.confidence || 1),
  }[sort] || ((a, b) => a.start - b.start);
  return list.sort(cmp);
}

function renderClipsTable() {
  const tbody = $('clips-tbody');
  tbody.innerHTML = '';
  if (detectedClips.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No clips detected yet. Run analysis in Setup to populate this cockpit.</td></tr>';
    return;
  }
  const list = visibleClips();
  if (list.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty">No clips match the current filter.</td></tr>';
    return;
  }
  const multiVideo = clipVideos.length > 1;
  for (const clip of list) {
    const globalIdx = detectedClips.indexOf(clip);
    const isActive = activePlayingClip && activePlayingClip.video_path === clip.video_path && activePlayingClip.clip_index === clip.clip_index;
    const tr = document.createElement('tr');
    tr.className = `clip-row ${clip.selected ? '' : 'unselected'} ${isActive ? 'active' : ''}`;
    tr.id = `clip-row-${globalIdx}`;
    const badgeClass = clip.is_high_energy ? 'energy' : (clip.manual ? 'manual' : '');
    const conf = clip.confidence != null ? clip.confidence : 1;
    const hitCount = (clip.hits || []).length;
    const kws = (clip.keywords && clip.keywords.length ? clip.keywords : [clip.primary_keyword]);
    const kwLabel = clip.custom_title || kws[0];
    const extraKws = kws.length > 1 ? `<span class="kw-more" title="${escapeHtml(kws.join(', '))}">+${kws.length - 1} more</span>` : '';
    tr.dataset.idx = String(globalIdx);
    tr.innerHTML = `
      <td class="stop"><input type="checkbox" class="clip-use" ${clip.selected ? 'checked' : ''} data-idx="${globalIdx}"></td>
      <td class="mono muted">${clip.clip_index}</td>
      <td><span class="kw-badge ${badgeClass}">${escapeHtml(kwLabel)}</span>${hitCount > 1 ? `<span class="kw-hits" title="${hitCount} keyword hits inside this clip">×${hitCount}</span>` : ''}${extraKws}${multiVideo ? `<div class="tiny dim ellipsis" style="max-width:150px" title="${escapeHtml(clip.video_filename)}">${escapeHtml(clip.video_filename)}</div>` : ''}</td>
      <td class="mono">${formatShort(clip.start)} → ${formatShort(clip.end)}</td>
      <td class="mono text-green">${clip.duration.toFixed(2)}s</td>
      <td><span class="matched" title="${escapeHtml(clip.matched_text)}">${escapeHtml(clip.matched_text || '')}</span> <span class="conf ${conf < 0.6 ? 'low' : ''}" title="ASR confidence">${Math.round(conf * 100)}%</span></td>
      <td class="right stop">
        <button class="btn small clip-audition" data-idx="${globalIdx}">▶ Audition</button>
        <button class="btn btn-ghost small clip-delete" title="Delete clip" data-idx="${globalIdx}">✕</button>
      </td>`;
    tbody.appendChild(tr);
  }
}
window.renderClipsTable = renderClipsTable;

function initClipTableHandlers() {
  const tbody = $('clips-tbody');
  if (!tbody) return;
  tbody.addEventListener('click', (e) => {
    const audition = e.target.closest('.clip-audition');
    if (audition) { e.stopPropagation(); previewClip(parseInt(audition.dataset.idx, 10)); return; }
    const del = e.target.closest('.clip-delete');
    if (del) { e.stopPropagation(); deleteClip(parseInt(del.dataset.idx, 10)); return; }
    if (e.target.closest('.stop')) return;
    const row = e.target.closest('tr.clip-row');
    if (row && row.dataset.idx !== undefined) previewClip(parseInt(row.dataset.idx, 10));
  });
  tbody.addEventListener('change', (e) => {
    const cb = e.target.closest('.clip-use');
    if (!cb) return;
    const clip = detectedClips[parseInt(cb.dataset.idx, 10)];
    if (clip) toggleClipSelected(clip.video_path, clip.clip_index, cb.checked);
  });
}

function showAnalyzingStateInReviewer() {
  const tbody = $('clips-tbody');
  if (tbody && detectedClips.length === 0) {
    tbody.innerHTML = '<tr><td colspan="7" class="empty"><div class="pulse text-cyan bold">Speech recognition in progress…</div><div class="small muted">Clips will appear here automatically when analysis finishes.</div></td></tr>';
  }
}

async function toggleClipSelected(videoPath, clipIndex, isSelected) {
  try {
    const r = await postJson('/api/clips/update', { video_path: videoPath, clip_index: clipIndex, selected: isSelected });
    if (r && r.clip) mergeClip(r.clip);
  } catch (err) {
    showToast('Failed to update clip: ' + err.message, 'error');
  }
}
window.toggleClipSelected = toggleClipSelected;

async function batchToggleClips(selectAll, invert = false) {
  try {
    const kw = $('clip-filter-keyword').value;
    const vid = $('clip-filter-video').value;
    const params = new URLSearchParams();
    if (invert) params.set('invert', 'true'); else params.set('select_all', selectAll ? 'true' : 'false');
    if (kw) params.set('keyword', kw);
    if (vid) params.set('video_path', vid);
    await postJson('/api/clips/batch_toggle?' + params.toString());
    await loadClips();
  } catch (err) { showToast('Batch toggle failed: ' + err.message, 'error'); }
}
window.batchToggleClips = batchToggleClips;

function toggleActiveClipSelection() {
  const clip = activePlayingClip || window.activePlayingClip || (detectedClips && detectedClips[0]) || (window.detectedClips && window.detectedClips[0]);
  if (!clip) { showToast('No active clip to toggle.', 'warning'); return; }
  activePlayingClip = clip; window.activePlayingClip = clip;
  const next = !clip.selected;
  toggleClipSelected(clip.video_path, clip.clip_index, next);
  showToast(`Clip #${clip.clip_index} (${clip.primary_keyword}): ${next ? 'included' : 'excluded'}`, next ? 'success' : 'warning', 2000);
}
window.toggleActiveClipSelection = toggleActiveClipSelection;

async function deleteClip(idx) {
  const clip = detectedClips[idx];
  if (!clip) return;
  try {
    await postJson('/api/clips/delete', { video_path: clip.video_path, clip_index: clip.clip_index });
    if (activePlayingClip && activePlayingClip.clip_index === clip.clip_index && activePlayingClip.video_path === clip.video_path) {
      activePlayingClip = null; window.activePlayingClip = null;
    }
    await loadClips();
    showToast(`Removed clip #${clip.clip_index}.`, 'info', 2000);
  } catch (err) { showToast('Delete failed: ' + err.message, 'error'); }
}
window.deleteClip = deleteClip;
function deleteActiveClip() { if (activePlayingClip) deleteClip(detectedClips.indexOf(activePlayingClip)); }
window.deleteActiveClip = deleteActiveClip;

async function saveClipTitle() {
  if (!activePlayingClip) return;
  const title = $('clip-title-input').value.trim();
  try {
    const r = await postJson('/api/clips/update', { video_path: activePlayingClip.video_path, clip_index: activePlayingClip.clip_index, custom_title: title });
    if (r && r.clip) mergeClip(r.clip);
    showToast('Clip title saved.', 'success', 1500);
  } catch (err) { showToast('Save failed: ' + err.message, 'error'); }
}
window.saveClipTitle = saveClipTitle;

async function updateClipBounds(clip, start, end) {
  try {
    const r = await postJson('/api/clips/update', { video_path: clip.video_path, clip_index: clip.clip_index, start, end });
    if (r && r.clip) mergeClip(r.clip);
  } catch (err) { showToast('Could not adjust clip: ' + err.message, 'error'); }
}

function nudgeClip(which, delta) {
  if (!activePlayingClip) { showToast('Audition a clip first.', 'warning'); return; }
  const c = activePlayingClip;
  let start = c.start, end = c.end;
  if (which === 'start') start = Math.max(0, Math.min(end - 0.1, start + delta));
  else end = Math.max(start + 0.1, end + delta);
  updateClipBounds(c, Math.round(start * 1000) / 1000, Math.round(end * 1000) / 1000);
}
window.nudgeClip = nudgeClip;

function setClipBoundFromPlayhead(which) {
  const player = $('studio-video-player');
  const t = Math.round((player.currentTime || 0) * 1000) / 1000;
  markers[which] = t;
  updateMarkerLabels();
  if (activePlayingClip) {
    const c = activePlayingClip;
    if (which === 'start' && t < c.end - 0.1) updateClipBounds(c, t, c.end);
    else if (which === 'end' && t > c.start + 0.1) updateClipBounds(c, c.start, t);
    else showToast('Marker would make the clip too short.', 'warning');
  } else {
    showToast(`${which === 'start' ? 'IN' : 'OUT'} marker set at ${formatTimeSec(t)}`, 'info', 1500);
  }
}
window.setClipBoundFromPlayhead = setClipBoundFromPlayhead;

function updateMarkerLabels() {
  $('marker-in').textContent = markers.in != null ? formatTimeSec(markers.in) : '—';
  $('marker-out').textContent = markers.out != null ? formatTimeSec(markers.out) : '—';
}

async function addManualClipFromPlayer() {
  const player = $('studio-video-player');
  const videoPath = currentPlayerPath();
  if (!videoPath) { showToast('Load a video in the player first (audition a clip or click a transcript line).', 'warning'); return; }
  const start = markers.in != null ? markers.in : Math.max(0, (player.currentTime || 0) - 2);
  const end = markers.out != null ? markers.out : Math.min(player.duration || start + 4, (player.currentTime || 0) + 2);
  if (end - start < 0.1) { showToast('OUT must be after IN.', 'warning'); return; }
  try {
    const r = await postJson('/api/clips/add', { video_path: videoPath, start, end, title: $('clip-title-input').value.trim() || 'manual' });
    markers = { in: null, out: null }; updateMarkerLabels();
    await loadClips();
    const idx = detectedClips.findIndex((c) => c.clip_index === r.clip.clip_index && c.video_path === r.clip.video_path);
    if (idx >= 0) previewClip(idx, false);
    showToast('Manual clip added.', 'success', 2000);
  } catch (err) { showToast('Add clip failed: ' + err.message, 'error'); }
}
window.addManualClipFromPlayer = addManualClipFromPlayer;

function currentPlayerPath() {
  const player = $('studio-video-player');
  return player.dataset.path || '';
}

function loadPlayerSource(videoPath, filename) {
  const player = $('studio-video-player');
  if (player.dataset.path !== videoPath) {
    player.dataset.path = videoPath;
    player.src = `/api/media?path=${encodeURIComponent(videoPath)}`;
    $('player-media-title').textContent = filename || videoPath.split(/[\\/]/).pop();
    $('player-media-title').title = videoPath;
    loadWaveform(videoPath);
  }
}

function previewClip(idx, autoplay = true) {
  const clip = detectedClips[idx];
  if (!clip) return;
  activePlayingClip = clip; window.activePlayingClip = clip;
  loadPlayerSource(clip.video_path, clip.video_filename);
  const player = $('studio-video-player');
  const seekAndPlay = () => {
    player.currentTime = clip.start;
    if (autoplay) player.play().catch(() => {});
    updateScrubberUI(clip.start, player.duration);
  };
  if (player.readyState >= 1) seekAndPlay(); else player.addEventListener('loadedmetadata', seekAndPlay, { once: true });
  updateActiveClipDetails();
  renderClipsTable();
  const row = $(`clip-row-${idx}`);
  if (row && row.scrollIntoView) row.scrollIntoView({ block: 'nearest' });
}
window.previewClip = previewClip;

function updateActiveClipDetails() {
  const c = activePlayingClip;
  if (!c) {
    $('clip-detail-keyword').textContent = 'No clip selected';
    $('clip-detail-duration').textContent = '-';
    $('clip-detail-text').textContent = 'Audition a clip from the list to load it here.';
    $('clip-title-input').value = '';
    return;
  }
  $('clip-detail-keyword').textContent = `#${c.clip_index} · ${c.primary_keyword}${c.selected ? '' : ' (excluded)'}`;
  $('clip-detail-duration').textContent = `${formatTimeSec(c.start)} → ${formatTimeSec(c.end)} · ${c.duration.toFixed(2)}s`;
  $('clip-detail-text').textContent = c.matched_text ? `“${c.matched_text}”` : (c.manual ? 'Manual clip' : '');
  if (document.activeElement !== $('clip-title-input')) $('clip-title-input').value = c.custom_title || '';
}

function activeIndexInVisible() {
  const list = visibleClips();
  if (!activePlayingClip) return { list, i: -1 };
  return { list, i: list.findIndex((c) => c.video_path === activePlayingClip.video_path && c.clip_index === activePlayingClip.clip_index) };
}
function nextClip() { const { list, i } = activeIndexInVisible(); if (list.length) previewClip(detectedClips.indexOf(list[Math.min(list.length - 1, i + 1)])); }
function prevClip() { const { list, i } = activeIndexInVisible(); if (list.length) previewClip(detectedClips.indexOf(list[Math.max(0, i <= 0 ? 0 : i - 1)])); }
window.nextClip = nextClip; window.prevClip = prevClip;

function seekToActiveClipStart() {
  if (!activePlayingClip) return;
  const player = $('studio-video-player');
  player.currentTime = activePlayingClip.start;
  player.play().catch(() => {});
}
function seekToActiveClipEnd() {
  if (!activePlayingClip) return;
  const player = $('studio-video-player');
  player.currentTime = Math.max(0, activePlayingClip.end - 0.5);
}
let clipEndWatcher = null;
function playClipSegment() {
  if (!activePlayingClip) return;
  const player = $('studio-video-player');
  const clip = activePlayingClip;
  player.currentTime = clip.start;
  player.play().catch(() => {});
  if (clipEndWatcher) player.removeEventListener('timeupdate', clipEndWatcher);
  clipEndWatcher = () => {
    if (player.currentTime >= clip.end) {
      player.pause();
      player.removeEventListener('timeupdate', clipEndWatcher);
      clipEndWatcher = null;
    }
  };
  player.addEventListener('timeupdate', clipEndWatcher);
}
window.seekToActiveClipStart = seekToActiveClipStart; window.seekToActiveClipEnd = seekToActiveClipEnd; window.playClipSegment = playClipSegment;

// ---------------------------------------------------------------------------
// Waveform & scrubber
// ---------------------------------------------------------------------------
let isScrubbing = false;
let wasPlayingBeforeScrub = false;
let waveformBars = [];

function generateWaveformBars(count = 120) {
  waveformBars = [];
  let prev = 0.3;
  for (let i = 0; i < count; i++) {
    const noise = (Math.random() - 0.48) * 0.3;
    const val = Math.max(0.06, Math.min(0.9, prev + noise + Math.sin(i / 9) * 0.03));
    waveformBars.push(val);
    prev = val;
  }
  return waveformBars;
}

async function loadWaveform(videoPath) {
  waveformPeaks = [];
  waveformForPath = videoPath;
  drawWaveform();
  try {
    const d = await api(`/api/waveform?path=${encodeURIComponent(videoPath)}&buckets=700`);
    if (waveformForPath !== videoPath) return;
    waveformPeaks = d.peaks || [];
    drawWaveform();
  } catch (err) {
    console.warn('Waveform unavailable:', err.message);
  }
}

function drawWaveform() {
  const canvas = $('player-waveform-canvas');
  if (!canvas) return;
  const ctx = canvas.getContext ? canvas.getContext('2d') : null;
  if (!ctx) return;

  const rect = canvas.getBoundingClientRect();
  const dpr = window.devicePixelRatio || 1;
  const width = rect.width || 360;
  const height = rect.height || 64;
  canvas.width = Math.round(width * dpr);
  canvas.height = Math.round(height * dpr);
  ctx.setTransform ? ctx.setTransform(dpr, 0, 0, dpr, 0, 0) : ctx.scale(dpr, dpr);
  ctx.clearRect(0, 0, width, height);

  const peaks = waveformPeaks.length ? waveformPeaks : (waveformBars.length ? waveformBars : generateWaveformBars(120));
  const player = $('studio-video-player');
  const duration = (player && player.duration > 0) ? player.duration : 1;
  const curTime = (player && !isNaN(player.currentTime)) ? player.currentTime : 0;
  const progressRatio = Math.min(1, Math.max(0, curTime / duration));

  const barCount = peaks.length;
  const barSpacing = width / barCount;
  const barWidth = Math.max(1, barSpacing * 0.7);
  const clipStartRatio = activePlayingClip ? activePlayingClip.start / duration : -1;
  const clipEndRatio = activePlayingClip ? activePlayingClip.end / duration : -1;
  const mid = height / 2;

  for (let i = 0; i < barCount; i++) {
    const x = i * barSpacing + (barSpacing - barWidth) / 2;
    const ratio = i / barCount;
    const h = Math.max(2, peaks[i] * (height * 0.9));
    const inside = ratio >= clipStartRatio && ratio <= clipEndRatio;
    if (ratio <= progressRatio) ctx.fillStyle = inside ? '#00f0ff' : '#10b981';
    else if (inside) ctx.fillStyle = 'rgba(0, 240, 255, 0.45)';
    else ctx.fillStyle = 'rgba(120, 136, 170, 0.35)';
    ctx.beginPath();
    if (ctx.roundRect) ctx.roundRect(x, mid - h / 2, barWidth, h, [1.5]); else ctx.rect(x, mid - h / 2, barWidth, h);
    ctx.fill();
  }

  // Marker lines (IN/OUT for manual clips)
  for (const [key, color] of [['in', '#f59e0b'], ['out', '#f59e0b']]) {
    if (markers[key] != null && duration > 0) {
      const mx = (markers[key] / duration) * width;
      ctx.fillStyle = color;
      ctx.fillRect(mx - 1, 0, 2, height);
    }
  }
}
window.drawWaveform = drawWaveform;

function updateScrubberUI(curTime, duration) {
  const player = $('studio-video-player');
  if (!player) return;
  if (curTime === undefined) curTime = player.currentTime || 0;
  if (duration === undefined) duration = player.duration || 0;

  const formattedCur = formatTimeSec(curTime);
  const formattedDur = duration > 0 ? formatTimeSec(duration) : '00:00.000';
  $('player-time-badge').textContent = formattedCur;
  $('scrubber-current-time').textContent = formattedCur;
  $('scrubber-total-duration').textContent = formattedDur;

  const pct = duration > 0 ? Math.min(100, Math.max(0, (curTime / duration) * 100)) : 0;
  $('waveform-playhead').style.left = `${pct}%`;
  const scrubber = $('player-scrubber');
  if (scrubber && !isScrubbing) scrubber.value = pct;

  const clipRegion = $('waveform-clip-region');
  const clipRangeText = $('scrubber-clip-range');
  if (activePlayingClip && duration > 0) {
    const startPct = Math.min(100, Math.max(0, (activePlayingClip.start / duration) * 100));
    const endPct = Math.min(100, Math.max(startPct, (activePlayingClip.end / duration) * 100));
    clipRegion.style.left = `${startPct}%`;
    clipRegion.style.width = `${Math.max(0.4, endPct - startPct)}%`;
    clipRegion.classList.remove('hidden');
    clipRangeText.textContent = `Clip #${activePlayingClip.clip_index} [${activePlayingClip.start.toFixed(1)}s – ${activePlayingClip.end.toFixed(1)}s]`;
    clipRangeText.className = 'text-cyan';
  } else {
    clipRegion.classList.add('hidden');
    clipRangeText.textContent = 'No active clip';
    clipRangeText.className = '';
  }
  drawWaveform();
}
window.updateScrubberUI = updateScrubberUI;

function seekToPercentage(pct, smooth = false) {
  const player = $('studio-video-player');
  if (!player) return;
  const duration = player.duration || 0;
  if (duration <= 0) return;
  const targetTime = Math.min(duration, Math.max(0, (pct / 100) * duration));
  if (smooth && player.fastSeek) player.fastSeek(targetTime); else player.currentTime = targetTime;
  updateScrubberUI(targetTime, duration);
}
window.seekToPercentage = seekToPercentage;

function initWaveformAndScrubber() {
  const player = $('studio-video-player');
  const track = $('waveform-track');
  const scrubber = $('player-scrubber');
  const hoverLine = $('scrubber-hover-line');
  const hoverTooltip = $('scrubber-hover-tooltip');
  if (!player || !track || !scrubber) return;

  generateWaveformBars(120);

  scrubber.addEventListener('input', (e) => { isScrubbing = true; seekToPercentage(parseFloat(e.target.value), true); });
  scrubber.addEventListener('change', (e) => { isScrubbing = false; seekToPercentage(parseFloat(e.target.value), false); });

  const handlePointerMove = (e) => {
    const rect = track.getBoundingClientRect();
    if (rect.width <= 0) return;
    const offsetX = Math.max(0, Math.min(rect.width, (e.clientX || 0) - (rect.left || 0)));
    const pct = (offsetX / rect.width) * 100;
    const hoverTime = (pct / 100) * (player.duration || 0);
    hoverLine.style.left = `${offsetX}px`; hoverLine.classList.remove('hidden');
    hoverTooltip.style.left = `${offsetX}px`; hoverTooltip.textContent = formatTimeSec(hoverTime); hoverTooltip.classList.remove('hidden');
    if (isScrubbing) seekToPercentage(pct, true);
  };
  track.addEventListener('pointerdown', (e) => {
    isScrubbing = true;
    wasPlayingBeforeScrub = !player.paused;
    player.pause();
    if (track.setPointerCapture && e.pointerId !== undefined) { try { track.setPointerCapture(e.pointerId); } catch (_) {} }
    handlePointerMove(e);
  });
  track.addEventListener('pointermove', handlePointerMove);
  const endScrubbing = (e) => {
    if (!isScrubbing) return;
    isScrubbing = false;
    if (track.releasePointerCapture && e.pointerId !== undefined) { try { track.releasePointerCapture(e.pointerId); } catch (_) {} }
    if (wasPlayingBeforeScrub) player.play().catch(() => {});
  };
  track.addEventListener('pointerup', endScrubbing);
  track.addEventListener('pointercancel', endScrubbing);
  track.addEventListener('pointerleave', () => { if (!isScrubbing) { hoverLine.classList.add('hidden'); hoverTooltip.classList.add('hidden'); } });

  player.addEventListener('timeupdate', () => { if (!isScrubbing) updateScrubberUI(); highlightTranscriptAtTime(player.currentTime); });
  player.addEventListener('loadedmetadata', () => { updateScrubberUI(); drawWaveform(); });
  player.addEventListener('durationchange', () => { updateScrubberUI(); drawWaveform(); });
  player.addEventListener('error', () => { if (player.dataset.path) showToast('The browser could not play this file. Rendered outputs (MP4) always play; try auditioning after render.', 'warning', 6000); });
  window.addEventListener('resize', drawWaveform);
}

// ---------------------------------------------------------------------------
// Keyboard shortcuts
// ---------------------------------------------------------------------------
function initKeyboardShortcuts() {
  window.addEventListener('keydown', (e) => {
    const target = e.target;
    const tag = (target && target.tagName ? target.tagName.toLowerCase() : '');
    if (tag === 'input' || tag === 'textarea' || tag === 'select' || (target && target.isContentEditable)) return;
    if ((e.ctrlKey || e.metaKey) && !e.altKey) {
      if (e.key === '=' || e.key === '+') { e.preventDefault(); changeUiScale(0.1); return; }
      if (e.key === '-' || e.key === '_') { e.preventDefault(); changeUiScale(-0.1); return; }
      if (e.key === '0') { e.preventDefault(); changeUiScale(0); return; }
      return;
    }
    if (e.altKey) return;

    const key = e.key;
    if (key === 'Escape') {
      if (!$('modal-backdrop').classList.contains('hidden')) { closeModals(); return; }
      if (jobRunning) { e.preventDefault(); cancelJob(); }
      return;
    }
    if (key === '?') { e.preventDefault(); openShortcutsModal(); return; }
    if (['1', '2', '3', '4', '5'].includes(key)) { switchTab(['setup', 'reviewer', 'transcript', 'export', 'tools'][parseInt(key, 10) - 1]); return; }

    const player = $('studio-video-player');
    if (!player) return;

    if (e.code === 'Space' || key === ' ') {
      e.preventDefault();
      if (player.paused) { player.play().catch(() => {}); showToast('▶ Playing', 'info', 900); }
      else { player.pause(); showToast('⏸ Paused', 'info', 900); }
      return;
    }
    if (key === 'j' || key === 'J') { e.preventDefault(); player.currentTime = Math.max(0, (player.currentTime || 0) - 2); updateScrubberUI(); return; }
    if (key === 'l' || key === 'L') { e.preventDefault(); player.currentTime = Math.min(player.duration || Infinity, (player.currentTime || 0) + 2); updateScrubberUI(); return; }
    if (key === 'ArrowLeft') { e.preventDefault(); player.currentTime = Math.max(0, (player.currentTime || 0) - 0.1); updateScrubberUI(); return; }
    if (key === 'ArrowRight') { e.preventDefault(); player.currentTime = Math.min(player.duration || Infinity, (player.currentTime || 0) + 0.1); updateScrubberUI(); return; }
    if (key === 's' || key === 'S') { e.preventDefault(); toggleActiveClipSelection(); return; }
    if (key === 'n' || key === 'N' || key === 'ArrowDown') { e.preventDefault(); nextClip(); return; }
    if (key === 'p' || key === 'P' || key === 'ArrowUp') { e.preventDefault(); prevClip(); return; }
    if (key === 'Enter') { e.preventDefault(); playClipSegment(); return; }
    if (key === 'i' || key === 'I') { e.preventDefault(); setClipBoundFromPlayhead('start'); return; }
    if (key === 'o' || key === 'O') { e.preventDefault(); setClipBoundFromPlayhead('end'); return; }
    if (key === '[') { e.preventDefault(); nudgeClip('start', -0.5); return; }
    if (key === ']') { e.preventDefault(); nudgeClip('end', 0.5); return; }
    if (key === '{') { e.preventDefault(); nudgeClip('start', 0.5); return; }
    if (key === '}') { e.preventDefault(); nudgeClip('end', -0.5); return; }
    if (key === 'Home') { e.preventDefault(); seekToActiveClipStart(); return; }
    if (key === 'End') { e.preventDefault(); seekToActiveClipEnd(); return; }
    if (key === 'Delete') { e.preventDefault(); deleteActiveClip(); return; }
  });
}

// ---------------------------------------------------------------------------
// Transcript inspector
// ---------------------------------------------------------------------------
async function loadTranscripts() {
  try {
    const data = await api('/api/transcript');
    allTranscripts = data.transcripts || [];
    const select = $('transcript-video-select');
    const prev = select.value;
    select.innerHTML = '';
    if (allTranscripts.length === 0) {
      select.innerHTML = '<option value="">No transcripts loaded</option>';
      $('transcript-container').innerHTML = '<div class="empty">No transcripts loaded yet. Run analysis to transcribe your media.</div>';
      $('transcript-meta').textContent = '';
      return;
    }
    for (const t of allTranscripts) {
      const opt = document.createElement('option');
      opt.value = t.video_path;
      opt.textContent = `${t.filename} (${formatDuration(t.duration)})`;
      select.appendChild(opt);
    }
    select.value = allTranscripts.some((t) => t.video_path === prev) ? prev : allTranscripts[0].video_path;
    renderTranscriptForSelectedVideo();
  } catch (err) {
    console.error('Failed to load transcripts:', err);
  }
}
window.loadTranscripts = loadTranscripts;

function keywordMatcher() {
  const kws = (appConfig.keywords || []).map((k) => k.toLowerCase().trim()).filter(Boolean);
  const singles = new Set(kws.filter((k) => !k.includes(' ')));
  const clean = (w) => w.toLowerCase().replace(/[^\p{L}\p{N}]/gu, '');
  return (wordText) => {
    const c = clean(wordText);
    if (!c) return false;
    if (singles.has(c)) return true;
    if (c.endsWith('s') && singles.has(c.slice(0, -1))) return true;
    if (c.endsWith('es') && singles.has(c.slice(0, -2))) return true;
    for (const k of kws) if (k.includes(' ') && k.split(' ').includes(c)) return true;
    return false;
  };
}

function renderTranscriptForSelectedVideo() {
  const select = $('transcript-video-select');
  const videoPath = select.value;
  const t = allTranscripts.find((x) => x.video_path === videoPath);
  const container = $('transcript-container');
  container.innerHTML = '';
  if (!t || !t.segments || t.segments.length === 0) {
    container.innerHTML = '<div class="empty">No speech segments detected for this video.</div>';
    $('transcript-meta').textContent = '';
    return;
  }
  $('transcript-meta').textContent = `${t.model_name || 'asr'} · ${t.language} · ${t.word_count} words${t.cached ? ' · cached' : ''}`;
  const spans = Array.isArray(t.hits) ? t.hits : null;
  const fallback = keywordMatcher();
  // Prefer the engine's own hit spans so the page shows exactly what matched (sister's, sisters, Sister?).
  const isHit = (w) => spans ? spans.some((h) => w.start >= h.start - 0.02 && w.end <= h.end + 0.02) : fallback(w.text);
  const frag = document.createDocumentFragment();
  t.segments.forEach((seg, sIdx) => {
    const line = document.createElement('div');
    line.className = 'transcript-line';
    line.dataset.text = (seg.text || '').toLowerCase();
    line.dataset.start = seg.start;
    line.dataset.end = seg.end;
    const m = Math.floor(seg.start / 60), s = Math.floor(seg.start % 60);
    let body;
    if (seg.words && seg.words.length) {
      body = seg.words.map((w) => {
        const cls = ['w'];
        if (isHit(w)) cls.push('hit');
        if (w.confidence != null && w.confidence < 0.5) cls.push('low');
        return `<span class="${cls.join(' ')}" data-t="${w.start}" title="${w.start.toFixed(2)}s · ${Math.round((w.confidence || 1) * 100)}%">${escapeHtml(w.text)}</span>`;
      }).join(' ');
    } else {
      body = escapeHtml(seg.text);
    }
    line.innerHTML = `<span class="ts">${String(m).padStart(2, '0')}:${String(s).padStart(2, '0')}</span><span class="txt">${body}</span><button class="btn btn-ghost small add" title="Add this segment as a clip">+ clip</button>`;
    line.querySelector('.add').onclick = async (e) => {
      e.stopPropagation();
      try {
        await postJson('/api/clips/add', { video_path: t.video_path, start: seg.start, end: seg.end, title: (seg.text || '').slice(0, 40) });
        await loadClips();
        showToast('Segment added as a clip.', 'success', 2000);
      } catch (err) { showToast('Add failed: ' + err.message, 'error'); }
    };
    line.onclick = (e) => {
      const wordEl = e.target.closest && e.target.closest('.w');
      const seekTo = wordEl && wordEl.dataset.t ? parseFloat(wordEl.dataset.t) : seg.start;
      const player = $('studio-video-player');
      loadPlayerSource(t.video_path, t.filename);
      const go = () => { player.currentTime = seekTo; player.play().catch(() => {}); updateScrubberUI(seekTo, player.duration); };
      if (player.readyState >= 1) go(); else player.addEventListener('loadedmetadata', go, { once: true });
      switchTab('reviewer');
    };
    frag.appendChild(line);
  });
  container.appendChild(frag);
  filterTranscriptLines();
}
window.renderTranscriptForSelectedVideo = renderTranscriptForSelectedVideo;

function filterTranscriptLines() {
  const query = ($('transcript-search-input').value || '').trim().toLowerCase();
  const lines = document.querySelectorAll('.transcript-line');
  let matches = 0;
  const hitWords = document.querySelectorAll('.transcript-line .w.hit').length;
  lines.forEach((l) => {
    const text = l.dataset.text || '';
    const show = !query || text.includes(query);
    l.classList.toggle('hidden', !show);
    if (show) matches++;
    l.querySelectorAll('.w.search').forEach((w) => w.classList.remove('search'));
    if (query && show) {
      l.querySelectorAll('.w').forEach((w) => { if (w.textContent.toLowerCase().includes(query)) w.classList.add('search'); });
    }
  });
  $('transcript-match-count').textContent = query
    ? `${matches} line${matches === 1 ? '' : 's'} match`
    : `${lines.length} lines · ${hitWords} keyword hit${hitWords === 1 ? '' : 's'}`;
}
window.filterTranscriptLines = filterTranscriptLines;

let lastHighlightedLine = null;
function highlightTranscriptAtTime(t) {
  if (activeTab !== 'transcript' && activeTab !== 'reviewer') return;
  const select = $('transcript-video-select');
  if (!select.value || select.value !== currentPlayerPath()) return;
  const lines = document.querySelectorAll('.transcript-line');
  let found = null;
  for (const l of lines) {
    if (t >= parseFloat(l.dataset.start) && t <= parseFloat(l.dataset.end)) { found = l; break; }
  }
  if (found !== lastHighlightedLine) {
    if (lastHighlightedLine) lastHighlightedLine.classList.remove('active');
    if (found) { found.classList.add('active'); if (activeTab === 'transcript') found.scrollIntoView({ block: 'nearest' }); }
    lastHighlightedLine = found;
  }
}

function exportTranscript(fmt) {
  const vp = $('transcript-video-select').value;
  if (!vp) { showToast('No transcript loaded.', 'warning'); return; }
  const url = `/api/transcript/export?video_path=${encodeURIComponent(vp)}&fmt=${fmt}`;
  const a = document.createElement('a');
  a.href = url; a.download = ''; document.body.appendChild(a); a.click(); a.remove();
}
window.exportTranscript = exportTranscript;

async function copyTranscript() {
  const vp = $('transcript-video-select').value;
  const t = allTranscripts.find((x) => x.video_path === vp);
  if (!t) return;
  try { await navigator.clipboard.writeText(t.full_text || ''); showToast('Transcript copied to clipboard.', 'success', 2000); }
  catch (_) { showToast('Clipboard unavailable in this window.', 'warning'); }
}
window.copyTranscript = copyTranscript;

// ---------------------------------------------------------------------------
// Delivery
// ---------------------------------------------------------------------------
const ARTIFACT_LABEL = { master: 'MP4', stitched: 'REEL', clip: 'CLIP', subtitle: 'SUB', nle: 'NLE', report: 'RPT', thumbnail: 'IMG', transcript: 'TXT', contact_sheet: 'IMG' };

async function loadLatestResult() {
  try {
    const data = await api('/api/result');
    if (!data.has_result) { $('render-result-banner').classList.add('hidden'); $('render-empty').classList.remove('hidden'); $('badge-result').classList.add('hidden'); return; }
    latestResult = data;
    $('badge-result').classList.remove('hidden');
    $('render-empty').classList.add('hidden');
    const banner = $('render-result-banner');
    banner.classList.remove('hidden');
    $('render-time-stat').textContent = `${data.total_clips_count} clips · ${formatDuration(data.total_clip_footage_sec)} footage · rendered in ${formatDuration(data.total_duration_sec)}${data.encoder_used ? ' · ' + data.encoder_used : ''}`;

    const master = data.primary_output;
    const finalPlayer = $('final-video-player');
    if (master) {
      finalPlayer.src = `/api/media?path=${encodeURIComponent(master)}`;
      finalPlayer.addEventListener('loadedmetadata', () => { if (finalPlayer.currentTime < 0.01) finalPlayer.currentTime = 0.05; }, { once: true });
      $('btn-download-master').href = `/api/download?path=${encodeURIComponent(master)}`;
      $('btn-download-master').classList.remove('hidden');
      const setupPlayer = $('setup-final-player');
      setupPlayer.src = finalPlayer.src;
      setupPlayer.addEventListener('loadedmetadata', () => { if (setupPlayer.currentTime < 0.01) setupPlayer.currentTime = 0.05; }, { once: true });
      $('btn-setup-download-master').href = $('btn-download-master').href;
      $('setup-completion-card').classList.remove('hidden');
      $('setup-completion-stat').textContent = `${data.total_clips_count} clips · ${formatDuration(data.total_clip_footage_sec)} · ${(master || '').split(/[\\/]/).pop()}`;
    } else {
      $('btn-download-master').classList.add('hidden');
    }
    const srt = (data.artifacts || []).find((a) => a.kind === 'subtitle' && a.filename.endsWith('.srt'));
    $('btn-download-srt').classList.toggle('hidden', !srt);
    if (srt) $('btn-download-srt').href = `/api/download?path=${encodeURIComponent(srt.path)}`;
    if (data.report_json_path) $('btn-download-report').href = `/api/download?path=${encodeURIComponent(data.report_json_path)}`;

    $('result-stats').innerHTML = [
      ['Keyword hits', data.total_hits], ['Clips rendered', data.total_clips_count], ['Footage', formatDuration(data.total_clip_footage_sec)],
      ['Render time', formatDuration(data.total_duration_sec)], ['Artifacts', (data.artifacts || []).length],
    ].map(([l, v]) => `<div class="stat"><div class="v">${escapeHtml(String(v))}</div><div class="l">${l}</div></div>`).join('');

    const list = $('artifacts-list');
    list.innerHTML = '';
    for (const a of data.artifacts || []) {
      if (!a.exists) continue;
      const el = document.createElement('div');
      el.className = 'artifact';
      const isMedia = /\.(mp4|mkv|mov|webm|m4v)$/i.test(a.filename);
      el.innerHTML = `
        <div class="kind ${a.kind}">${ARTIFACT_LABEL[a.kind] || 'FILE'}</div>
        <div class="info"><div class="n" title="${escapeHtml(a.path)}">${escapeHtml(a.label || a.filename)}</div><div class="m">${escapeHtml(a.filename)} · ${formatBytes(a.size_bytes)}</div></div>
        <div class="acts">
          ${isMedia ? `<button class="btn btn-ghost small act-preview" title="Preview" data-path="${escapeHtml(a.path)}">▶</button>` : ''}
          <a class="btn btn-ghost small" title="Download" href="/api/download?path=${encodeURIComponent(a.path)}" download>⤓</a>
          <button class="btn btn-ghost small act-open" title="Show in folder" data-path="${escapeHtml(a.path)}">📁</button>
        </div>`;
      list.appendChild(el);
    }
    const tb = $('per-video-tbody');
    tb.innerHTML = (data.per_video || []).map((v) => `<tr><td>${escapeHtml(v.video)}</td><td class="mono">${v.hits}</td><td class="mono">${v.clips}</td><td class="mono">${v.transcribe_s}s</td><td class="mono">${v.encode_s}s</td><td class="mono ${v.failed ? 'text-rose' : ''}">${escapeHtml(v.failed ? (v.error || 'failed') : (v.output || '—'))}</td></tr>`).join('');
  } catch (err) {
    console.error('Failed to load result:', err);
  }
}
window.loadLatestResult = loadLatestResult;

function initPathActionHandlers() {
  document.addEventListener('click', (e) => {
    const open = e.target.closest('.act-open');
    if (open && open.dataset.path) { e.preventDefault(); openPath(open.dataset.path); return; }
    const prev = e.target.closest('.act-preview');
    if (prev && prev.dataset.path) { e.preventDefault(); previewArtifact(prev.dataset.path); }
  });
}

function previewArtifact(path) {
  const p = $('final-video-player');
  p.src = `/api/media?path=${encodeURIComponent(path)}`;
  p.play().catch(() => {});
  p.scrollIntoView({ block: 'center' });
}
window.previewArtifact = previewArtifact;

async function loadJobHistory() {
  try {
    const d = await api('/api/jobs');
    const box = $('job-history');
    if (!d.jobs || d.jobs.length === 0) { box.innerHTML = '<div class="empty">No jobs yet this session.</div>'; return; }
    box.innerHTML = d.jobs.map((j) => {
      const when = new Date(j.finished_at * 1000).toLocaleTimeString();
      const out = j.output ? `<span class="link act-open" data-path="${escapeHtml(j.output)}">${escapeHtml(j.output.split(/[\\/]/).pop())}</span>` : '<span class="dim">—</span>';
      return `<div class="history-item"><span class="k">${escapeHtml(j.kind)}</span><span class="mono dim">${when}</span><span>${j.clips} clips · ${j.hits} hits${j.keywords != null ? ' · ' + j.keywords + ' keywords' : ''}</span><span class="mono dim">${formatDuration(j.elapsed)}</span><span>${j.cancelled ? '<span class="text-rose">cancelled</span>' : out}</span></div>`;
    }).join('');
  } catch (_) {}
}
window.loadJobHistory = loadJobHistory;

// ---------------------------------------------------------------------------
// Bootstrap
// ---------------------------------------------------------------------------
window.addEventListener('DOMContentLoaded', async () => {
  try { loadUiScale(); } catch (err) { console.error('UI scale init error:', err); }
  try { initWaveformAndScrubber(); } catch (err) { console.error('Scrubber init error:', err); }
  try { initKeyboardShortcuts(); } catch (err) { console.error('Keyboard init error:', err); }
  try { initDropZone(); } catch (err) { console.error('Dropzone init error:', err); }
  try { initClipTableHandlers(); initPathActionHandlers(); } catch (err) { console.error('Handler init error:', err); }
  try { bindConfigInputs(); } catch (err) { console.error('Config bind error:', err); }
  try { await loadConfig(); } catch (err) { console.error('Config init error:', err); }
  try { loadSystemInfo(false); } catch (err) { console.error('System info error:', err); }
  try { initSSE(); } catch (err) { console.error('SSE init error:', err); }
  try { await loadClips(); } catch (err) { console.error('Clips init error:', err); }
  try { await loadTranscripts(); } catch (err) { console.error('Transcripts init error:', err); }
  try { await loadLatestResult(); } catch (err) { console.error('Result init error:', err); }
  try { loadRecentLogs(); } catch (err) { console.error('Logs init error:', err); }
  try {
    const saved = localStorage.getItem('supercut.tab');
    if (saved && $('tab-' + saved) && saved !== 'setup' && (detectedClips.length || latestResult)) switchTab(saved);
  } catch (_) {}
  updateMarkerLabels();
  document.querySelectorAll('details.drawer').forEach((d) => {
    try { if (localStorage.getItem('supercut.drawer.' + d.id) === '1') d.open = true; } catch (_) {}
    d.addEventListener('toggle', () => { try { localStorage.setItem('supercut.drawer.' + d.id, d.open ? '1' : '0'); } catch (_) {} });
  });
  window.addEventListener('beforeunload', () => { if (configDirty) { try { navigator.sendBeacon && navigator.sendBeacon('/api/config', new Blob([JSON.stringify(collectCurrentConfig())], { type: 'application/json' })); } catch (_) {} } });
});

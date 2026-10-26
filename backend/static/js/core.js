// Shared state, API helper, element references and small utilities.
// Loaded first; every other script uses these.

const MEAL_TYPES = [
  { key: 'breakfast', label: 'Breakfast' },
  { key: 'lunch', label: 'Lunch' },
  { key: 'dinner', label: 'Dinner' },
  { key: 'snack', label: 'Snacks' },
];
const MAIN_MEALS = ['breakfast', 'lunch', 'dinner'];

const TOKEN_KEY = 'foodlog_token';

// Pick up a token passed via URL hash (e.g. https://app/#token=app_xyz)
// and stash it. Strip from URL so it doesn't end up in browser history.
(function readTokenFromHash() {
  const m = location.hash.match(/[#&]token=([^&]+)/);
  if (m) {
    localStorage.setItem(TOKEN_KEY, decodeURIComponent(m[1]));
    history.replaceState(null, '', location.pathname);
  }
})();

async function api(url, options = {}) {
  const token = localStorage.getItem(TOKEN_KEY);
  const headers = { ...(options.headers || {}) };
  if (token) headers['Authorization'] = `Bearer ${token}`;
  const response = await fetch(url, { ...options, headers });
  if (response.status === 401) {
    showAuthBanner();
    throw new Error('Not authorized. Open the URL with #token=… to set your app token.');
  }
  return response;
}

function showAuthBanner() {
  if (document.getElementById('auth-banner')) return;
  const banner = document.createElement('div');
  banner.id = 'auth-banner';
  banner.className = 'error';
  banner.style.margin = '12px 0';
  banner.textContent = 'Auth required. Reopen the URL with #token=… to set your app token.';
  document.body.insertBefore(banner, document.body.firstChild);
}

let currentDate = todayISO();
let dayMeals = [];           // meals for currentDate, as last fetched
let currentEntry = null;     // meal open in the entry detail view
let profileData = null;      // { profile, targets } from /profile
let setupReturnView = 'day'; // where the setup view goes back to
let pendingAnalysis = null;
let pendingPreviewUrl = null;
let pendingFile = null;      // photo being reviewed (null when describing in text)
let pendingNotes = [];       // what the user typed/said during this review
let entryRevision = null;    // corrected estimate for the open entry, not yet saved
let favourites = [];         // starred meals from /favourites, most-used first

const views = {
  day: document.getElementById('day-view'),
  review: document.getElementById('review-view'),
  entry: document.getElementById('entry-view'),
  setup: document.getElementById('setup-view'),
  summary: document.getElementById('summary-view'),
  sleep: document.getElementById('sleep-view'),
  fitness: document.getElementById('fitness-view'),
};
const tabBar = document.getElementById('tab-bar');
const summaryContent = document.getElementById('summary-content');
const summarySubtitle = document.getElementById('summary-subtitle');
const monthLabel = document.getElementById('month-label');
const todayBtn = document.getElementById('today-btn');
const dayTotals = document.getElementById('day-totals');
const weekStripEl = document.getElementById('week-strip');
const mealSections = document.getElementById('meal-sections');
const snapBtn = document.getElementById('snap-btn');
const describeBtn = document.getElementById('describe-btn');
const logTarget = document.getElementById('log-target');
const photoInput = document.getElementById('photo-input');
const notePanel = document.getElementById('note-panel');
const noteLabel = document.getElementById('note-label');
const noteInput = document.getElementById('note-input');
const noteSubmit = document.getElementById('note-submit');
const entryNoteInput = document.getElementById('entry-note-input');
const entryNoteSubmit = document.getElementById('entry-note-submit');
const entryRevised = document.getElementById('entry-revised');
const cancelBtn = document.getElementById('cancel-btn');
const statusEl = document.getElementById('status');
const resultEl = document.getElementById('result');
const labelPrompt = document.getElementById('label-prompt');
const mealTypeButtons = document.getElementById('meal-type-buttons');
const redoBtn = document.getElementById('redo-btn');
const entryTitle = document.getElementById('entry-title');
const entryDetail = document.getElementById('entry-detail');
const entryBack = document.getElementById('entry-back');
const entryDelete = document.getElementById('entry-delete');
const entryFav = document.getElementById('entry-fav');
const setupForm = document.getElementById('setup-form');
const setupBack = document.getElementById('setup-back');
const setupSave = document.getElementById('setup-save');
const setupError = document.getElementById('setup-error');
const setupResult = document.getElementById('setup-result');

// Cache per-day summaries (count + calories + meal types) keyed by ISO date.
// loadDay updates the cache for currentDate after a fresh fetch, so
// the week-strip dots stay in sync without explicit invalidation.
const daySummaryCache = new Map();

function todayISO() {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function shiftDate(iso, days) {
  const d = new Date(iso + 'T00:00:00');
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`;
}

function formatDateLabel(iso) {
  const today = todayISO();
  if (iso === today) return 'Today';
  if (iso === shiftDate(today, -1)) return 'Yesterday';
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(undefined, { weekday: 'short', month: 'short', day: 'numeric' });
}

function formatShortDate(iso) {
  const d = new Date(iso + 'T00:00:00');
  return d.toLocaleDateString(undefined, { month: 'short', day: 'numeric' });
}

function fmt(n) {
  return Math.round(n).toLocaleString();
}

function escapeHtml(s) {
  const div = document.createElement('div');
  div.textContent = s;
  return div.innerHTML;
}

// Sum protein/carbs/fat over items that have macros (meals logged before
// macros were added have nulls). Returns null if no item has macros.
function sumMacros(items) {
  const withMacros = items.filter(i => i.protein_g !== null && i.protein_g !== undefined);
  if (withMacros.length === 0) return null;
  const sum = key => Math.round(withMacros.reduce((s, i) => s + (i[key] || 0), 0));
  return { p: sum('protein_g'), c: sum('carbs_g'), f: sum('fat_g') };
}

function macroText(m) {
  return `P ${m.p}g · C ${m.c}g · F ${m.f}g`;
}

function showView(name) {
  for (const [key, el] of Object.entries(views)) el.hidden = key !== name;
  // Tab bar shows on the two top-level tabs, hides in sub-views
  const topLevel = ['day', 'summary', 'sleep', 'fitness'].includes(name);
  tabBar.classList.toggle('hidden', !topLevel);
  document.body.classList.toggle('no-tab-bar', !topLevel);
  document.querySelectorAll('.tab').forEach(t => {
    t.classList.toggle('active', t.dataset.tab === name);
  });
  if (!topLevel) window.scrollTo(0, 0);
}

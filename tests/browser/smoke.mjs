// Drives headless Chrome over the DevTools protocol through one scenario.
//
//   node smoke.mjs <app-url> <scenario>
//
// Prints one JSON line {ok, detail} and exits 0 (pass), 1 (fail) or
// 3 (no Chrome found). Any uncaught page error fails the scenario.
// Needs Node 22+ (built-in WebSocket and fetch). CHROME_PATH overrides
// the browser location.

import { spawn, spawnSync } from 'node:child_process';
import { existsSync, mkdtempSync, rmSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

const [url, scenarioName] = process.argv.slice(2);

// ---------------------------------------------------------------------------
// Scenarios run inside the page, with access to the app's globals.
// `waitFor` polls a condition; `check` throws with a message on failure.
// Each returns a short description of what it verified.
// ---------------------------------------------------------------------------

const PRELUDE = `
  const sleep = ms => new Promise(r => setTimeout(r, ms));
  const waitFor = async (cond, what, ms = 8000) => {
    for (let t = 0; t < ms; t += 100) { try { if (cond()) return; } catch (e) {} await sleep(100); }
    throw new Error('timed out waiting for ' + what);
  };
  const check = (cond, what) => { if (!cond) throw new Error('check failed: ' + what); };
  const visible = el => el && !el.hidden && el.offsetParent !== null;
  const ready = () => waitFor(() => profileData && !syncInFlight, 'app start-up');
`;

const SCENARIOS = {
  async food_log() {
    await ready();
    await waitFor(() => document.querySelectorAll('.meal-entry').length === 1, 'the seeded meal');
    const days = [...document.querySelectorAll('.week-day')].map(b => b.dataset.date);
    check(days.length === 7 && days[3] === todayISO(), 'today is the middle of the week strip');
    check(visible(views.day) && !visible(views.setup), 'food log shown, not setup');
    check(document.getElementById('day-totals').textContent.includes('of 2,750 cal'), 'day total shows the target');
    return 'week strip, seeded meal and target shown';
  },

  async first_run_setup() {
    await waitFor(() => profileData, 'profile load');
    check(visible(views.setup), 'setup shown when there is no profile');
    check(getComputedStyle(document.getElementById('setup-back')).visibility === 'hidden', 'no Back on first run');
    const f = document.getElementById('setup-form').elements;
    f.age.value = 30; f.height_cm.value = 180; f.weight_kg.value = 80;
    f.sex.value = 'male'; f.activity.value = 'active'; f.goal.value = 'maintain';
    document.getElementById('setup-save').click();
    await waitFor(() => document.getElementById('setup-done'), 'targets result');
    check(document.getElementById('setup-result').textContent.includes('2,750'), 'calorie target shown');
    document.getElementById('setup-done').click();
    check(visible(views.day), 'back to the food log');
    return 'setup saved and showed targets';
  },

  async describe_correct_save_edit() {
    await ready();
    document.getElementById('describe-btn').click();
    check(document.getElementById('note-label').textContent === 'What did you have?', 'describe prompt');
    document.getElementById('note-input').value = 'two slices of sourdough';
    document.getElementById('note-submit').click();
    await waitFor(() => resultEl.textContent.includes('version 1'), 'first estimate');
    check(!mealTypeButtons.hidden, 'meal type buttons shown');

    document.getElementById('note-input').value = 'actually two big slices';
    document.getElementById('note-submit').click();
    await waitFor(() => resultEl.textContent.includes('version 2'), 'corrected estimate');
    check(resultEl.textContent.includes('two slices of sourdough; actually two big slices'), 'notes listed');

    document.querySelector('.meal-type-btn[data-type=snack]').click();
    await waitFor(() => visible(views.day) && dayMeals.some(m => m.meal_type === 'snack'), 'saved snack');
    const saved = dayMeals.find(m => m.meal_type === 'snack');
    check(saved.source === 'text', 'saved as a text entry');

    document.querySelector(`[data-entry-id="${saved.id}"]`).click();
    await waitFor(() => visible(views.entry), 'entry detail');
    document.getElementById('entry-note-input').value = 'it had butter too';
    document.getElementById('entry-note-submit').click();
    await waitFor(() => document.getElementById('entry-save'), 'revised estimate');
    document.getElementById('entry-save').click();
    await waitFor(() => currentEntry && (currentEntry.note || '').includes('butter'), 'saved correction');
    check(currentEntry.updated_at, 'entry marked as updated');
    return 'described, corrected, saved and edited a meal';
  },

  async summary() {
    await ready();
    document.querySelector('[data-tab=summary]').click();
    await waitFor(() => document.querySelector('#summary-content .bullet-card li'), 'analysis');
    check(document.querySelectorAll('.cal-dot').length === 7, 'logging calendar has 7 days');
    check(document.querySelector('.chart-svg'), 'calorie chart drawn');
    check(document.querySelector('.meter'), 'target meters drawn');
    return 'analysis, meters, chart and calendar rendered';
  },

  async summary_claude_down() {
    await ready();
    document.querySelector('[data-tab=summary]').click();
    await waitFor(() => document.querySelector('#summary-content .error'), 'analysis error');
    check(document.querySelector('#summary-content .error').textContent.includes('service unavailable'), 'error explains');
    check(document.querySelector('.chart-svg') && document.querySelectorAll('.cal-dot').length === 7, 'stats still shown');
    return 'stats rendered with the analysis error in place';
  },

  async sleep_and_fitness() {
    await ready();
    await waitFor(() => profileData.targets.basis === 'garmin', 'sync to update the target');
    document.querySelector('[data-tab=sleep]').click();
    await waitFor(() => document.querySelectorAll('#sleep-content .list-row').length > 0, 'nights');
    check(document.querySelector('#sleep-content .chart-svg rect'), 'sleep chart bars');
    check(document.getElementById('sleep-sync').textContent.includes('synced'), 'sync line');

    document.querySelector('[data-tab=fitness]').click();
    await waitFor(() => document.querySelectorAll('#fitness-content .list-row').length > 0, 'workouts');
    check(document.querySelector('.target-note').textContent.includes('active calories'), 'measured target note');
    return 'sleep and fitness tabs rendered from synced data';
  },

  async garmin_not_connected() {
    await ready();
    document.querySelector('[data-tab=sleep]').click();
    await waitFor(() => document.querySelector('#sleep-content .summary-empty'), 'empty state');
    check(document.getElementById('sleep-content').textContent.includes("isn't connected"), 'explains how to connect');
    return 'not-connected message shown';
  },
};

// ---------------------------------------------------------------------------
// Chrome plumbing
// ---------------------------------------------------------------------------

function findChrome() {
  if (process.env.CHROME_PATH) return process.env.CHROME_PATH;
  const candidates = process.platform === 'win32'
    ? ['C:/Program Files/Google/Chrome/Application/chrome.exe',
       'C:/Program Files (x86)/Microsoft/Edge/Application/msedge.exe']
    : process.platform === 'darwin'
      ? ['/Applications/Google Chrome.app/Contents/MacOS/Google Chrome']
      : [];
  for (const path of candidates) if (existsSync(path)) return path;
  for (const name of ['google-chrome', 'google-chrome-stable', 'chromium', 'chromium-browser']) {
    const found = spawnSync('which', [name], { encoding: 'utf8' });
    if (found.status === 0 && found.stdout.trim()) return found.stdout.trim();
  }
  return null;
}

function finish(code, result) {
  console.log(JSON.stringify(result));
  process.exit(code);
}

const scenario = SCENARIOS[scenarioName];
if (!url || !scenario) finish(1, { ok: false, detail: `usage: smoke.mjs <url> <${Object.keys(SCENARIOS).join('|')}>` });
const chromePath = findChrome();
if (!chromePath) finish(3, { ok: false, detail: 'Chrome not found (set CHROME_PATH)' });

const profileDir = mkdtempSync(join(tmpdir(), 'smoke-'));
const port = 9300 + Math.floor(Math.random() * 600);
const args = ['--headless=new', '--disable-gpu', '--no-first-run', '--disable-dev-shm-usage',
  `--remote-debugging-port=${port}`, `--user-data-dir=${profileDir}`, 'about:blank'];
if (process.platform === 'linux') args.push('--no-sandbox');
const chrome = spawn(chromePath, args, { stdio: 'ignore' });

const cleanUp = () => { try { chrome.kill(); } catch {} try { rmSync(profileDir, { recursive: true, force: true }); } catch {} };
const timer = setTimeout(() => { cleanUp(); finish(1, { ok: false, detail: 'scenario timed out' }); }, 60000);

try {
  let target;
  for (let i = 0; i < 100 && !target; i++) {
    await new Promise(r => setTimeout(r, 150));
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json`)).json()).find(t => t.type === 'page'); } catch {}
  }
  if (!target) throw new Error('Chrome did not start');

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((resolve, reject) => { ws.onopen = resolve; ws.onerror = reject; });
  let nextId = 0;
  const pending = new Map();
  const pageErrors = [];
  let loaded = null;
  ws.onmessage = event => {
    const msg = JSON.parse(event.data);
    if (msg.id && pending.has(msg.id)) { pending.get(msg.id)(msg); pending.delete(msg.id); }
    if (msg.method === 'Runtime.exceptionThrown') {
      const d = msg.params.exceptionDetails;
      pageErrors.push((d.exception && d.exception.description) || d.text);
    }
    if (msg.method === 'Page.loadEventFired' && loaded) loaded();
  };
  const send = (method, params = {}) => new Promise(resolve => {
    const id = ++nextId;
    pending.set(id, resolve);
    ws.send(JSON.stringify({ id, method, params }));
  });

  await send('Runtime.enable');
  await send('Page.enable');
  await send('Emulation.setDeviceMetricsOverride', { width: 390, height: 844, deviceScaleFactor: 1, mobile: true });
  const pageLoaded = new Promise(resolve => { loaded = resolve; });
  await send('Page.navigate', { url });
  await pageLoaded;

  const reply = await send('Runtime.evaluate', {
    // Scenarios are object methods, so re-wrap the source in an object literal
    expression: `(async () => { ${PRELUDE} return await ({ ${scenario.toString()} }).${scenarioName}(); })()`,
    awaitPromise: true,
    returnByValue: true,
  });
  await new Promise(r => setTimeout(r, 300)); // let late errors surface
  const failure = reply.result.exceptionDetails;
  clearTimeout(timer);
  cleanUp();
  if (failure) {
    finish(1, { ok: false, detail: (failure.exception && failure.exception.description) || failure.text, pageErrors });
  } else if (pageErrors.length) {
    finish(1, { ok: false, detail: 'page threw errors', pageErrors });
  } else {
    finish(0, { ok: true, detail: reply.result.result.value });
  }
} catch (err) {
  clearTimeout(timer);
  cleanUp();
  finish(1, { ok: false, detail: String(err && err.stack || err) });
}

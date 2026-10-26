// Sleep and Fitness tabs: Garmin sync and rendering.

const HEALTH_DAYS = 14;
const healthCache = new Map(); // 'sleep' | 'fitness' -> response
let syncInFlight = null;

function syncGarmin(force = false) {
  if (syncInFlight) return syncInFlight;
  syncInFlight = (async () => {
    try {
      const params = new URLSearchParams({ today: todayISO() });
      if (force) params.set('force', 'true');
      const r = await api(`/sync?${params}`, { method: 'POST' });
      if (!r.ok) return { status: 'error', error: await errorMessage(r) };
      const result = await r.json();
      if (result.status === 'ok') {
        // New watch data can change the analysis and the calorie target
        healthCache.clear();
        summaryCache.clear();
        await refreshProfile();
        for (const kind of ['sleep', 'fitness']) {
          if (!views[kind].hidden) loadHealthView(kind);
        }
      }
      return result;
    } catch (e) {
      return { status: 'error', error: e.message };
    } finally {
      syncInFlight = null;
    }
  })();
  return syncInFlight;
}

async function loadHealthView(kind) {
  const content = document.getElementById(`${kind}-content`);
  if (!healthCache.has(kind)) content.innerHTML = '<div class="summary-loading pulsing">Loading…</div>';
  try {
    const params = new URLSearchParams({ days: HEALTH_DAYS, today: todayISO() });
    const r = await api(`/${kind}?${params}`);
    if (!r.ok) throw new Error(await errorMessage(r));
    const data = await r.json();
    healthCache.set(kind, data);
    if (!views[kind].hidden) renderHealthView(kind, data);
  } catch (err) {
    content.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
  }
}

function renderHealthView(kind, data) {
  renderSyncLine(kind, data);
  const content = document.getElementById(`${kind}-content`);
  const hasData = kind === 'sleep' ? data.nights.length > 0 : (data.days.length > 0 || data.activities.length > 0);
  if (!data.configured) {
    content.innerHTML = `<div class="summary-empty">Garmin isn't connected yet. Add your Personal API key to Render
      as <strong>GARMIN_PERSONAL_API_KEY</strong>, then reopen the app.</div>`;
  } else if (!hasData) {
    content.innerHTML = `<div class="summary-empty">${kind === 'sleep'
      ? 'No sleep recorded in the last 14 nights. Wear your watch overnight and nights appear here once it syncs to Garmin Connect.'
      : 'No activity recorded in the last 14 days. Wear your watch and it appears here once it syncs to Garmin Connect.'}</div>`;
  } else {
    content.innerHTML = kind === 'sleep' ? sleepHtml(data) : fitnessHtml(data);
  }
}

function renderSyncLine(kind, data) {
  const line = document.getElementById(`${kind}-sync`);
  if (!data.configured) { line.innerHTML = ''; return; }
  const when = data.last_success_at ? `synced ${relativeTime(data.last_success_at)}` : 'not synced yet';
  line.innerHTML = `
    <span>Garmin · ${when}${data.last_error ? ' · last sync failed' : ''}</span>
    <button class="link-btn" data-sync-now>Sync now</button>`;
  const btn = line.querySelector('[data-sync-now]');
  btn.addEventListener('click', async () => {
    btn.disabled = true;
    btn.textContent = 'Syncing…';
    const result = await syncGarmin(true);
    await loadHealthView(kind);
    if (result && result.status === 'error') {
      document.getElementById(`${kind}-content`).insertAdjacentHTML('afterbegin',
        `<div class="error">${escapeHtml(result.error || 'Sync failed')}</div>`);
    }
  });
}

function relativeTime(iso) {
  const minutes = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (minutes < 1) return 'just now';
  if (minutes < 60) return `${minutes} min ago`;
  const hours = Math.round(minutes / 60);
  if (hours < 24) return `${hours} hr ago`;
  return `${Math.round(hours / 24)} days ago`;
}

function dateRange(start, end) {
  const out = [];
  for (let d = start; d <= end; d = shiftDate(d, 1)) out.push(d);
  return out;
}

function fmtDuration(seconds) {
  if (!seconds) return '—';
  const minutes = Math.round(seconds / 60);
  if (minutes < 60) return `${minutes}m`;
  return `${Math.floor(minutes / 60)}h ${String(minutes % 60).padStart(2, '0')}m`;
}

function average(values) {
  const v = values.filter(x => x !== null && x !== undefined);
  return v.length ? v.reduce((s, x) => s + x, 0) / v.length : null;
}

function statHtml(label, value, sub = '') {
  return `<div class="stat"><div class="stat-label">${label}</div>
    <div class="stat-value">${value}</div>${sub ? `<div class="stat-sub">${sub}</div>` : ''}</div>`;
}

const STAGES = [
  { key: 'deep_seconds', cls: 'stage-deep', label: 'Deep' },
  { key: 'light_seconds', cls: 'stage-light', label: 'Light' },
  { key: 'rem_seconds', cls: 'stage-rem', label: 'REM' },
  { key: 'awake_seconds', cls: 'stage-awake', label: 'Awake' },
];

function hasStages(n) {
  return STAGES.some(s => n[s.key] !== null && n[s.key] !== undefined);
}

function sleepHtml(data) {
  const nights = data.nights;
  const avgSleep = average(nights.map(n => n.sleep_seconds));
  const avgScore = average(nights.map(n => n.sleep_score));
  const avgHrv = average(nights.map(n => n.hrv_ms));
  const avgRhr = average(nights.map(n => n.resting_hr));
  const plural = nights.length === 1 ? 'night' : 'nights';

  const stats = `
    <div class="stat-grid">
      ${statHtml('Avg sleep', fmtDuration(avgSleep), `${nights.length} ${plural} recorded`)}
      ${statHtml('Sleep score', avgScore !== null ? Math.round(avgScore) : '—', 'average')}
      ${statHtml('Overnight HRV', avgHrv !== null ? `${Math.round(avgHrv)} ms` : '—', 'average')}
      ${statHtml('Resting HR', avgRhr !== null ? `${Math.round(avgRhr)} bpm` : '—', 'average')}
    </div>`;

  const rows = [...nights].reverse().map(n => {
    const total = STAGES.reduce((s, st) => s + (n[st.key] || 0), 0);
    const bar = hasStages(n) && total
      ? `<div class="stage-bar">${STAGES.map(st => n[st.key]
          ? `<div class="${st.cls}" style="width: ${(n[st.key] / total) * 100}%"></div>` : '').join('')}</div>`
      : '';
    const times = n.bedtime_local && n.wake_local ? `${n.bedtime_local.slice(11)} – ${n.wake_local.slice(11)}` : '';
    const extras = [times, n.hrv_ms !== null && n.hrv_ms !== undefined ? `HRV ${Math.round(n.hrv_ms)} ms` : '']
      .filter(Boolean).join(' · ');
    return `
      <div class="list-row">
        <div>
          <div class="list-row-main">${formatDateLabel(n.night_date)}</div>
          ${extras ? `<div class="list-row-sub">${extras}</div>` : ''}
          ${bar}
        </div>
        <div class="list-row-value">${fmtDuration(n.sleep_seconds)}
          ${n.sleep_score !== null && n.sleep_score !== undefined ? `<div class="list-row-sub">score ${Math.round(n.sleep_score)}</div>` : ''}
        </div>
      </div>`;
  }).join('');

  return `
    <div class="summary-section">${stats}</div>
    <div class="summary-section">
      <div class="summary-section-header"><span>Sleep per night</span></div>
      ${sleepChartHtml(data)}
    </div>
    <div class="summary-section">
      <div class="summary-section-header"><span>Nights</span></div>
      <div class="list-card">${rows}</div>
    </div>`;
}

function sleepChartHtml(data) {
  const dates = dateRange(data.start, data.end);
  const byDate = Object.fromEntries(data.nights.map(n => [n.night_date, n]));
  const W = 320, H = 120, padTop = 6, padBottom = 2;
  const slot = W / dates.length;
  const barW = Math.max(4, slot * 0.62);
  const maxS = Math.max(10 * 3600, ...data.nights.map(n => n.sleep_seconds || 0)) * 1.05;
  const h = s => (s / maxS) * (H - padTop - padBottom);
  const base = H - padBottom;

  const bars = dates.map((d, i) => {
    const n = byDate[d];
    if (!n) return '';
    const x = (i * slot + (slot - barW) / 2).toFixed(1);
    if (!hasStages(n)) {
      return `<rect x="${x}" y="${(base - h(n.sleep_seconds)).toFixed(1)}" width="${barW.toFixed(1)}" height="${h(n.sleep_seconds).toFixed(1)}" fill="var(--sleep-light)" rx="1.5" />`;
    }
    let y = base;
    return STAGES.map(st => {
      const secs = n[st.key] || 0;
      if (!secs) return '';
      y -= h(secs);
      return `<rect x="${x}" y="${y.toFixed(1)}" width="${barW.toFixed(1)}" height="${h(secs).toFixed(1)}" fill="var(--${st.cls.replace('stage', 'sleep')})" />`;
    }).join('');
  }).join('');

  const eightY = (base - h(8 * 3600)).toFixed(1);
  return `
    <div class="chart-card">
      <svg class="chart-svg" viewBox="0 0 ${W} ${H}">
        <line x1="0" x2="${W}" y1="${eightY}" y2="${eightY}" stroke="var(--text-faint)" stroke-opacity="0.6" stroke-width="1" stroke-dasharray="3 3" />
        <text x="${W - 2}" y="${(eightY - 3)}" text-anchor="end" font-size="9" fill="var(--text-faint)">8h</text>
        ${bars}
      </svg>
      <div class="chart-x-labels">
        <span>${formatShortDate(data.start)}</span>
        <span>${formatShortDate(data.end)}</span>
      </div>
      <div class="chart-legend">
        ${STAGES.map(st => `<span><i class="lg-dot ${st.cls}"></i>${st.label}</span>`).join('')}
        <span>Gaps: not recorded</span>
      </div>
    </div>`;
}

function fitnessHtml(data) {
  const worn = data.days.filter(d => (d.steps || 0) > 0);
  const avgSteps = average(worn.map(d => d.steps));
  const avgActive = average(worn.map(d => d.active_kcal));
  const totalLoad = data.activities.reduce((s, a) => s + (a.training_load || 0), 0);
  const t = profileData && profileData.targets;

  const stats = `
    <div class="stat-grid">
      ${statHtml('Steps', avgSteps !== null ? fmt(avgSteps) : '—', 'daily average')}
      ${statHtml('Active calories', avgActive !== null ? fmt(avgActive) : '—', 'daily average')}
      ${statHtml('Workouts', data.activities.length, 'last 14 days')}
      ${statHtml('Training load', totalLoad ? fmt(totalLoad) : '—', 'last 14 days')}
    </div>`;

  const targetNote = t && t.basis === 'garmin'
    ? `<p class="target-note">Your calorie target (~${fmt(t.calories)}) now uses your 7-day average of
       ~${fmt(t.active_kcal_avg)} active calories, so it rises on active weeks and falls on quiet ones.</p>`
    : (t ? `<p class="target-note">Your calorie target switches to your measured activity once your watch
       has recorded at least 3 of the last 7 days.</p>` : '');

  const rows = [...data.activities].reverse().map(a => {
    const name = a.name || (a.activity_type || 'Workout').replace(/_/g, ' ');
    const sub = [
      formatDateLabel(a.local_date),
      a.duration_s ? fmtDuration(a.duration_s) : '',
      a.distance_m ? `${(a.distance_m / 1000).toFixed(1)} km` : '',
      a.avg_hr ? `avg ${Math.round(a.avg_hr)} bpm` : '',
    ].filter(Boolean).join(' · ');
    return `
      <div class="list-row">
        <div>
          <div class="list-row-main">${escapeHtml(name)}</div>
          <div class="list-row-sub">${sub}</div>
        </div>
        <div class="list-row-value">${a.calories ? `${fmt(a.calories)} cal` : ''}
          ${a.training_load ? `<div class="list-row-sub">load ${Math.round(a.training_load)}</div>` : ''}
        </div>
      </div>`;
  }).join('');

  return `
    <div class="summary-section">${stats}</div>
    ${targetNote}
    <div class="summary-section">
      <div class="summary-section-header"><span>Active calories per day</span></div>
      ${activeChartHtml(data)}
    </div>
    <div class="summary-section">
      <div class="summary-section-header"><span>Workouts</span></div>
      ${rows ? `<div class="list-card">${rows}</div>` : '<div class="summary-empty">No workouts recorded.</div>'}
    </div>`;
}

function activeChartHtml(data) {
  const dates = dateRange(data.start, data.end);
  const byDate = Object.fromEntries(data.days.map(d => [d.local_date, d]));
  const W = 320, H = 100, padTop = 6, padBottom = 2;
  const slot = W / dates.length;
  const barW = Math.max(4, slot * 0.62);
  const maxK = Math.max(300, ...data.days.map(d => d.active_kcal || 0)) * 1.05;
  const h = k => (k / maxK) * (H - padTop - padBottom);
  const bars = dates.map((d, i) => {
    const k = byDate[d] && byDate[d].active_kcal;
    if (!k) return '';
    const x = (i * slot + (slot - barW) / 2).toFixed(1);
    return `<rect x="${x}" y="${(H - padBottom - h(k)).toFixed(1)}" width="${barW.toFixed(1)}" height="${h(k).toFixed(1)}" fill="var(--accent)" rx="1.5" />`;
  }).join('');
  return `
    <div class="chart-card">
      <svg class="chart-svg" viewBox="0 0 ${W} ${H}">${bars}</svg>
      <div class="chart-x-labels">
        <span>${formatShortDate(data.start)}</span>
        <span>${formatShortDate(data.end)}</span>
      </div>
    </div>`;
}

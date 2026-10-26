// Summary tab: stats, Claude's analysis, charts, logging calendar.

let currentPeriodDays = 7;
const summaryCache = new Map(); // period days -> SummaryResponse

document.querySelectorAll('.tab').forEach(t => {
  t.addEventListener('click', () => {
    const tab = t.dataset.tab;
    if (tab === 'day') {
      showView('day');
    } else if (tab === 'summary') {
      showView('summary');
      if (!summaryCache.has(currentPeriodDays)) loadSummary();
    } else if (tab === 'sleep' || tab === 'fitness') {
      showView(tab);
      if (healthCache.has(tab)) renderHealthView(tab, healthCache.get(tab)); else loadHealthView(tab);
    }
  });
});

document.querySelectorAll('.period-btn').forEach(b => {
  b.addEventListener('click', () => {
    if (parseInt(b.dataset.days, 10) === currentPeriodDays) return;
    document.querySelectorAll('.period-btn').forEach(x => x.classList.remove('active'));
    b.classList.add('active');
    currentPeriodDays = parseInt(b.dataset.days, 10);
    const cached = summaryCache.get(currentPeriodDays);
    if (cached) renderSummary(cached); else loadSummary();
  });
});

async function loadSummary(refresh = false) {
  const days = currentPeriodDays;
  summaryContent.innerHTML = `
    <div class="summary-loading pulsing">
      Analysing your last ${days} days…<br>
      <span class="summary-loading-sub">(up to 30s when there's new data)</span>
    </div>`;
  try {
    const params = new URLSearchParams({ days, today: todayISO() });
    if (refresh) params.set('refresh', 'true');
    const r = await api(`/summary?${params}`);
    if (!r.ok) throw new Error(`Server error ${r.status}: ${await r.text()}`);
    const data = await r.json();
    // Don't keep a failed analysis client-side, so reopening the tab retries
    if (data.analysis) summaryCache.set(days, data);
    // The user may have switched period while this loaded
    if (days === currentPeriodDays) renderSummary(data);
  } catch (err) {
    if (days !== currentPeriodDays) return;
    summaryContent.innerHTML = `
      <div class="error">${escapeHtml(err.message)}</div>
      <button class="secondary-btn" id="retry-summary">Retry</button>`;
    const retry = document.getElementById('retry-summary');
    if (retry) retry.addEventListener('click', () => loadSummary());
  }
}

function isFullDay(d) {
  return MAIN_MEALS.every(t => d.meal_types.includes(t));
}

function meterHtml(label, value, unit, target, band) {
  const max = Math.max(value, band ? band[1] : target) * 1.15;
  const pct = v => Math.min(100, (v / max) * 100);
  return `
    <div class="meter">
      <div class="meter-head">
        <span>${label}</span>
        <span><strong>${fmt(value)}${unit}</strong> / ${fmt(target)}${unit} target</span>
      </div>
      <div class="meter-track">
        ${band ? `<div class="meter-band" style="left: ${pct(band[0])}%; width: ${pct(band[1]) - pct(band[0])}%"></div>` : ''}
        <div class="meter-fill" style="width: ${pct(value)}%"></div>
        <div class="meter-target" style="left: ${pct(target)}%"></div>
      </div>
    </div>`;
}

function quickNumbersHtml(data) {
  const q = data.quick_numbers;
  const t = data.targets;
  const plural = (n, word) => `${n} ${word}${n === 1 ? '' : 's'}`;

  let body;
  if (q.avg_basis === 'none') {
    body = `<div class="summary-quick-main">Nothing logged in this period.</div>`;
  } else {
    const basisNote = q.avg_basis === 'full'
      ? `Daily averages from ${plural(q.full_days, 'fully logged day')} (breakfast, lunch and dinner).`
      : `No fully logged days, so averages come from ${plural(q.days_logged, 'partly logged day')} and are likely low.`;
    const hasMacros = q.avg_protein_g !== null && q.avg_protein_g !== undefined;

    const meters = t
      ? meterHtml('Calories', q.avg_calories_per_day, '', t.calories, [t.calories_low, t.calories_high])
        + (hasMacros ? meterHtml('Protein', q.avg_protein_g, 'g', t.protein_g, null) : '')
      : '';
    const plainNumbers = t ? '' : `
      <div class="summary-quick-main">~${fmt(q.avg_calories_per_day)} cal/day</div>
      ${hasMacros ? `<div class="summary-quick-macros"><span>P <strong>${fmt(q.avg_protein_g)}g</strong></span></div>` : ''}`;
    const otherMacros = hasMacros
      ? `<div class="summary-quick-macros">
           <span>C <strong>${fmt(q.avg_carbs_g)}g</strong></span>
           <span>F <strong>${fmt(q.avg_fat_g)}g</strong></span>
         </div>`
      : '';

    body = `
      <div class="summary-quick-main">
        ${q.days_logged}/${q.days_in_period} days logged · ${q.full_days} complete · ${plural(q.total_meals, 'meal')}
      </div>
      ${meters}${plainNumbers}${otherMacros}
      <div class="summary-note">${basisNote}</div>`;
  }

  const targetsLine = t
    ? `<div class="targets-line">
         <span>Targets ~${fmt(t.calories)} cal · ${t.protein_g}g protein${t.basis === 'garmin' ? ' · calories from Garmin activity' : ''}</span>
         <button class="link-btn" data-edit-targets>Edit</button>
       </div>`
    : `<div class="targets-line">
         <span>No targets set yet.</span>
         <button class="link-btn" data-edit-targets>Set targets</button>
       </div>`;

  return `<div class="summary-quick">${body}${targetsLine}</div>`;
}

function calorieChartHtml(data) {
  const totals = data.daily_totals;
  const t = data.targets;
  const W = 320, H = 110, padX = 6, padTop = 8, padBottom = 6;
  const n = totals.length;
  const maxY = Math.max(...totals.map(d => d.calories), t ? t.calories_high : 0, 1) * 1.1;
  const x = i => padX + (n === 1 ? (W - 2 * padX) / 2 : i * (W - 2 * padX) / (n - 1));
  const y = v => H - padBottom - (v / maxY) * (H - padTop - padBottom);

  // Break the line wherever a day has nothing logged
  const segments = [];
  let current = [];
  totals.forEach((d, i) => {
    if (d.meal_count > 0) {
      current.push(`${x(i).toFixed(1)},${y(d.calories).toFixed(1)}`);
    } else if (current.length) {
      segments.push(current);
      current = [];
    }
  });
  if (current.length) segments.push(current);

  const lines = segments.filter(s => s.length > 1).map(s =>
    `<polyline points="${s.join(' ')}" fill="none" stroke="var(--accent)" stroke-width="2" stroke-linejoin="round" stroke-linecap="round" />`
  ).join('');

  const r = n > 30 ? 2 : 3;
  const points = totals.map((d, i) => {
    if (d.meal_count === 0) return '';
    const cx = x(i).toFixed(1), cy = y(d.calories).toFixed(1);
    return isFullDay(d)
      ? `<circle cx="${cx}" cy="${cy}" r="${r}" fill="var(--accent)" />`
      : `<circle cx="${cx}" cy="${cy}" r="${r}" fill="var(--surface)" stroke="var(--accent)" stroke-width="1.5" />`;
  }).join('');

  const band = t ? `
    <rect x="0" y="${y(t.calories_high).toFixed(1)}" width="${W}" height="${(y(t.calories_low) - y(t.calories_high)).toFixed(1)}" fill="var(--accent-soft)" />
    <line x1="0" x2="${W}" y1="${y(t.calories).toFixed(1)}" y2="${y(t.calories).toFixed(1)}" stroke="var(--accent)" stroke-opacity="0.5" stroke-width="1" stroke-dasharray="3 3" />` : '';

  return `
    <div class="chart-card">
      <svg class="chart-svg" viewBox="0 0 ${W} ${H}">${band}${lines}${points}</svg>
      <div class="chart-x-labels">
        <span>${formatShortDate(data.period_start)}</span>
        <span>${formatShortDate(data.period_end)}</span>
      </div>
      <div class="chart-legend">
        <span><i class="lg-dot solid"></i>Full day</span>
        <span><i class="lg-dot hollow"></i>Partial day</span>
        ${t ? '<span><i class="lg-band"></i>Target range</span>' : ''}
        <span>Gaps: nothing logged</span>
      </div>
    </div>`;
}

function loggingCalendarHtml(data) {
  // Monday-first grid, padded so the first day lands under its weekday
  const first = new Date(data.period_start + 'T00:00:00');
  const lead = (first.getDay() + 6) % 7;
  const today = todayISO();
  const cells = Array(lead).fill('<div class="cal-cell"></div>');
  for (const d of data.daily_totals) {
    const status = dayStatus(d.date, { count: d.meal_count, mealTypes: new Set(d.meal_types) }, today);
    const dayNum = new Date(d.date + 'T00:00:00').getDate();
    cells.push(`
      <div class="cal-cell" title="${formatShortDate(d.date)}">
        <span class="cal-num">${dayNum}</span>
        <span class="cal-dot status-dot ${status}"></span>
      </div>`);
  }
  const heads = ['M', 'T', 'W', 'T', 'F', 'S', 'S'].map(h => `<div class="cal-head">${h}</div>`).join('');
  return `
    <div class="chart-card">
      <div class="cal-grid">${heads}${cells.join('')}</div>
      <div class="chart-legend">
        <span><i class="lg-dot status-dot empty"></i>Nothing</span>
        <span><i class="lg-dot status-dot partial"></i>Some meals</span>
        <span><i class="lg-dot status-dot full"></i>Breakfast, lunch and dinner</span>
      </div>
    </div>`;
}

function summaryAnalysisHtml(a, error) {
  // The stats below still render when Claude's analysis failed
  if (!a) {
    return `
      <div class="summary-section">
        <div class="summary-section-header">
          <span>What I'm seeing</span>
          <button class="refresh-btn" id="refresh-summary">↻ Retry</button>
        </div>
        <div class="error">${escapeHtml(error || 'The analysis is unavailable right now.')}</div>
      </div>`;
  }
  return `
    <div class="summary-section">
      <div class="summary-section-header">
        <span>What I'm seeing</span>
        <button class="refresh-btn" id="refresh-summary">↻ Refresh</button>
      </div>
      ${a.observations.length === 0
        ? `<div class="summary-empty">No observations — log a few more meals.</div>`
        : `<div class="bullet-card"><ul>${a.observations.map(o => `<li>${escapeHtml(o)}</li>`).join('')}</ul></div>`}
    </div>

    <div class="summary-section">
      <div class="summary-section-header"><span>Try this week</span></div>
      ${a.suggestions.length === 0
        ? `<div class="summary-empty">No suggestions yet.</div>`
        : `<div class="bullet-card"><ul>${a.suggestions.map(s => `<li>${escapeHtml(s)}</li>`).join('')}</ul></div>`}
    </div>`;
}

function renderSummary(data) {
  summarySubtitle.textContent =
    `${formatShortDate(data.period_start)} – ${formatShortDate(data.period_end)}. Today is added once it's over.`;

  summaryContent.innerHTML = `
    <div class="summary-section">
      <div class="summary-section-header"><span>Quick numbers</span></div>
      ${quickNumbersHtml(data)}
    </div>

    ${summaryAnalysisHtml(data.analysis, data.analysis_error)}

    <div class="summary-section">
      <div class="summary-section-header"><span>Daily calories</span></div>
      ${calorieChartHtml(data)}
    </div>

    <div class="summary-section">
      <div class="summary-section-header"><span>Logging</span></div>
      ${loggingCalendarHtml(data)}
    </div>
  `;

  document.getElementById('refresh-summary').addEventListener('click', () => {
    summaryCache.delete(currentPeriodDays);
    loadSummary(true);
  });
  summaryContent.querySelectorAll('[data-edit-targets]').forEach(b =>
    b.addEventListener('click', () => openSetup('summary'))
  );
}

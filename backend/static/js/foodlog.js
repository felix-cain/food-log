// Food log: week strip, day view, swipe between days.

function getWeekWindow(selectedDate) {
  // 7-day window centred on `selectedDate`, so today sits in the middle on
  // load with the next 3 (future) days shown greyed out.
  const start = shiftDate(selectedDate, -3);
  const days = [];
  for (let i = 0; i < 7; i++) days.push(shiftDate(start, i));
  return days;
}

function summariseMeals(meals) {
  return {
    count: meals.reduce((s, m) => s + m.items.length, 0),
    calories: meals.reduce((s, m) => s + m.items.reduce((s2, i) => s2 + i.calories, 0), 0),
    mealTypes: new Set(meals.map(m => m.meal_type)),
  };
}

function dayStatus(date, summary, today) {
  if (date > today) return 'future';
  if (summary.count === 0) return date === today ? 'future' : 'empty';
  const full = MAIN_MEALS.every(t => summary.mealTypes.has(t));
  return full ? 'full' : 'partial';
}

function setDot(date, summary) {
  const dot = weekStripEl.querySelector(`[data-dot="${date}"]`);
  if (!dot) return;
  dot.classList.remove('empty', 'partial', 'full');
  const status = dayStatus(date, summary, todayISO());
  if (status !== 'future') dot.classList.add(status);
}

async function fetchDaySummary(date) {
  if (daySummaryCache.has(date)) return daySummaryCache.get(date);
  try {
    const r = await api(`/entries?date=${date}`);
    if (!r.ok) throw new Error('non-ok');
    const summary = summariseMeals(await r.json());
    daySummaryCache.set(date, summary);
    return summary;
  } catch (e) {
    return { count: 0, calories: 0, mealTypes: new Set() };
  }
}

async function renderWeekStrip() {
  const today = todayISO();
  const days = getWeekWindow(currentDate);

  weekStripEl.innerHTML = days.map(d => {
    const dateObj = new Date(d + 'T00:00:00');
    const name = dateObj.toLocaleDateString(undefined, { weekday: 'short' }).slice(0, 3);
    const num = dateObj.getDate();
    const classes = ['week-day'];
    if (d === currentDate) classes.push('selected');
    if (d === today) classes.push('today');
    if (d > today) classes.push('future');
    return `
      <button class="${classes.join(' ')}" data-date="${d}"${d > today ? ' disabled' : ''}>
        <div class="week-day-name">${name}</div>
        <div class="week-day-num">${num}</div>
        <div class="week-day-dot status-dot" data-dot="${d}"></div>
      </button>
    `;
  }).join('');

  await Promise.all(days.filter(d => d <= today).map(async d => {
    setDot(d, await fetchDaySummary(d));
  }));
}

function goToDate(date) {
  if (date > todayISO() || date === currentDate) return;
  currentDate = date;
  loadDay();
}

async function loadDay() {
  const today = todayISO();
  const dateObj = new Date(currentDate + 'T00:00:00');
  monthLabel.textContent = dateObj.toLocaleDateString(undefined, { month: 'long', year: 'numeric' });
  todayBtn.hidden = currentDate === today;
  // Saves go to the selected day, so say so when it isn't today
  logTarget.hidden = currentDate === today;
  logTarget.textContent = `Saving to ${currentDate === shiftDate(today, -1) ? 'yesterday' : formatDateLabel(currentDate)}`;

  // Render strip in parallel with the day's meal data
  renderWeekStrip();

  const requestedDate = currentDate;
  let meals = [];
  try {
    const response = await api(`/entries?date=${requestedDate}`);
    if (response.ok) meals = await response.json();
  } catch (e) { /* show empty */ }
  // A quick swipe may have moved on to another day while this was in flight
  if (requestedDate !== currentDate) return;

  dayMeals = meals;
  renderDayTotals();

  // Update cache so the week-strip dot reflects fresh data after save/delete
  const freshSummary = summariseMeals(meals);
  daySummaryCache.set(currentDate, freshSummary);

  const byType = Object.fromEntries(MEAL_TYPES.map(t => [t.key, []]));
  for (const meal of meals) byType[meal.meal_type].push(meal);

  mealSections.innerHTML = MEAL_TYPES.map(t => {
    const sectionMeals = byType[t.key];
    const sectionCal = sectionMeals.reduce(
      (s, m) => s + m.items.reduce((s2, i) => s2 + i.calories, 0), 0
    );
    return `
      <div class="meal-section">
        <div class="meal-header">
          <span>${t.label}</span>
          ${sectionCal > 0 ? `<span class="meal-header-cal">${fmt(sectionCal)} cal</span>` : ''}
        </div>
        ${sectionMeals.length === 0
          ? emptySlotHtml(t)
          : sectionMeals.map(renderMealEntry).join('')}
      </div>
    `;
  }).join('');

  // Refresh the strip's dot for the current day in case its status changed
  setDot(currentDate, freshSummary);
}

function renderDayTotals() {
  const items = dayMeals.flatMap(m => m.items);
  if (items.length === 0) {
    dayTotals.textContent = 'Nothing logged yet';
    return;
  }
  const cal = items.reduce((s, i) => s + i.calories, 0);
  const targets = profileData && profileData.targets;
  const calText = targets ? `~${fmt(cal)} of ${fmt(targets.calories)} cal` : `~${fmt(cal)} cal`;
  const m = sumMacros(items);
  const protein = m && (targets ? `P ${m.p} of ${targets.protein_g}g` : `P ${m.p}g`);
  dayTotals.innerHTML = `
    <div>${calText} · ${items.length} item${items.length === 1 ? '' : 's'}</div>
    ${m ? `<div class="day-totals-macros">${protein} · C ${m.c}g · F ${m.f}g</div>` : ''}
  `;
}

function renderMealEntry(meal) {
  const mealCal = meal.items.reduce((s, i) => s + i.calories, 0);
  const itemList = meal.items.map(i => i.name).join(', ');
  const m = sumMacros(meal.items);
  return `
    <button class="meal-entry" data-entry-id="${meal.id}">
      <div class="meal-entry-header">
        <div class="meal-entry-desc">${escapeHtml(meal.description)}</div>
        <div class="meal-entry-cal">${fmt(mealCal)} cal<span class="chev">›</span></div>
      </div>
      <div class="meal-entry-items">${escapeHtml(itemList)}</div>
      ${m ? `<div class="meal-entry-macros">${macroText(m)}</div>` : ''}
    </button>
  `;
}

weekStripEl.addEventListener('click', (e) => {
  const el = e.target.closest('[data-date]');
  if (el && !el.disabled) goToDate(el.dataset.date);
});

// An empty slot offers that meal type's favourites as one-tap chips
const MAX_FAVOURITE_CHIPS = 3;

function emptySlotHtml(mealType) {
  const chips = favourites
    .filter(f => f.meal_type === mealType.key)
    .slice(0, MAX_FAVOURITE_CHIPS)
    .map(f => `<button class="fav-chip" data-fav-id="${f.id}">+ ${escapeHtml(f.name)}
      <span class="fav-chip-cal">${fmt(f.calories)} cal</span></button>`)
    .join('');
  return `<div class="empty-slot">No ${mealType.label.toLowerCase()} logged
    ${chips ? `<div class="fav-chips">${chips}</div>` : ''}</div>`;
}

async function loadFavourites() {
  try {
    const r = await api('/favourites');
    if (r.ok) favourites = await r.json();
  } catch (e) { /* chips just won't show */ }
}

async function logFavourite(chip) {
  chip.disabled = true;
  try {
    const r = await api(`/favourites/${chip.dataset.favId}/log`, {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ date: currentDate }),
    });
    if (!r.ok) throw new Error(await errorMessage(r));
    summaryCache.clear();
    await loadFavourites(); // use counts change the order
    await loadDay();
  } catch (err) {
    chip.disabled = false;
    chip.insertAdjacentHTML('afterend', `<div class="error">${escapeHtml(err.message)}</div>`);
  }
}

mealSections.addEventListener('click', (e) => {
  const chip = e.target.closest('[data-fav-id]');
  if (chip) {
    if (!chip.disabled) logFavourite(chip);
    return;
  }
  const el = e.target.closest('[data-entry-id]');
  if (!el) return;
  const meal = dayMeals.find(m => String(m.id) === el.dataset.entryId);
  if (meal) openEntry(meal);
});

todayBtn.addEventListener('click', () => goToDate(todayISO()));

// Swipe left/right on the day view to move between days
let touchStart = null;
views.day.addEventListener('touchstart', (e) => {
  const t = e.touches[0];
  touchStart = { x: t.clientX, y: t.clientY };
}, { passive: true });
views.day.addEventListener('touchend', (e) => {
  if (!touchStart) return;
  const t = e.changedTouches[0];
  const dx = t.clientX - touchStart.x;
  const dy = t.clientY - touchStart.y;
  touchStart = null;
  if (Math.abs(dx) < 60 || Math.abs(dx) < Math.abs(dy) * 1.5) return;
  goToDate(shiftDate(currentDate, dx < 0 ? 1 : -1));
});

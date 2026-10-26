// Entry detail: view, correct or delete a saved meal.

function openEntry(meal) {
  currentEntry = meal;
  entryRevision = null;
  entryRevised.innerHTML = '';
  entryNoteInput.value = '';
  const typeLabel = { breakfast: 'Breakfast', lunch: 'Lunch', dinner: 'Dinner', snack: 'Snack' }[meal.meal_type];
  entryTitle.textContent = `${typeLabel} · ${formatDateLabel(meal.date)}`;

  entryDetail.innerHTML = analysisHtml(entryAsAnalysis(meal)) + (meal.note
    ? `<div class="entry-note"><strong>Your notes:</strong> ${escapeHtml(meal.note)}</div>`
    : '');
  entryFav.disabled = false;
  entryFav.textContent = currentFavourite() ? 'Remove from favourites' : 'Save as favourite';
  showView('entry');
}

// The favourite this meal was logged from or starred as, if it still exists
function currentFavourite() {
  return currentEntry && favourites.find(f => f.id === currentEntry.favourite_id);
}

entryFav.addEventListener('click', async () => {
  if (!currentEntry) return;
  const id = currentEntry.id;
  const existing = currentFavourite();
  entryFav.disabled = true;
  try {
    const r = existing
      ? await api(`/favourites/${existing.id}`, { method: 'DELETE' })
      : await api('/favourites', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ meal_id: id }),
        });
    if (!r.ok) throw new Error(await errorMessage(r));
    await loadFavourites();
    await loadDay();
    const updated = dayMeals.find(m => m.id === id);
    if (updated) openEntry(updated);
  } catch (err) {
    entryFav.disabled = false;
    entryRevised.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
  }
});

// Saved meals use DB field names; the analysis endpoints use MealAnalysis's
function entryAsAnalysis(meal) {
  return {
    description: meal.description,
    items: meal.items.map(i => ({
      name: i.name,
      estimated_grams: i.grams,
      calories_estimate: i.calories,
      protein_g: i.protein_g,
      carbs_g: i.carbs_g,
      fat_g: i.fat_g,
      confidence: i.confidence,
    })),
  };
}

function joinNotes(...notes) {
  return notes.filter(Boolean).join('; ') || null;
}

entryBack.addEventListener('click', () => {
  currentEntry = null;
  showView('day');
});

entryNoteSubmit.addEventListener('click', async () => {
  const note = entryNoteInput.value.trim();
  if (!currentEntry || !note) return;
  entryNoteSubmit.disabled = true;
  entryRevised.innerHTML = '<div class="status pulsing">Updating with Claude (15–30s)…</div>';
  const formData = new FormData();
  formData.append('note', note);
  formData.append('previous', JSON.stringify(entryAsAnalysis(currentEntry)));
  try {
    const r = await api('/analyze', { method: 'POST', body: formData });
    if (!r.ok) throw new Error(await errorMessage(r));
    entryRevision = { analysis: await r.json(), note };
    entryRevised.innerHTML = `
      <div class="revised-heading">Revised estimate</div>
      ${analysisHtml(entryRevision.analysis)}
      <button class="primary-btn" id="entry-save" style="margin-top: 16px;">Save changes</button>
      <button class="secondary-btn" id="entry-discard">Discard</button>`;
    document.getElementById('entry-save').addEventListener('click', saveEntryRevision);
    document.getElementById('entry-discard').addEventListener('click', () => {
      entryRevision = null;
      entryRevised.innerHTML = '';
    });
  } catch (err) {
    entryRevised.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
  } finally {
    entryNoteSubmit.disabled = false;
  }
});

async function saveEntryRevision() {
  if (!currentEntry || !entryRevision) return;
  const id = currentEntry.id;
  try {
    const r = await api(`/entries/${id}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        analysis: entryRevision.analysis,
        note: joinNotes(currentEntry.note, entryRevision.note),
      }),
    });
    if (!r.ok) throw new Error(await errorMessage(r));
    summaryCache.clear();
    await loadDay();
    const updated = dayMeals.find(m => m.id === id);
    if (updated) openEntry(updated); else showView('day');
  } catch (err) {
    entryRevised.insertAdjacentHTML('beforeend', `<div class="error">${escapeHtml(err.message)}</div>`);
  }
}

entryDelete.addEventListener('click', async () => {
  if (!currentEntry || !confirm('Delete this entry?')) return;
  try {
    const r = await api(`/entries/${currentEntry.id}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(`Delete failed: ${await r.text()}`);
    currentEntry = null;
    summaryCache.clear();
    showView('day');
    loadDay();
  } catch (err) {
    entryDetail.insertAdjacentHTML('beforeend', `<div class="error">${escapeHtml(err.message)}</div>`);
  }
});

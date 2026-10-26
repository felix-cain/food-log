// Logging: photo and/or description -> review -> save.

async function errorMessage(response) {
  const text = await response.text();
  try {
    const detail = JSON.parse(text).detail;
    if (typeof detail === 'string') return detail;
  } catch (e) { /* not JSON */ }
  return `Server error ${response.status}: ${text}`;
}

function resetReview() {
  pendingAnalysis = null;
  pendingFile = null;
  pendingNotes = [];
  if (pendingPreviewUrl) { URL.revokeObjectURL(pendingPreviewUrl); pendingPreviewUrl = null; }
  resultEl.innerHTML = '';
  statusEl.textContent = '';
  noteInput.value = '';
  notePanel.hidden = true;
  labelPrompt.hidden = true;
  mealTypeButtons.hidden = true;
  redoBtn.hidden = false;
}

// mode is "describe" (first description, nothing analysed yet) or
// "correct" (an estimate exists and the text adjusts it)
function showNotePanel(mode) {
  const describing = mode === 'describe';
  notePanel.hidden = false;
  noteLabel.textContent = describing ? 'What did you have?' : 'Not quite right? Say what to change';
  noteInput.placeholder = describing
    ? 'e.g. two slices of sourdough with peanut butter and a banana'
    : 'e.g. it was oat milk, and the curry had chickpeas in it';
  noteSubmit.textContent = describing ? 'Analyse' : 'Update estimate';
  noteSubmit.className = describing ? 'primary-btn' : 'secondary-btn';
}

snapBtn.addEventListener('click', () => photoInput.click());

describeBtn.addEventListener('click', () => {
  resetReview();
  showView('review');
  showNotePanel('describe');
  noteInput.focus();
});

cancelBtn.addEventListener('click', () => {
  resetReview();
  showView('day');
});

redoBtn.addEventListener('click', () => {
  resetReview();
  showView('day');
});

photoInput.addEventListener('change', (e) => {
  const file = e.target.files[0];
  photoInput.value = '';
  if (!file) return;
  resetReview();
  pendingFile = file;
  pendingPreviewUrl = URL.createObjectURL(file);
  resultEl.innerHTML = `<img class="preview" src="${pendingPreviewUrl}" alt="meal">`;
  showView('review');
  runAnalysis(null);
});

noteSubmit.addEventListener('click', () => {
  const note = noteInput.value.trim();
  if (note) runAnalysis(note);
});

async function runAnalysis(note) {
  labelPrompt.hidden = true;
  mealTypeButtons.hidden = true;
  noteSubmit.disabled = true;
  resultEl.querySelectorAll('.error').forEach(el => el.remove());
  statusEl.textContent = 'Sending to Claude (15–30s)…';
  statusEl.classList.add('pulsing');

  const formData = new FormData();
  if (pendingFile) formData.append('photo', pendingFile);
  if (note) formData.append('note', note);
  if (note && pendingAnalysis) formData.append('previous', JSON.stringify(pendingAnalysis));

  try {
    const response = await api('/analyze', { method: 'POST', body: formData });
    if (!response.ok) throw new Error(await errorMessage(response));
    pendingAnalysis = await response.json();
    if (note) pendingNotes.push(note);
    noteInput.value = '';
    renderAnalysis(pendingAnalysis);
    showNotePanel('correct');
    labelPrompt.hidden = false;
    mealTypeButtons.hidden = false;
  } catch (err) {
    resultEl.insertAdjacentHTML('beforeend', `<div class="error">${escapeHtml(err.message)}</div>`);
    if (pendingAnalysis) {
      // Keep the earlier estimate usable
      labelPrompt.hidden = false;
      mealTypeButtons.hidden = false;
    } else {
      showNotePanel(pendingFile ? 'correct' : 'describe');
    }
  } finally {
    statusEl.textContent = '';
    statusEl.classList.remove('pulsing');
    noteSubmit.disabled = false;
  }
}

const PROCESSING_LABELS = { whole: 'whole food', processed: 'processed', ultra_processed: 'ultra-processed' };

// e.g. "vegetables · whole food · fibre 4g · broccoli, carrot"; empty for older items
function itemTagsHtml(item) {
  if (!item.food_group) return '';
  const tags = [
    item.food_group.replace(/_/g, ' '),
    PROCESSING_LABELS[item.processing] || item.processing,
    `fibre ${Math.round(item.fibre_g || 0)}g`,
    (item.plants || []).join(', '),
  ].filter(Boolean);
  return `<div class="item-tags">${escapeHtml(tags.join(' · '))}</div>`;
}

// Description, items and totals for a MealAnalysis-shaped object
function analysisHtml(data) {
  const totalCal = data.items.reduce((s, i) => s + i.calories_estimate, 0);
  const totalGrams = data.items.reduce((s, i) => s + i.estimated_grams, 0);
  const m = sumMacros(data.items);

  const itemsHtml = data.items.map(item => `
    <div class="item">
      <div class="item-name">${escapeHtml(item.name)}</div>
      <div class="item-meta">
        <span>${Math.round(item.estimated_grams)}g</span>
        <span>${fmt(item.calories_estimate)} cal</span>
        <span class="pill ${escapeHtml(item.confidence)}">${escapeHtml(item.confidence)}</span>
      </div>
      ${item.protein_g !== null && item.protein_g !== undefined
        ? `<div class="item-macros">P ${Math.round(item.protein_g)}g · C ${Math.round(item.carbs_g || 0)}g · F ${Math.round(item.fat_g || 0)}g</div>`
        : ''}
      ${itemTagsHtml(item)}
    </div>
  `).join('');

  return `
    <div class="description">${escapeHtml(data.description)}</div>
    <div class="items">${itemsHtml}</div>
    <div class="totals">
      <span>${data.items.length} item${data.items.length === 1 ? '' : 's'} · ${Math.round(totalGrams)}g</span>
      <span>~${fmt(totalCal)} cal</span>
    </div>
    ${m ? `<div class="totals-macros">${macroText(m)}</div>` : ''}
  `;
}

function renderAnalysis(data) {
  const preview = pendingPreviewUrl ? `<img class="preview" src="${pendingPreviewUrl}" alt="meal">` : '';
  const notes = pendingNotes.length
    ? `<div class="entry-note"><strong>Your notes:</strong> ${escapeHtml(pendingNotes.join('; '))}</div>`
    : '';
  resultEl.innerHTML = preview + analysisHtml(data) + notes;
}

mealTypeButtons.addEventListener('click', async (e) => {
  const btn = e.target.closest('.meal-type-btn');
  if (!btn || !pendingAnalysis) return;

  const mealType = btn.dataset.type;
  labelPrompt.hidden = true;
  mealTypeButtons.hidden = true;
  notePanel.hidden = true;
  redoBtn.hidden = true;
  statusEl.textContent = 'Saving…';

  try {
    const response = await api('/entries', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        date: currentDate,
        meal_type: mealType,
        analysis: pendingAnalysis,
        note: joinNotes(...pendingNotes),
        source: pendingFile ? 'photo' : 'text',
      }),
    });
    if (!response.ok) throw new Error(`Save failed: ${await errorMessage(response)}`);
    resetReview();
    summaryCache.clear();
    showView('day');
    await loadDay();
  } catch (err) {
    resultEl.insertAdjacentHTML('beforeend', `<div class="error">${escapeHtml(err.message)}</div>`);
    labelPrompt.hidden = false;
    mealTypeButtons.hidden = false;
    notePanel.hidden = false;
    redoBtn.hidden = false;
  } finally {
    statusEl.textContent = '';
  }
});

// Gym classes: screenshots of the class programming -> Claude reads them ->
// add your own notes and a 1-5 rating -> save. Listed on the Fitness tab.

const GYM_MAX_PHOTOS = 4;
const EFFORT_LABELS = { 1: 'Easy', 2: 'Fairly easy', 3: 'Solid', 4: 'Hard', 5: 'Wrecked' };

const gymBack = document.getElementById('gym-back');
const gymTitle = document.getElementById('gym-title');
const gymCapture = document.getElementById('gym-capture');
const gymPick = document.getElementById('gym-pick');
const gymPhotos = document.getElementById('gym-photos');
const gymPhotoCount = document.getElementById('gym-photo-count');
const gymRead = document.getElementById('gym-read');
const gymStatus = document.getElementById('gym-status');
const gymProgrammeEl = document.getElementById('gym-programme');
const gymForm = document.getElementById('gym-form');
const gymClass = document.getElementById('gym-class');
const gymDate = document.getElementById('gym-date');
const gymNotes = document.getElementById('gym-notes');
const gymRatingHint = document.getElementById('gym-rating-hint');
const gymError = document.getElementById('gym-error');
const gymSave = document.getElementById('gym-save');
const gymDelete = document.getElementById('gym-delete');

let gymFiles = [];         // screenshots chosen, not yet read
let gymProgramme = null;   // Claude's reading of them (or the saved session's blocks)
let gymEditing = null;     // saved session being edited, or null when logging a new one
let gymRating = null;      // 1-5, or null when not rated

function setGymRating(value) {
  gymRating = value;
  for (const input of gymForm.querySelectorAll('input[name=effort]')) input.checked = parseInt(input.value, 10) === value;
  gymRatingHint.textContent = value ? `· ${EFFORT_LABELS[value]}` : '';
}

// Tapping the chosen number again clears it, since the rating is optional
for (const input of gymForm.querySelectorAll('input[name=effort]')) {
  input.addEventListener('click', () => {
    const value = parseInt(input.value, 10);
    setGymRating(gymRating === value ? null : value);
  });
}

function gymProgrammeHtml(programme) {
  if (!programme || !programme.blocks.length) return '';
  const blocks = programme.blocks.map(b => {
    const head = [b.title, b.format].filter(Boolean).join(' · ');
    return `
      <div class="gym-block">
        <div class="gym-block-kind">${escapeHtml(b.kind)}</div>
        ${head ? `<div class="gym-block-head">${escapeHtml(head)}</div>` : ''}
        ${b.movements.length ? `<ul>${b.movements.map(m => `<li>${escapeHtml(m)}</li>`).join('')}</ul>` : ''}
        ${b.scheme ? `<div class="gym-block-scheme">${escapeHtml(b.scheme)}</div>` : ''}
      </div>`;
  }).join('');
  return `<div class="description">${escapeHtml(programme.summary || '')}</div>${blocks}`;
}

function resetGym() {
  gymFiles = [];
  gymProgramme = null;
  gymEditing = null;
  gymPhotos.value = '';
  gymPhotoCount.textContent = '';
  gymRead.hidden = true;
  gymRead.disabled = false;
  gymStatus.textContent = '';
  gymStatus.classList.remove('pulsing');
  gymProgrammeEl.innerHTML = '';
  gymError.innerHTML = '';
  gymClass.value = '';
  gymNotes.value = '';
  gymDate.value = todayISO();
  gymSave.disabled = false;
  gymDelete.hidden = true;
  gymCapture.hidden = false;
  setGymRating(null);
}

// session is a saved class from /fitness to edit, or omitted to log a new one
function openGym(session = null) {
  resetGym();
  if (session) {
    gymEditing = session;
    gymProgramme = { summary: session.summary, blocks: session.blocks };
    gymTitle.textContent = 'Edit class';
    gymCapture.hidden = true;
    gymProgrammeEl.innerHTML = gymProgrammeHtml(gymProgramme);
    gymClass.value = session.class_name;
    gymDate.value = session.local_date;
    gymNotes.value = session.notes || '';
    setGymRating(session.effort_rating || null);
    gymSave.textContent = 'Save changes';
    gymDelete.hidden = false;
  } else {
    gymTitle.textContent = 'Log a class';
    gymSave.textContent = 'Save class';
  }
  showView('gym');
}

function closeGym() {
  resetGym();
  showView('fitness');
  if (healthCache.has('fitness')) renderHealthView('fitness', healthCache.get('fitness'));
  else loadHealthView('fitness');
}

gymBack.addEventListener('click', closeGym);
gymPick.addEventListener('click', () => gymPhotos.click());

gymPhotos.addEventListener('change', () => {
  gymFiles = [...gymFiles, ...gymPhotos.files].slice(0, GYM_MAX_PHOTOS);
  gymPhotos.value = '';
  gymPhotoCount.textContent = gymFiles.length
    ? `${gymFiles.length} screenshot${gymFiles.length === 1 ? '' : 's'} added${gymFiles.length === GYM_MAX_PHOTOS ? ' (the most you can send)' : ''}`
    : '';
  gymRead.hidden = gymFiles.length === 0;
});

gymRead.addEventListener('click', async () => {
  if (!gymFiles.length) return;
  gymRead.disabled = true;
  gymError.innerHTML = '';
  gymStatus.textContent = 'Sending to Claude (15–30s)…';
  gymStatus.classList.add('pulsing');
  const formData = new FormData();
  for (const file of gymFiles) formData.append('photos', file);
  try {
    const r = await api('/gym/analyze', { method: 'POST', body: formData });
    if (!r.ok) throw new Error(await errorMessage(r));
    gymProgramme = await r.json();
    gymProgrammeEl.innerHTML = gymProgrammeHtml(gymProgramme);
    if (!gymClass.value.trim()) gymClass.value = gymProgramme.class_name;
    gymFiles = [];
    gymPhotoCount.textContent = '';
    gymRead.hidden = true;
  } catch (err) {
    // The class can still be saved by name and notes
    gymError.innerHTML = `<div class="error">${escapeHtml(err.message)} You can still save the class without it.</div>`;
  } finally {
    gymStatus.textContent = '';
    gymStatus.classList.remove('pulsing');
    gymRead.disabled = false;
  }
});

gymForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  gymError.innerHTML = '';
  const className = gymClass.value.trim();
  if (!className || !gymDate.value) {
    gymError.innerHTML = '<div class="error">Add the class name and the date.</div>';
    return;
  }
  const fields = {
    date: gymDate.value,
    class_name: className,
    notes: gymNotes.value.trim() || null,
    effort_rating: gymRating,
  };
  gymSave.disabled = true;
  try {
    const r = gymEditing
      ? await api(`/gym/sessions/${gymEditing.id}`, {
          method: 'PUT',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(fields),
        })
      : await api('/gym/sessions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ ...fields, programme: gymProgramme }),
        });
    if (!r.ok) throw new Error(`Save failed: ${await errorMessage(r)}`);
    afterGymChange();
  } catch (err) {
    gymError.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
    gymSave.disabled = false;
  }
});

gymDelete.addEventListener('click', async () => {
  if (!gymEditing || !confirm('Delete this class?')) return;
  gymDelete.disabled = true;
  try {
    const r = await api(`/gym/sessions/${gymEditing.id}`, { method: 'DELETE' });
    if (!r.ok) throw new Error(await errorMessage(r));
    afterGymChange();
  } catch (err) {
    gymError.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
  } finally {
    gymDelete.disabled = false;
  }
});

// Classes feed the Summary analysis, so both lists are stale after a change
function afterGymChange() {
  healthCache.delete('fitness');
  summaryCache.clear();
  resetGym();
  showView('fitness');
  loadHealthView('fitness');
}

// ---- Fitness tab section -------------------------------------------------

function gymSectionHtml(data) {
  const sessions = [...(data.gym_sessions || [])].reverse();
  const rows = sessions.map(g => {
    const notes = g.notes && g.notes.length > 70 ? `${g.notes.slice(0, 70)}…` : g.notes;
    const sub = [formatDateLabel(g.local_date), notes].filter(Boolean).map(escapeHtml).join(' · ');
    return `
      <div class="list-row tappable" data-gym-id="${g.id}">
        <div>
          <div class="list-row-main">${escapeHtml(g.class_name)}</div>
          <div class="list-row-sub">${sub}</div>
        </div>
        <div class="list-row-value">${g.effort_rating ? `${g.effort_rating}/5<div class="list-row-sub">${EFFORT_LABELS[g.effort_rating]}</div>` : ''}</div>
      </div>`;
  }).join('');
  return `
    <div class="summary-section">
      <div class="summary-section-header"><span>Gym classes</span></div>
      <button class="primary-btn gym-add" data-gym-log>Log a class</button>
      ${rows ? `<div class="list-card">${rows}</div>` : '<div class="summary-empty">No classes logged in the last 14 days.</div>'}
    </div>`;
}

function wireGymSection(container, data) {
  container.querySelector('[data-gym-log]').addEventListener('click', () => openGym());
  for (const row of container.querySelectorAll('[data-gym-id]')) {
    row.addEventListener('click', () => {
      const session = (data.gym_sessions || []).find(g => g.id === parseInt(row.dataset.gymId, 10));
      if (session) openGym(session);
    });
  }
}

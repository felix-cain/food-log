// Setup: profile form -> calorie and protein targets.

function openSetup(returnView) {
  setupReturnView = returnView;
  // First run has nowhere to go back to
  setupBack.style.visibility = profileData && profileData.profile ? 'visible' : 'hidden';
  setupForm.hidden = false;
  setupResult.hidden = true;
  setupError.innerHTML = '';
  const p = profileData && profileData.profile;
  if (p) {
    for (const key of ['age', 'height_cm', 'weight_kg', 'sex', 'activity', 'goal']) {
      setupForm.elements[key].value = p[key];
    }
  }
  showView('setup');
}

function closeSetup() {
  showView(setupReturnView);
  if (setupReturnView === 'summary') loadSummary();
}

setupBack.addEventListener('click', closeSetup);

setupForm.addEventListener('submit', async (e) => {
  e.preventDefault();
  setupError.innerHTML = '';
  if (!setupForm.checkValidity()) {
    setupError.innerHTML = '<div class="error">Fill in every field. Age 14–100, height 120–230 cm, weight 30–300 kg.</div>';
    return;
  }
  const f = setupForm.elements;
  const body = {
    age: parseInt(f.age.value, 10),
    sex: f.sex.value,
    height_cm: parseFloat(f.height_cm.value),
    weight_kg: parseFloat(f.weight_kg.value),
    activity: f.activity.value,
    goal: f.goal.value,
  };

  setupSave.disabled = true;
  try {
    const r = await api(`/profile?today=${todayISO()}`, {
      method: 'PUT',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    if (!r.ok) throw new Error(`Save failed: ${await r.text()}`);
    profileData = await r.json();
    summaryCache.clear();
    renderDayTotals();
    showSetupResult();
  } catch (err) {
    setupError.innerHTML = `<div class="error">${escapeHtml(err.message)}</div>`;
  } finally {
    setupSave.disabled = false;
  }
});

function showSetupResult() {
  const t = profileData.targets;
  setupForm.hidden = true;
  setupResult.hidden = false;
  setupResult.innerHTML = `
    <div class="setup-result">
      <div class="target-row"><span>Calories</span><strong>~${fmt(t.calories)}</strong></div>
      <div class="target-sub">Aim for ${fmt(t.calories_low)}–${fmt(t.calories_high)} a day</div>
      <div class="target-row"><span>Protein</span><strong>${t.protein_g}g</strong></div>
      <p class="setup-note">
        Estimated from your details using the Mifflin-St Jeor formula, adjusted
        for activity and goal. Treat them as a guide, not a prescription.
      </p>
    </div>
    <button class="primary-btn" id="setup-done">Done</button>
  `;
  document.getElementById('setup-done').addEventListener('click', closeSetup);
  window.scrollTo(0, 0);
}

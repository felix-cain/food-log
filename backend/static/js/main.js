// Start-up. Loaded last.

async function refreshProfile() {
  const r = await api(`/profile?today=${todayISO()}`);
  if (!r.ok) return false;
  profileData = await r.json();
  renderDayTotals();
  return true;
}

// Banners for things that need attention (expiring keys, reviews due)
async function loadNotices() {
  try {
    const r = await api(`/notices?today=${todayISO()}`);
    if (!r.ok) return;
    const { notices } = await r.json();
    document.getElementById('notices').innerHTML = notices.map(n => `
      <details class="notice">
        <summary>${escapeHtml(n.text)}</summary>
        <div class="notice-action">${escapeHtml(n.action)}</div>
      </details>`).join('');
  } catch (e) { /* not worth interrupting the app for */ }
}

async function init() {
  // Favourites first so empty slots can offer them on the first render
  await loadFavourites();
  loadDay();
  try {
    if (!(await refreshProfile())) return;
    if (!profileData.profile) openSetup('day');
    // Pull fresh Garmin data in the background (skipped if synced in the
    // last hour), then check for notices, which include a rejected key
    syncGarmin().then(loadNotices);
  } catch (e) { /* auth banner already shown, or offline */ }
}

init();

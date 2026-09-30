// Hash router: #/projects (home) · #/project/<name> · #/sessions · #/usage · #/caveman.
// Views are <section data-view="…"> blocks; only the active view is shown. Chart.js can't size
// a canvas inside a hidden section, so stats-based views render the first time they're shown.

const _rendered = new Set();

function parseHash() {
  const raw = location.hash.replace(/^#\/?/, '');
  const [path, query] = raw.split('?');
  const [view, ...rest] = (path || 'projects').split('/');
  return { view: view || 'projects', arg: rest.length ? decodeURIComponent(rest.join('/')) : '', params: new URLSearchParams(query || '') };
}

function showView(view) {
  document.querySelectorAll('[data-view]').forEach(el => { el.hidden = el.dataset.view !== view; });
  const tab = view === 'project' ? 'projects' : view;
  document.querySelectorAll('#tabs a').forEach(a => a.classList.toggle('active', a.dataset.tab === tab));
}

async function route() {
  const { view, arg, params } = parseHash();
  const known = ['projects', 'project', 'sessions', 'usage', 'caveman'];
  const v = known.includes(view) && (view !== 'project' || arg) ? view : 'projects';
  showView(v);
  window.scrollTo(0, 0);
  const data = window.__stats;
  if (v === 'projects') await renderProjectsView(params);
  else if (v === 'project') await renderProjectView(arg, params);
  else if (v === 'sessions') renderSessionsView(data, params.get('project'));
  else if (!_rendered.has(v)) {
    (v === 'usage' ? renderUsageView : renderCavemanView)(data);
    _rendered.add(v);
  }
}

async function boot() {
  try {
    window.__stats = await loadStats();
  } catch (e) {
    document.querySelector('main').innerHTML = `<div style="padding:40px;color:#f85149">
      Failed to load stats.json: ${esc(e.message)}</div>`;
    return;
  }
  initModal();
  window.addEventListener('hashchange', route);
  await route();
}

boot();

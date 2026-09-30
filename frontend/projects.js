// Projects (home) and Project detail views. Data comes from /api/projects and /api/project,
// which aggregate the durable ledger per turn. Relies on helpers from app.js
// (fmt, esc, fmtUsd, fmtWh, fmtRange, shortProject, fmtDate, openSession).

const RANGE_LABELS = { '7d': '7 days', '30d': '30 days', '90d': '90 days', all: 'All time' };

function fmtHours(h) {
  if (!h) return '0 h';
  return h < 1 ? `${Math.round(h * 60)} min` : `${h.toFixed(h < 10 ? 1 : 0)} h`;
}

function fmtAgo(ts) {
  if (!ts) return '—';
  const days = Math.floor((Date.now() - ts) / 86400000);
  if (days <= 0) return 'today';
  if (days === 1) return 'yesterday';
  if (days < 45) return `${days}d ago`;
  return fmtDate(ts);
}

function totalTokens(p) {
  return Object.values(p.tokens || {}).reduce((a, b) => a + b, 0);
}

// Range buttons (7d / 30d / 90d / all) for the current view, as links so the back button works.
function renderRangeSeg(container, current, hrefFor) {
  container.innerHTML = Object.entries(RANGE_LABELS).map(([key, label]) =>
    `<a class="seg-btn${key === current ? ' active' : ''}" href="${esc(hrefFor(key))}"
        title="Show ${esc(label.toLowerCase())}">${key === 'all' ? 'All' : key}</a>`).join('');
}

function estimateNote(estUsd, totalUsd) {
  if (!estUsd || !totalUsd) return '';
  const pct = Math.round(estUsd / totalUsd * 100);
  return `<span class="est-badge" title="${pct}% of this is estimated from prompt counts (sessions whose transcripts were deleted before the ledger existed)">~${pct}% est.</span>`;
}

function statCells(cells) {
  return cells.map(c => `
    <div class="stat-cell"${c.title ? ` title="${esc(c.title)}"` : ''}>
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
      ${c.sub ? `<div class="sub">${c.sub}</div>` : ''}
    </div>`).join('');
}

// ── Projects (home) ─────────────────────────────────────────────────────────

async function renderProjectsView(params) {
  const range = params.get('range') || '30d';
  const group = params.get('group') || 'project';
  const res = await fetch(`/api/projects?range=${encodeURIComponent(range)}&group=${encodeURIComponent(group)}`);
  if (!res.ok) return;
  const data = await res.json();

  const hrefFor = (r) => `#/projects?range=${r}${group !== 'project' ? `&group=${encodeURIComponent(group)}` : ''}`;
  document.querySelectorAll('#projects-view [data-range-seg]').forEach(el => renderRangeSeg(el, data.range, hrefFor));

  const groupSel = document.getElementById('projects-group');
  groupSel.innerHTML = [['project', 'project'], ...data.tag_keys.map(k => [`tag:${k}`, k])]
    .map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');
  groupSel.value = group;
  groupSel.onchange = () => {
    location.hash = `#/projects?range=${data.range}${groupSel.value !== 'project' ? `&group=${encodeURIComponent(groupSel.value)}` : ''}`;
  };

  const billing = window.__stats?.billing_configured;
  document.getElementById('projects-callout').innerHTML = `
    <strong>Two kinds of dollars.</strong> <em>API $</em> is what these tokens would cost at Anthropic's list prices —
    useful for quoting work. <em>Your $</em> is your subscription fee split by each project's share of that month's usage
    ${billing ? '' : '— add <code>[billing]</code> to <code>~/.config/claude-token-tracker/tags.toml</code> to see it'}.
    Anything marked <span class="est-badge">est.</span> includes older sessions reconstructed from prompt counts after
    Claude Code deleted their transcripts.`;

  const t = data.totals;
  document.getElementById('projects-kpis').innerHTML = statCells([
    { label: 'API $', value: fmtUsd(t.api_usd), sub: `${RANGE_LABELS[data.range]} ${estimateNote(t.estimated_api_usd, t.api_usd)}`,
      title: 'Tokens × Anthropic API list prices, all projects in range' },
    { label: 'Your $', value: billing ? fmtUsd(t.sub_usd) : '—', sub: billing ? 'subscription share' : 'set [billing] in tags.toml' },
    { label: 'Active time', value: fmtHours(t.active_hours), sub: 'gaps ≤ 15 min between turns',
      title: 'Sum of time between consecutive turns, ignoring idle gaps over 15 minutes' },
    { label: 'Energy', value: fmtWh(t.wh[1]), sub: fmtRange(t.wh, fmtWh), title: 'Estimated data-center electricity (see Usage → Environmental Impact)' },
    { label: 'Projects', value: t.projects, sub: group === 'project' ? 'with activity in range' : `distinct ${esc(group.slice(4))} values` },
  ]);

  const maxUsd = Math.max(...data.projects.map(p => p.api_usd), 0.0001);
  const linkable = group === 'project';
  document.getElementById('projects-table-wrap').innerHTML = data.projects.length ? `
    <table class="sessions-table projects-table">
      <thead><tr>
        <th>${linkable ? 'Project' : esc(group.slice(4))}</th>
        <th title="API list-price dollars">API $</th>
        <th title="Subscription share">Your $</th>
        <th title="All tokens incl. cache reads">Tokens</th>
        <th title="Estimated electricity, central">Energy</th>
        <th title="Active time (gaps ≤ 15 min)">Active</th>
        <th title="Sessions (subagents fold into their parent)">Sessions</th>
        <th title="Distinct files touched by tool calls">Files</th>
        <th title="Lines added / removed via Edit and Write">Lines</th>
        <th>Last active</th>
      </tr></thead>
      <tbody>${data.projects.map(p => `
        <tr${linkable ? ` data-project="${esc(p.name)}"` : ''} title="${esc(p.name)}">
          <td>
            <div class="proj-name">${esc(shortProject(p.name))} ${estimateNote(p.estimated_api_usd, p.api_usd)}</div>
            <div class="share-bar"><span style="width:${(p.api_usd / maxUsd * 100).toFixed(1)}%"></span></div>
          </td>
          <td>${fmtUsd(p.api_usd)}</td>
          <td>${billing ? fmtUsd(p.sub_usd) : '—'}</td>
          <td>${fmt(totalTokens(p))}</td>
          <td title="${esc(fmtRange(p.wh, fmtWh))}">${fmtWh(p.wh[1])}</td>
          <td>${fmtHours(p.active_hours)}</td>
          <td>${p.sessions}${p.subagents ? ` <span class="muted">+${p.subagents}</span>` : ''}</td>
          <td>${p.files_touched || '—'}</td>
          <td>${p.lines_added || p.lines_removed ? `+${fmt(p.lines_added)} / −${fmt(p.lines_removed)}` : '—'}</td>
          <td>${fmtAgo(p.last_ts)}</td>
        </tr>`).join('')}
      </tbody>
    </table>` : '<p class="chart-sub">No activity in this range.</p>';

  if (linkable) {
    document.querySelectorAll('#projects-table-wrap tr[data-project]').forEach(tr => {
      tr.addEventListener('click', () => {
        location.hash = `#/project/${encodeURIComponent(tr.dataset.project)}?range=${data.range}`;
      });
    });
  }
}

// ── Project detail ──────────────────────────────────────────────────────────

let _projectDailyChart = null;

function renderProjectDaily(daily) {
  if (_projectDailyChart) _projectDailyChart.destroy();
  const labels = daily.map(d => d.date);
  _projectDailyChart = new Chart(document.getElementById('chart-project-daily'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        { label: 'Exact', data: daily.map(d => +(d.api_usd - d.estimated_api_usd).toFixed(4)),
          backgroundColor: COLORS.blue + 'cc', borderRadius: 3, stack: 'usd' },
        { label: 'Estimated', data: daily.map(d => +d.estimated_api_usd.toFixed(4)),
          backgroundColor: COLORS.blue + '40', borderColor: COLORS.blue, borderWidth: 1,
          borderDash: [3, 3], borderRadius: 3, stack: 'usd' },
      ],
    },
    options: {
      responsive: true, maintainAspectRatio: false,
      plugins: {
        legend: { labels: { boxWidth: 12 } },
        tooltip: { callbacks: { label: ctx => `${ctx.dataset.label}: ${fmtUsd(ctx.parsed.y)}` } },
      },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { maxRotation: 0, autoSkip: true, maxTicksLimit: 8 } },
        y: { stacked: true, grid: { color: '#21262d' }, ticks: { callback: v => '$' + v } },
      },
    },
  });
}

function renderProjectModels(models) {
  const total = Object.values(models).reduce((a, b) => a + b, 0) || 1;
  document.getElementById('project-models').innerHTML = Object.entries(models)
    .sort((a, b) => b[1] - a[1])
    .map(([m, n]) => `
      <div class="kv-row" title="${n} turns">
        <span>${esc(shortModel(m))}</span>
        <span class="share-bar inline"><span style="width:${(n / total * 100).toFixed(1)}%"></span></span>
        <strong>${Math.round(n / total * 100)}%</strong>
      </div>`).join('') || '<p class="chart-sub">—</p>';
}

function renderProjectSignals(s) {
  const rows = [
    ['Prompts', s.prompts, 'User prompts that drove work on this project (estimated sessions: one per prompt)'],
    ['Turns', fmt(s.turns), 'Model calls attributed to this project'],
    ['Subagents', s.subagents, 'Subagent runs that worked on this project'],
    ['Tool calls', fmt(s.tool_calls), 'Tool uses (Edit, Bash, Read, …) in exact transcripts'],
    ['Files touched', s.files_touched, 'Distinct file paths touched by tool calls'],
    ['Lines', `+${fmt(s.lines_added)} / −${fmt(s.lines_removed)}`, 'From Edit diffs and Write contents (Write over an existing file over-counts)'],
    ['Avg context', fmt(s.avg_context), 'Average tokens processed per turn (input + cache) — a proxy for how much code/context each step needed'],
    ['Active days', s.active_days, 'Distinct days with activity'],
  ];
  document.getElementById('project-signals').innerHTML = rows.map(([k, v, title]) => `
    <div class="kv-row" title="${esc(title)}"><span>${k}</span><strong>${v}</strong></div>`).join('');
}

function renderProjectSessions(sessions, range) {
  const wrap = document.getElementById('project-sessions-wrap');
  wrap.innerHTML = sessions.length ? `
    <table class="sessions-table">
      <thead><tr><th>Last active</th><th>Session</th><th>API $</th><th>Active</th><th>Turns</th><th>Files</th></tr></thead>
      <tbody>${sessions.map(s => `
        <tr data-sid="${esc(s.session_id)}" title="${esc(s.session_name || s.session_id)}">
          <td>${fmtAgo(s.last_ts)}</td>
          <td>${s.is_subagent ? '<span class="badge badge-agent">agent</span> ' : ''}${s.estimated ? '<span class="est-badge">est.</span> ' : ''}<span class="session-name">${esc((s.session_name || s.session_id).slice(0, 60))}</span></td>
          <td>${fmtUsd(s.api_usd)}</td>
          <td>${fmtHours(s.active_hours)}</td>
          <td>${s.turns}</td>
          <td>${s.files_touched || '—'}</td>
        </tr>`).join('')}
      </tbody>
    </table>` : `<p class="chart-sub">No sessions in ${esc(RANGE_LABELS[range].toLowerCase())}.</p>`;
  wrap.querySelectorAll('tr[data-sid]').forEach(tr => tr.addEventListener('click', () => openSession(tr.dataset.sid)));
}

async function renderProjectView(name, params) {
  const range = params.get('range') || 'all';
  const res = await fetch(`/api/project?name=${encodeURIComponent(name)}&range=${encodeURIComponent(range)}`);
  if (!res.ok) return;
  const d = await res.json();
  const s = d.summary;
  const billing = window.__stats?.billing_configured;

  document.getElementById('project-title').textContent = shortProject(name);
  document.getElementById('project-title').title = name;
  document.getElementById('project-tags').innerHTML = Object.entries(d.tags || {})
    .map(([k, v]) => `<span class="tag-chip" title="${esc(k)}">${esc(v)}</span>`).join('');
  document.querySelectorAll('#project-view [data-range-seg]').forEach(el =>
    renderRangeSeg(el, d.range, r => `#/project/${encodeURIComponent(name)}?range=${r}`));

  const callout = document.getElementById('project-callout');
  callout.hidden = !s.estimated_api_usd;
  callout.innerHTML = `<strong>Includes estimates.</strong> ${fmtUsd(s.estimated_api_usd)} of ${fmtUsd(s.api_usd)} comes from
    sessions whose transcripts were deleted before the ledger existed; they're sized from prompt counts × this project's
    typical tokens per prompt, priced at the model most used that month.`;

  document.getElementById('project-kpis').innerHTML = statCells([
    { label: 'API $', value: fmtUsd(s.api_usd), sub: estimateNote(s.estimated_api_usd, s.api_usd) || 'API list price' },
    { label: 'Your $', value: billing ? fmtUsd(s.sub_usd) : '—', sub: billing ? 'subscription share' : 'set [billing] in tags.toml' },
    { label: 'Active time', value: fmtHours(s.active_hours), sub: `${s.active_days} active day${s.active_days === 1 ? '' : 's'}` },
    { label: 'Energy', value: fmtWh(s.wh[1]), sub: fmtRange(s.wh, fmtWh), title: 'Estimated data-center electricity' },
    { label: 'Tokens', value: fmt(totalTokens(s)), sub: `${fmt(s.tokens.output || 0)} output` },
    { label: 'Sessions', value: s.sessions, sub: s.subagents ? `+${s.subagents} subagents` : `${s.prompts} prompts` },
    { label: 'Span', value: s.first_ts ? `${Math.max(1, Math.round((s.last_ts - s.first_ts) / 86400000))} days` : '—',
      sub: s.first_ts ? `${fmtDate(s.first_ts)} → ${fmtAgo(s.last_ts)}` : '', title: 'First to last activity in range' },
  ]);

  renderProjectDaily(d.daily);
  renderProjectModels(s.models || {});
  renderProjectSignals(s);
  renderProjectSessions(d.sessions, d.range);
}

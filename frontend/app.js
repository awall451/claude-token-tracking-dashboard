const COLORS = {
  blue:   '#58a6ff',
  green:  '#3fb950',
  orange: '#f0883e',
  purple: '#bc8cff',
  red:    '#f85149',
  teal:   '#39d353',
  muted:  '#8b949e',
};

const CHART_DEFAULTS = {
  color: '#e6edf3',
  borderColor: '#30363d',
  backgroundColor: 'transparent',
};

Chart.defaults.color = CHART_DEFAULTS.color;
Chart.defaults.borderColor = CHART_DEFAULTS.borderColor;

function fmt(n) {
  if (n >= 1_000_000) return (n / 1_000_000).toFixed(2) + 'M';
  if (n >= 1_000)     return (n / 1_000).toFixed(1) + 'K';
  return String(n);
}

// Escape transcript-derived strings (prompts, paths) before interpolating into HTML.
function esc(str) {
  return String(str ?? '').replace(/[&<>"']/g, c => (
    { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
  ));
}

function fmtUsd(n) {
  return '$' + (n >= 100 ? Math.round(n).toLocaleString() : n.toFixed(2));
}

function shortProject(path) {
  if (!path) return 'unknown';
  const parts = path.split('/');
  return parts[parts.length - 1] || parts[parts.length - 2] || path;
}

function renderCards(totals, lifetime) {
  const lt = lifetime || {};
  const peakDate = lt.peak_day && lt.peak_day.date ? lt.peak_day.date : '—';
  const dailyAvgTooltip = lt.days_tracked
    ? `${fmt(lt.total_cost_tokens || 0)} ÷ ${lt.days_tracked} days tracked`
    : 'no data';
  const cards = [
    { label: 'Sessions',     value: totals.sessions,                        sub: null },
    { label: 'Turns',        value: fmt(totals.turns),                      sub: null },
    { label: 'Cost Tokens',  value: fmt(totals.total_cost_tokens),           sub: 'input + output + cache writes' },
    { label: 'Cache Hit',    value: (totals.cache_hit_rate * 100).toFixed(1) + '%', sub: 'reads / (reads + writes)' },
    { label: 'Output Tokens',value: fmt(totals.output_tokens),               sub: null },
    { label: 'Cache Reads',  value: fmt(totals.cache_read_tokens),           sub: null },
    { label: 'Daily Average',value: fmt(lt.daily_average_cost_tokens || 0),  sub: `${lt.days_tracked || 0} days tracked`, title: dailyAvgTooltip },
    { label: 'Weekly Average',value: fmt(lt.weekly_average_cost_tokens || 0), sub: 'daily avg × 7' },
    { label: 'Peak Day',     value: fmt((lt.peak_day && lt.peak_day.cost_tokens) || 0), sub: peakDate, title: `Highest single-day total: ${peakDate}` },
  ];
  if (totals.reported_sessions) {
    cards.push({
      label: 'API-Equivalent Cost',
      value: fmtUsd(totals.reported_cost_usd || 0),
      sub: `Claude Code's own tally · ${totals.reported_sessions} sessions`,
      title: 'Sum of totalCostUSD from cost-state entries Claude Code writes into each transcript. '
           + 'What this usage would cost at API list prices, not what a subscription bills. '
           + `Includes side calls missing from transcripts (${fmt(totals.reported_tokens || 0)} tokens reported).`,
    });
  }
  document.getElementById('cards').innerHTML = cards.map(c => `
    <div class="card"${c.title ? ` title="${esc(c.title)}"` : ''}>
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
      ${c.sub ? `<div class="sub">${c.sub}</div>` : ''}
    </div>
  `).join('');
}

function renderLifetime(lt) {
  if (!lt) return;
  const calloutEl = document.getElementById('lifetime-callout');
  const gridEl = document.getElementById('lifetime-grid');
  if (!calloutEl || !gridEl) return;

  const firstDate = lt.first_session_at_ms ? fmtDate(lt.first_session_at_ms) : '—';
  const lastDate = lt.last_session_at_ms ? fmtDate(lt.last_session_at_ms) : '—';
  const peakDate = (lt.peak_day && lt.peak_day.date) || '—';

  calloutEl.innerHTML = `
    <strong>Heads-up:</strong> these numbers sum every session JSONL stored under
    <code>~/.claude/projects/</code> on this machine — not Anthropic's authoritative quota.
    Claude Code does not expose account-level totals locally; the official source of truth lives at
    <code>claude.ai</code> (Settings → Usage).
    Tracking ${lt.days_tracked || 0} day${lt.days_tracked === 1 ? '' : 's'} of activity from
    <strong>${firstDate}</strong> to <strong>${lastDate}</strong>.
  `;

  const stats = [
    { label: 'Lifetime cost tokens', value: fmt(lt.total_cost_tokens || 0), sub: 'input + cache writes + output' },
    { label: 'Lifetime turns',       value: fmt(lt.total_turns || 0),       sub: `across ${lt.total_sessions || 0} sessions` },
    { label: 'Daily average',        value: fmt(lt.daily_average_cost_tokens || 0), sub: 'lifetime ÷ days tracked' },
    { label: 'Weekly average',       value: fmt(lt.weekly_average_cost_tokens || 0), sub: 'daily × 7' },
    { label: 'Monthly average',      value: fmt(lt.monthly_average_cost_tokens || 0), sub: 'daily × 30' },
    { label: 'Last 30d total',       value: fmt(lt.last_30d_total_cost_tokens || 0), sub: 'most recent 30 days of data' },
    { label: 'Last 30d daily avg',   value: fmt(lt.last_30d_daily_average || 0), sub: null },
    { label: 'Peak day',             value: fmt((lt.peak_day && lt.peak_day.cost_tokens) || 0), sub: peakDate },
    { label: 'First session',        value: firstDate, sub: null },
  ];

  gridEl.innerHTML = stats.map(s => `
    <div class="stat-cell">
      <div class="label">${s.label}</div>
      <div class="value">${s.value}</div>
      ${s.sub ? `<div class="sub">${s.sub}</div>` : ''}
    </div>
  `).join('');
}

function renderCavemanComparison(data) {
  const cc = data.caveman_comparison;
  const { caveman, normal, savings_pct } = cc;
  const noPlugin = !data.has_caveman_plugin;
  const noData = caveman.sessions === 0;

  if (noPlugin && noData) {
    document.getElementById('caveman-onboarding').hidden = false;
    document.getElementById('caveman-data').hidden = true;
    return;
  }

  if (!noPlugin && noData) {
    document.getElementById('caveman-no-data').hidden = false;
    document.getElementById('caveman-data').hidden = true;
    return;
  }

  document.getElementById('cav-normal').innerHTML = `
    <div class="mode">Normal</div>
    <div class="big" style="color:${COLORS.blue}">${fmt(normal.avg_tokens_per_turn)}</div>
    <div class="desc">avg tokens/turn</div>
    <div class="desc" style="margin-top:8px">${normal.sessions} sessions · ${fmt(normal.turns)} turns</div>
  `;

  document.getElementById('cav-caveman').innerHTML = `
    <div class="mode" style="color:${COLORS.orange}">Caveman</div>
    <div class="big" style="color:${COLORS.orange}">${fmt(caveman.avg_tokens_per_turn)}</div>
    <div class="desc">avg tokens/turn</div>
    <div class="desc" style="margin-top:8px">${caveman.sessions} sessions · ${fmt(caveman.turns)} turns</div>
  `;

  const sign = savings_pct > 0 ? '-' : '+';
  const color = savings_pct > 0 ? COLORS.green : COLORS.red;
  document.getElementById('cav-savings').innerHTML = `
    <div class="pct" style="color:${color}">${sign}${Math.abs(savings_pct)}%</div>
    <div class="label">tokens saved</div>
    <div class="sub">caveman vs normal</div>
  `;

  renderCavemanModes(cc.by_mode || {});
}

const MODE_COLORS = {
  normal: COLORS.blue,
  lite:   '#7ee787',
  full:   COLORS.orange,
  ultra:  COLORS.red,
  wenyan: COLORS.purple,
};

function renderCavemanModes(byMode) {
  const CANONICAL = ['normal', 'lite', 'full', 'ultra', 'wenyan'];
  const modes = [
    ...CANONICAL.filter(m => byMode[m] && byMode[m].avg_tokens_per_turn > 0),
    ...Object.keys(byMode).filter(m => !CANONICAL.includes(m) && byMode[m].avg_tokens_per_turn > 0),
  ];
  if (modes.length < 2) return; // nothing interesting to show with only 1 mode

  new Chart(document.getElementById('chart-caveman-modes'), {
    type: 'bar',
    data: {
      labels: modes.map(m => m.charAt(0).toUpperCase() + m.slice(1)),
      datasets: [{
        label: 'Avg tokens/turn',
        data: modes.map(m => byMode[m].avg_tokens_per_turn),
        backgroundColor: modes.map(m => (MODE_COLORS[m] || COLORS.muted) + '99'),
        borderColor: modes.map(m => MODE_COLORS[m] || COLORS.muted),
        borderWidth: 1,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            afterLabel: (item) => {
              const s = byMode[modes[item.dataIndex]];
              return `${s.sessions} sessions · ${fmt(s.turns)} turns`;
            },
          },
        },
      },
      scales: {
        y: { beginAtZero: true, grid: { color: '#21262d' } },
        x: { grid: { display: false } },
      },
    },
  });
}

function renderCavemanAdoption(byDay) {
  const days = Object.keys(byDay).slice(-60);
  const dayData = days.map(d => byDay[d]);

  new Chart(document.getElementById('chart-caveman-adoption'), {
    type: 'bar',
    data: {
      labels: days,
      datasets: [
        {
          label: 'Normal',
          data: dayData.map(d => d.normal_sessions || 0),
          backgroundColor: COLORS.blue + '99',
          borderColor: COLORS.blue,
          borderWidth: 1,
          stack: 'sessions',
        },
        {
          label: 'Caveman',
          data: dayData.map(d => d.caveman_sessions || 0),
          backgroundColor: COLORS.orange + '99',
          borderColor: COLORS.orange,
          borderWidth: 1,
          stack: 'sessions',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top' },
        tooltip: {
          callbacks: {
            footer: (items) => {
              const total = items.reduce((s, i) => s + i.raw, 0);
              return `Total: ${total} session${total !== 1 ? 's' : ''}`;
            },
          },
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, beginAtZero: true, grid: { color: '#21262d' }, ticks: { stepSize: 1, precision: 0 } },
      },
    },
  });
}

function renderDaily(byDay, rolling7d) {
  const days = Object.keys(byDay).slice(-60); // last 60 days
  const data = days.map(d => byDay[d]);

  // Build rolling-7d map keyed by date for fast lookup, then align to displayed days
  const rolling7Map = {};
  for (const r of (rolling7d || [])) rolling7Map[r.date] = r.avg_cost_tokens;
  const rollingValues = days.map(d => rolling7Map[d] ?? null);

  new Chart(document.getElementById('chart-daily'), {
    type: 'bar',
    data: {
      labels: days,
      datasets: [
        {
          label: 'Input',
          data: data.map(d => d.input_tokens),
          backgroundColor: COLORS.blue + '99',
          borderColor: COLORS.blue,
          borderWidth: 1,
          stack: 'tokens',
        },
        {
          label: 'Cache Writes',
          data: data.map(d => d.cache_creation_tokens),
          backgroundColor: COLORS.purple + '99',
          borderColor: COLORS.purple,
          borderWidth: 1,
          stack: 'tokens',
        },
        {
          label: 'Output',
          data: data.map(d => d.output_tokens),
          backgroundColor: COLORS.orange + '99',
          borderColor: COLORS.orange,
          borderWidth: 1,
          stack: 'tokens',
        },
        {
          label: '7-day avg',
          type: 'line',
          data: rollingValues,
          borderColor: COLORS.orange,
          backgroundColor: COLORS.orange,
          borderWidth: 2,
          pointRadius: 0,
          pointHoverRadius: 4,
          tension: 0.25,
          fill: false,
          spanGaps: true,
          stack: undefined,
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top' },
        tooltip: {
          callbacks: {
            footer: (items) => {
              const total = items.reduce((s, i) => s + i.raw, 0);
              return `Total: ${fmt(total)}`;
            },
          },
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false } },
        y: { stacked: true, grid: { color: '#21262d' } },
      },
    },
  });
}

const GROUP_SUBTITLES = {
  project: 'Cost tokens per project, attributed per turn from the files each tool call touched',
  launch:  'Cost tokens per directory Claude Code was launched in (the old per-session view)',
};
let _projectChart = null;

function renderByProject(data) {
  const select = document.getElementById('project-groupby');
  const tagKeys = Object.keys(data.by_tag || {}).sort();
  const options = [
    ['project', 'project'],
    ...tagKeys.map(k => [`tag:${k}`, `tag: ${k}`]),
    ['launch', 'launch dir'],
  ];
  select.innerHTML = options.map(([v, l]) => `<option value="${esc(v)}">${esc(l)}</option>`).join('');

  const callout = document.getElementById('project-callout');
  if (!tagKeys.length) {
    callout.hidden = false;
    callout.innerHTML = `<strong>Tip:</strong> working across several repos from one directory? Add
      <code>~/.config/claude-token-tracker/tags.toml</code> (see <code>tags.example.toml</code>) to split usage by
      project and group by tags like <code>client</code> or <code>kind</code>. Use <code>/tag &lt;name&gt;</code> in a
      session to override.`;
  }

  const draw = () => {
    const mode = select.value;
    let source;
    if (mode === 'launch') source = data.by_launch_dir || {};
    else if (mode.startsWith('tag:')) source = (data.by_tag || {})[mode.slice(4)] || {};
    else source = data.by_project || {};
    document.getElementById('project-sub').textContent = mode.startsWith('tag:')
      ? `Cost tokens per "${mode.slice(4)}" tag value (turns without this tag are not shown)`
      : GROUP_SUBTITLES[mode];

    const entries = Object.entries(source)
      .sort((a, b) => b[1].total_cost_tokens - a[1].total_cost_tokens)
      .slice(0, 15);

    if (_projectChart) _projectChart.destroy();
    _projectChart = new Chart(document.getElementById('chart-project'), {
      type: 'bar',
      data: {
        labels: entries.map(([p]) => shortProject(p)),
        datasets: [
          {
            label: 'Cost Tokens',
            data: entries.map(([, d]) => d.total_cost_tokens),
            backgroundColor: COLORS.blue + '99',
            borderColor: COLORS.blue,
            borderWidth: 1,
            borderRadius: 4,
            yAxisID: 'y',
          },
        ],
      },
      options: {
        responsive: true,
        maintainAspectRatio: false,
        indexAxis: 'y',
        plugins: {
          legend: { display: false },
          tooltip: {
            callbacks: {
              afterLabel: ctx => {
                const d = entries[ctx.dataIndex][1];
                return `${d.turns} turns · ${d.sessions} sessions · ${fmt(d.cache_read_tokens)} cache reads`;
              },
            },
          },
        },
        scales: {
          x: { grid: { color: '#21262d' } },
          y: {
            grid: { display: false },
            ticks: { autoSkip: false },
          },
        },
      },
    });
  };

  select.onchange = draw;
  draw();
}

// --- Real quota (statusline snapshots) ---------------------------------------

function fmtIn(seconds) {
  if (seconds <= 0) return 'now';
  const h = Math.floor(seconds / 3600), m = Math.floor((seconds % 3600) / 60);
  if (h >= 24) return `${Math.floor(h / 24)}d ${h % 24}h`;
  return h ? `${h}h ${m}m` : `${m}m`;
}

function renderQuota(q) {
  const el = document.getElementById('quota-strip');
  el.hidden = false;
  if (!q || (!q.five_hour && !q.seven_day)) {
    el.innerHTML = `<strong>Want real quota numbers?</strong> On Pro/Max, Claude Code gives the statusline your
      actual 5-hour and weekly usage %. Wrap your statusline with <code>extras/statusline-tee.sh</code>
      (see README) and they'll show here, and the soft limit gets measured instead of guessed.`;
    return;
  }
  const now = Date.now() / 1000;
  const part = (label, w) => w ? `
      <span title="${esc(`Captured ${fmtDate(w.captured_at * 1000)} · ${fmt(w.window_cost_tokens)} cost tokens seen in this window`)}">
        ${label}: <strong>${w.used_percentage.toFixed(0)}%</strong> used · resets in ${fmtIn(w.resets_at - now)}
      </span>` : '';
  const stale = q.age_seconds > 3600
    ? ` <span title="No statusline refresh recently — numbers may be out of date">(as of ${fmtIn(q.age_seconds)} ago)</span>`
    : '';
  el.innerHTML = `<strong>Real quota</strong> (from Claude Code's statusline)${stale}: &nbsp;
    ${part('5-hour', q.five_hour)} &nbsp;|&nbsp; ${part('weekly', q.seven_day)}`;
}

// --- Environmental impact ---------------------------------------------------

function fmtWh(wh) {
  if (wh >= 1000) return (wh / 1000).toFixed(wh >= 10000 ? 1 : 2) + ' kWh';
  return wh.toFixed(wh >= 10 ? 0 : 1) + ' Wh';
}
function fmtMl(ml) { return ml >= 1000 ? (ml / 1000).toFixed(1) + ' L' : Math.round(ml) + ' mL'; }
function fmtG(g) { return g >= 1000 ? (g / 1000).toFixed(1) + ' kg' : Math.round(g) + ' g'; }
function fmtRange(tri, f) { return `${f(tri[0])} – ${f(tri[2])}`; }

// Everyday equivalents for a central Wh figure. 1 kW microwave; 10 W LED bulb;
// ~20 Wh per phone charge (assumption, see research notes).
function whEquivalent(wh) {
  const microwaveMin = wh / 1000 * 60;
  if (microwaveMin < 90) return `≈ ${microwaveMin < 1 ? '<1' : Math.round(microwaveMin)} min of a microwave`;
  return `≈ ${Math.round(wh / 10).toLocaleString()} LED-bulb hours`;
}

function renderImpact(impact) {
  const panel = document.getElementById('impact-panel');
  if (!impact) { panel.hidden = true; return; }
  const a = impact.assumptions || {};
  const t = impact.total, m = impact.last_30d;

  document.getElementById('impact-callout').innerHTML = `
    <strong>These are estimates, not measurements.</strong> No AI provider publishes energy per token, so every
    figure is back-derived from third-party studies (Epoch AI, Simon Couch, Zeke Hausfather) and the real value can
    fall outside the range. Carbon uses ${a.grid_gco2_per_kwh} gCO₂e/kWh (≈ US-average grid); water counts on-site
    cooling only (${a.water_onsite_ml_per_wh} mL/Wh) — water evaporated at power plants adds roughly 2–12× more.
    Cache reads are included: they are cheap per token but, at agentic volumes, most of the energy.`;

  const cells = [
    { label: 'Energy (all time)', value: fmtWh(t.wh[1]), sub: fmtRange(t.wh, fmtWh), title: whEquivalent(t.wh[1]) },
    { label: 'Water, on-site',    value: fmtMl(t.water_ml[1]), sub: fmtRange(t.water_ml, fmtMl), title: 'Data-center cooling water only' },
    { label: 'Carbon',            value: fmtG(t.gco2[1]) + ' CO₂e', sub: fmtRange(t.gco2, fmtG), title: `At ${a.grid_gco2_per_kwh} g/kWh` },
    { label: 'Energy, last 30d',  value: fmtWh(m.wh[1]), sub: fmtRange(m.wh, fmtWh), title: 'Most recent 30 days with activity' },
    { label: 'Equivalent',        value: whEquivalent(t.wh[1]).replace('≈ ', ''), sub: 'central estimate, all time' },
  ];
  document.getElementById('impact-grid').innerHTML = cells.map(c => `
    <div class="stat-cell"${c.title ? ` title="${esc(c.title)}"` : ''}>
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
      ${c.sub ? `<div class="sub">${c.sub}</div>` : ''}
    </div>
  `).join('');

  const rows = Object.entries(impact.by_project || {})
    .sort((x, y) => y[1].wh[1] - x[1].wh[1])
    .slice(0, 12);
  document.getElementById('impact-table-wrap').innerHTML = `
    <table class="sessions-table">
      <thead><tr><th>Project</th><th>Energy</th><th>Water</th><th>Carbon</th><th>Roughly</th></tr></thead>
      <tbody>${rows.map(([name, v]) => `
        <tr title="${esc(`${name}\nEnergy ${fmtRange(v.wh, fmtWh)} · Water ${fmtRange(v.water_ml, fmtMl)} · Carbon ${fmtRange(v.gco2, fmtG)}`)}">
          <td>${esc(shortProject(name))}</td>
          <td>${fmtWh(v.wh[1])}</td>
          <td>${fmtMl(v.water_ml[1])}</td>
          <td>${fmtG(v.gco2[1])}</td>
          <td>${whEquivalent(v.wh[1])}</td>
        </tr>`).join('')}
      </tbody>
    </table>`;
}

function renderModels(sessions) {
  const modelCounts = {};
  for (const s of sessions) {
    for (const [model, count] of Object.entries(s.models || {})) {
      modelCounts[model] = (modelCounts[model] || 0) + count;
    }
  }
  const entries = Object.entries(modelCounts).sort((a, b) => b[1] - a[1]);
  const palette = [COLORS.blue, COLORS.orange, COLORS.green, COLORS.purple, COLORS.teal];

  new Chart(document.getElementById('chart-models'), {
    type: 'doughnut',
    data: {
      labels: entries.map(([m]) => m.replace('claude-', '').replace(/-\d{8}$/, '')),
      datasets: [{
        data: entries.map(([, n]) => n),
        backgroundColor: entries.map((_, i) => palette[i % palette.length] + 'cc'),
        borderColor: entries.map((_, i) => palette[i % palette.length]),
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { position: 'right' } },
    },
  });
}

function renderCache(totals) {
  const { cache_read_tokens, cache_creation_tokens, input_tokens } = totals;
  new Chart(document.getElementById('chart-cache'), {
    type: 'doughnut',
    data: {
      labels: ['Cache Reads', 'Cache Writes', 'Fresh Input'],
      datasets: [{
        data: [cache_read_tokens, cache_creation_tokens, input_tokens],
        backgroundColor: [COLORS.green + 'cc', COLORS.purple + 'cc', COLORS.blue + 'cc'],
        borderColor: [COLORS.green, COLORS.purple, COLORS.blue],
        borderWidth: 1,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'right' },
        tooltip: {
          callbacks: {
            label: (item) => ` ${item.label}: ${fmt(item.raw)}`,
          },
        },
      },
    },
  });
}

function barColor(pct) {
  if (pct >= 0.85) return COLORS.red;
  if (pct >= 0.60) return COLORS.orange;
  return COLORS.green;
}

function renderRolling(rolling, softLimit) {
  const peak5h = rolling.historical_peak_5h || 0;
  const windows = [
    { key: '1h',  label: 'Last 1 Hour',              peak: null },
    { key: '5h',  label: 'Last 5 Hours (reset window)', peak: peak5h },
    { key: '24h', label: 'Last 24 Hours',             peak: null },
  ];

  document.getElementById('rolling-grid').innerHTML = windows.map(({ key, label, peak }) => {
    const w = rolling[key];
    const pct = softLimit > 0 ? Math.min(w.cost_tokens / softLimit, 1) : 0;
    const color = barColor(pct);
    const pctStr = (pct * 100).toFixed(1);
    const peakNote = peak ? `&nbsp;·&nbsp; peak ${fmt(peak)}` : '';
    return `
      <div class="rolling-card">
        <div class="rc-label">${label}</div>
        <div class="rc-bar-wrap">
          <div class="rc-bar" style="width:${pctStr}%;background:${color}"></div>
        </div>
        <div class="rc-value" style="color:${color}">${fmt(w.cost_tokens)}</div>
        <div class="rc-sub">${w.turns} turns &nbsp;·&nbsp; ${pctStr}% of ${fmt(softLimit)} limit &nbsp;·&nbsp; ${fmt(w.tokens_per_hour)}/hr${peakNote}</div>
      </div>
    `;
  }).join('');

  // Burn rate projection (based on 1h rate vs 5h limit)
  const burn = rolling.burn_rate_per_hour;
  const cost5h = rolling['5h'].cost_tokens;
  let projHtml = '';
  if (burn > 0 && softLimit > 0 && cost5h < softLimit) {
    const remaining = softLimit - cost5h;
    const hoursLeft = remaining / burn;
    const color = hoursLeft < 1 ? COLORS.red : hoursLeft < 3 ? COLORS.orange : COLORS.green;
    projHtml = `<div style="margin-top:8px;font-size:12px;color:${color}">
      At current pace (${fmt(burn)} tok/hr), 5h window fills in ~${hoursLeft.toFixed(1)}h
    </div>`;
  } else if (burn === 0) {
    projHtml = `<div style="margin-top:8px;font-size:12px;color:var(--muted)">No activity in last hour.</div>`;
  }

  document.getElementById('rolling-panel').insertAdjacentHTML('beforeend', projHtml);

  // Sparkline: 24h hourly buckets
  const buckets = rolling.hourly_buckets_24h;
  const nowHour = new Date();
  const labels = buckets.map((_, i) => {
    const h = new Date(nowHour.getTime() - (23 - i) * 3600 * 1000);
    return h.getHours() + ':00';
  });

  const sparkColors = buckets.map(v => {
    const pct = softLimit > 0 ? v / softLimit : 0;
    return barColor(pct) + 'cc';
  });

  new Chart(document.getElementById('chart-sparkline'), {
    type: 'bar',
    data: {
      labels,
      datasets: [{
        label: 'Tokens/hr',
        data: buckets,
        backgroundColor: sparkColors,
        borderWidth: 0,
        borderRadius: 2,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 8 } },
        y: { grid: { color: '#21262d' } },
      },
    },
  });
}

function fmtDuration(ms) {
  if (!ms || ms <= 0) return '—';
  const m = Math.round(ms / 60000);
  if (m < 60) return `${m}m`;
  const h = Math.floor(m / 60), rm = m % 60;
  return rm ? `${h}h ${rm}m` : `${h}h`;
}

function fmtDate(ts) {
  if (!ts) return '—';
  return new Date(ts).toLocaleDateString(undefined, { month: 'short', day: 'numeric', year: 'numeric' });
}

function shortModel(m) {
  return m.replace('claude-', '').replace(/-\d{8}$/, '');
}

let _modalContextChart = null;
let _modalTurnsChart = null;

function openSessionDetail(session) {
  const modal = document.getElementById('session-modal');
  const turns = session.turns_raw || [];

  // Destroy old charts
  if (_modalContextChart) { _modalContextChart.destroy(); _modalContextChart = null; }
  if (_modalTurnsChart)   { _modalTurnsChart.destroy();   _modalTurnsChart = null; }

  // Header
  const models = Object.keys(session.models || {}).map(shortModel).join(', ') || '—';
  const duration = fmtDuration(session.end_ts - session.start_ts);
  const modeLabel = session.caveman
    ? `<span class="badge badge-caveman">caveman${session.caveman_mode ? ' · ' + session.caveman_mode : ''}</span>`
    : `<span class="badge badge-normal">normal</span>`;
  const sessionNameHtml = session.session_name
    ? `<div class="modal-session-name" title="${esc(session.session_name)}">${esc(session.session_name.slice(0, 120))}</div>`
    : '';
  const agentBadge = session.is_subagent
    ? ` <span class="badge badge-agent" title="Subagent of session ${esc(session.parent_session_id)}">${esc(session.agent_type || 'subagent')}</span>`
    : '';
  const reported = session.reported && session.reported.cost_usd !== undefined
    ? `<span title="Claude Code's own tally (cost-state), API list prices">API-equiv: <strong>${fmtUsd(session.reported.cost_usd)}</strong></span>`
    : '';
  const projEntries = Object.entries(session.projects || {}).sort((a, b) => b[1] - a[1]);
  const projTotal = projEntries.reduce((n, [, v]) => n + v, 0) || 1;
  const projectSplit = projEntries.length > 1
    ? `<div class="modal-meta" title="Cost tokens per project, attributed per turn from files touched (tags.toml / /tag)">
        <span>Projects:</span>
        ${projEntries.map(([p, v]) => `<span>${esc(shortProject(p))} <strong>${Math.round(v / projTotal * 100)}%</strong></span>`).join('')}
      </div>`
    : '';
  document.getElementById('modal-header').innerHTML = `
    <h2>${esc(shortProject(session.project_path))} &nbsp;${modeLabel}${agentBadge}</h2>
    ${sessionNameHtml}
    <div class="modal-meta">
      <span><strong>${fmtDate(session.start_ts)}</strong></span>
      <span>Duration: <strong>${duration}</strong></span>
      <span>Turns: <strong>${session.turns}</strong></span>
      <span>Cost tokens: <strong>${fmt(session.total_cost_tokens)}</strong></span>
      ${reported}
      <span>Cache hit: <strong>${(session.cache_hit_rate * 100).toFixed(1)}%</strong></span>
      <span>Model: <strong>${models}</strong></span>
    </div>
    ${projectSplit}
  `;

  // Educational callout — adapt message based on session characteristics
  const hitRate = session.cache_hit_rate;
  const avgCtx = turns.length
    ? Math.round(turns.reduce((s, t) => s + t.input + t.cache_read + t.cache_creation, 0) / turns.length)
    : 0;
  const firstCtx = turns[0] ? turns[0].input + turns[0].cache_read + turns[0].cache_creation : 0;
  const lastCtx  = turns[turns.length - 1]
    ? turns[turns.length - 1].input + turns[turns.length - 1].cache_read + turns[turns.length - 1].cache_creation
    : 0;
  const ctxGrowth = firstCtx > 0 ? Math.round((lastCtx / firstCtx - 1) * 100) : 0;

  let eduMsg = `Each turn, Claude processes the <strong>entire conversation history</strong> — not just your latest message. `;
  if (turns.length > 1 && ctxGrowth > 0) {
    eduMsg += `This session's context grew <strong>${ctxGrowth}×</strong> from turn 1 to turn ${turns.length}. `;
  }
  if (hitRate > 0.5) {
    eduMsg += `High cache hit rate (<strong>${(hitRate*100).toFixed(0)}%</strong>) means Claude re-used stored context blocks — paying ~10× less per cached token vs fresh input.`;
  } else if (hitRate > 0) {
    eduMsg += `Low cache hit rate (<strong>${(hitRate*100).toFixed(0)}%</strong>) — most context was read fresh. Longer sessions with repeated context amortize cache writes into cheaper reads.`;
  } else {
    eduMsg += `No cache hits — context was processed fresh each turn.`;
  }
  document.getElementById('modal-edu').innerHTML = eduMsg;

  // Context growth chart
  const turnLabels = turns.map((_, i) => `T${i + 1}`);
  const ctxPerTurn = turns.map(t => t.input + t.cache_read + t.cache_creation);
  _modalContextChart = new Chart(document.getElementById('modal-chart-context'), {
    type: 'line',
    data: {
      labels: turnLabels,
      datasets: [{
        label: 'Context size (tokens)',
        data: ctxPerTurn,
        borderColor: COLORS.teal,
        backgroundColor: COLORS.teal + '22',
        borderWidth: 2,
        pointRadius: turns.length > 30 ? 0 : 3,
        fill: true,
        tension: 0.3,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { display: false },
        tooltip: {
          callbacks: {
            label: (item) => ` Context: ${fmt(item.raw)} tokens`,
            afterLabel: (item) => {
              const t = turns[item.dataIndex];
              if (!t) return '';
              const cached = t.cache_read + t.cache_creation;
              const hitPct = cached > 0 ? Math.round(t.cache_read / cached * 100) : 0;
              return `  Fresh: ${fmt(t.input)}  Cached: ${fmt(t.cache_read)}  Cache hit: ${hitPct}%`;
            },
          },
        },
      },
      scales: {
        x: { grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        y: { grid: { color: '#21262d' }, ticks: { callback: fmt } },
      },
    },
  });

  // Per-turn composition stacked bar
  _modalTurnsChart = new Chart(document.getElementById('modal-chart-turns'), {
    type: 'bar',
    data: {
      labels: turnLabels,
      datasets: [
        {
          label: 'Fresh input',
          data: turns.map(t => t.input),
          backgroundColor: COLORS.blue + '99',
          borderColor: COLORS.blue,
          borderWidth: 0,
          stack: 'a',
        },
        {
          label: 'Cache write',
          data: turns.map(t => t.cache_creation),
          backgroundColor: COLORS.purple + '99',
          borderColor: COLORS.purple,
          borderWidth: 0,
          stack: 'a',
        },
        {
          label: 'Cache read',
          data: turns.map(t => t.cache_read),
          backgroundColor: COLORS.green + '66',
          borderColor: COLORS.green,
          borderWidth: 0,
          stack: 'a',
        },
        {
          label: 'Output',
          data: turns.map(t => t.output),
          backgroundColor: COLORS.orange + '99',
          borderColor: COLORS.orange,
          borderWidth: 0,
          stack: 'a',
        },
      ],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: {
        legend: { position: 'top', labels: { boxWidth: 10, font: { size: 11 } } },
        tooltip: {
          callbacks: {
            footer: (items) => `Cost: ${fmt(items.filter(i => i.datasetIndex !== 2).reduce((s, i) => s + i.raw, 0))} (cache reads excluded)`,
          },
        },
      },
      scales: {
        x: { stacked: true, grid: { display: false }, ticks: { maxTicksLimit: 12 } },
        y: { stacked: true, grid: { color: '#21262d' }, ticks: { callback: fmt } },
      },
    },
  });

  modal.hidden = false;
}

// Session table with client-side sort
function renderSessionsTable(sessions) {
  const wrap = document.getElementById('sessions-table-wrap');
  const PAGE = 25;
  let shown = PAGE;
  let sortKey = 'start_ts';
  let sortDir = -1; // -1 = desc

  const sortedSessions = () => [...sessions].sort((a, b) => {
    const av = a[sortKey] ?? 0, bv = b[sortKey] ?? 0;
    return sortDir * (av < bv ? -1 : av > bv ? 1 : 0);
  });

  const cols = [
    { key: 'start_ts',           label: 'Date',        render: s => fmtDate(s.start_ts) },
    { key: 'project_path',       label: 'Project',     render: s => {
      const proj = esc(shortProject(s.project_path));
      const name = s.session_name ? esc(s.session_name.slice(0, 60)) : '';
      const agent = s.is_subagent ? '<span class="badge badge-agent" title="Subagent transcript">agent</span> ' : '';
      const display = name ? `${agent}${proj}/<span class="session-name">${name}</span>` : agent + proj;
      const title = name ? `${s.project_path}\n${s.session_name}` : s.project_path;
      return `<span title="${esc(title)}">${display}</span>`;
    }},
    { key: 'turns',              label: 'Turns',       render: s => s.turns },
    { key: 'total_cost_tokens',  label: 'Cost Tokens', render: s => fmt(s.total_cost_tokens) },
    { key: 'cache_hit_rate',     label: 'Cache Hit',   render: s => (s.cache_hit_rate * 100).toFixed(1) + '%' },
    { key: 'avg_tokens_per_turn',label: 'Avg/Turn',    render: s => fmt(s.avg_tokens_per_turn) },
    { key: 'caveman',            label: 'Mode',        render: s => s.caveman
        ? `<span class="badge badge-caveman">${s.caveman_mode || 'caveman'}</span>`
        : `<span class="badge badge-normal">normal</span>` },
  ];

  function render() {
    const rows = sortedSessions().slice(0, shown);
    const total = sessions.length;

    const thead = cols.map(c => {
      const cls = sortKey === c.key ? (sortDir === 1 ? 'sort-asc' : 'sort-desc') : '';
      return `<th data-key="${c.key}" class="${cls}">${c.label}</th>`;
    }).join('');

    const tbody = rows.map(s => `
      <tr data-sid="${esc(s.session_id)}">
        ${cols.map(c => `<td>${c.render(s)}</td>`).join('')}
      </tr>
    `).join('');

    const moreBtn = shown < total
      ? `<button class="sessions-show-more" id="sessions-more">Show more (${total - shown} remaining)</button>`
      : '';

    wrap.innerHTML = `
      <table class="sessions-table">
        <thead><tr>${thead}</tr></thead>
        <tbody>${tbody}</tbody>
      </table>
      ${moreBtn}
    `;

    // Sort click
    wrap.querySelectorAll('th[data-key]').forEach(th => {
      th.addEventListener('click', () => {
        const k = th.dataset.key;
        if (sortKey === k) sortDir *= -1;
        else { sortKey = k; sortDir = -1; }
        render();
      });
    });

    // Row click → detail modal
    wrap.querySelectorAll('tbody tr').forEach(tr => {
      tr.addEventListener('click', () => {
        const sid = tr.dataset.sid;
        const s = sessions.find(x => x.session_id === sid);
        if (s) openSessionDetail(s);
      });
    });

    // Show more
    document.getElementById('sessions-more')?.addEventListener('click', () => {
      shown += PAGE;
      render();
    });
  }

  render();
}

async function main() {
  let data;
  try {
    const res = await fetch('/stats.json');
    if (!res.ok) throw new Error(`HTTP ${res.status}`);
    data = await res.json();
  } catch (e) {
    document.body.innerHTML = `<div style="padding:40px;color:#f85149">
      Failed to load stats.json: ${e.message}<br><br>
      Run: <code>python3 parser/parse.py --out stats.json</code>
    </div>`;
    return;
  }

  const ts = new Date(data.generated_at).toLocaleString();
  document.getElementById('generated-at').textContent = `Updated ${ts}`;

  const softLimitInput = document.getElementById('soft-limit');
  const planSelect = document.getElementById('plan-select');
  const planInfo = data.plan_info || {};
  const planLimits = planInfo.limits || { standard: 600000, ultraplan_5x: 3000000, ultraplan_20x: 12000000 };

  // Measured limit from statusline quota snapshots beats any plan guess
  const implied5h = data.quota?.five_hour?.implied_limit_cost_tokens;
  if (implied5h) {
    planLimits.statusline = implied5h;
    const opt = document.createElement('option');
    opt.value = 'statusline';
    opt.textContent = `Measured (~${fmt(implied5h)})`;
    opt.title = 'Implied from real 5h quota % captured by extras/statusline-tee.sh';
    planSelect.prepend(opt);
  }

  // Auto-select plan from detected tier
  if (implied5h) {
    planSelect.value = 'statusline';
  } else if (planInfo.detected) {
    planSelect.value = planInfo.ultraplan ? 'ultraplan_5x' : 'standard';
  } else {
    planSelect.value = 'custom';
  }

  // Set initial soft limit from plan selection (or historical peak if custom)
  function applyPlanLimit() {
    const v = planSelect.value;
    if (v === 'custom') {
      if (data.rolling.suggested_limit) softLimitInput.value = data.rolling.suggested_limit;
    } else {
      softLimitInput.value = planLimits[v] || data.rolling.suggested_limit || 1_100_000;
    }
    softLimitInput.disabled = (v !== 'custom');
  }
  applyPlanLimit();

  const getSoftLimit = () => parseInt(softLimitInput.value, 10) || 1_100_000;

  function reRenderRolling() {
    document.getElementById('rolling-grid').innerHTML = '';
    Chart.getChart(document.getElementById('chart-sparkline'))?.destroy();
    document.getElementById('rolling-panel').querySelector('div[style*="margin-top:8px"]')?.remove();
    renderRolling(data.rolling, getSoftLimit());
  }

  renderQuota(data.quota);
  renderCards(data.totals, data.lifetime);
  renderLifetime(data.lifetime);
  renderRolling(data.rolling, getSoftLimit());

  planSelect.addEventListener('change', () => {
    applyPlanLimit();
    reRenderRolling();
  });

  softLimitInput.addEventListener('change', () => {
    reRenderRolling();
  });

  renderCavemanComparison(data);
  renderCavemanAdoption(data.by_day);
  renderDaily(data.by_day, data.lifetime && data.lifetime.rolling_7d_average);
  renderByProject(data);
  renderImpact(data.impact);
  renderModels(data.sessions);
  renderCache(data.totals);
  renderSessionsTable([...data.sessions].reverse()); // newest first default

  // Modal close
  document.getElementById('modal-close').addEventListener('click', () => {
    document.getElementById('session-modal').hidden = true;
  });
  document.getElementById('session-modal').addEventListener('click', (e) => {
    if (e.target === e.currentTarget) e.currentTarget.hidden = true;
  });
  document.addEventListener('keydown', (e) => {
    if (e.key === 'Escape') document.getElementById('session-modal').hidden = true;
  });
}

main();

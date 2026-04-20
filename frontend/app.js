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

function shortProject(path) {
  if (!path) return 'unknown';
  const parts = path.split('/');
  return parts[parts.length - 1] || parts[parts.length - 2] || path;
}

function renderCards(totals) {
  const cards = [
    { label: 'Sessions',     value: totals.sessions,                        sub: null },
    { label: 'Turns',        value: fmt(totals.turns),                      sub: null },
    { label: 'Cost Tokens',  value: fmt(totals.total_cost_tokens),           sub: 'input + output + cache writes' },
    { label: 'Cache Hit',    value: (totals.cache_hit_rate * 100).toFixed(1) + '%', sub: 'reads / (reads + writes)' },
    { label: 'Output Tokens',value: fmt(totals.output_tokens),               sub: null },
    { label: 'Cache Reads',  value: fmt(totals.cache_read_tokens),           sub: null },
  ];
  document.getElementById('cards').innerHTML = cards.map(c => `
    <div class="card">
      <div class="label">${c.label}</div>
      <div class="value">${c.value}</div>
      ${c.sub ? `<div class="sub">${c.sub}</div>` : ''}
    </div>
  `).join('');
}

function renderCavemanComparison(cc) {
  const { caveman, normal, savings_pct } = cc;

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

  new Chart(document.getElementById('chart-caveman'), {
    type: 'bar',
    data: {
      labels: ['Normal', 'Caveman'],
      datasets: [{
        label: 'Avg tokens/turn',
        data: [normal.avg_tokens_per_turn, caveman.avg_tokens_per_turn],
        backgroundColor: [COLORS.blue + '99', COLORS.orange + '99'],
        borderColor: [COLORS.blue, COLORS.orange],
        borderWidth: 1,
        borderRadius: 4,
      }],
    },
    options: {
      responsive: true,
      maintainAspectRatio: false,
      plugins: { legend: { display: false } },
      scales: {
        y: { beginAtZero: true, grid: { color: '#21262d' } },
        x: { grid: { display: false } },
      },
    },
  });
}

function renderDaily(byDay) {
  const days = Object.keys(byDay).slice(-60); // last 60 days
  const data = days.map(d => byDay[d]);

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

function renderByProject(byProject) {
  const entries = Object.entries(byProject)
    .sort((a, b) => b[1].total_cost_tokens - a[1].total_cost_tokens)
    .slice(0, 15);

  const labels = entries.map(([p]) => shortProject(p));
  const costData = entries.map(([, d]) => d.total_cost_tokens);
  const cavemanData = entries.map(([, d]) => d.caveman_sessions);

  new Chart(document.getElementById('chart-project'), {
    type: 'bar',
    data: {
      labels,
      datasets: [
        {
          label: 'Cost Tokens',
          data: costData,
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
      plugins: { legend: { display: false } },
      scales: {
        x: { grid: { color: '#21262d' } },
        y: { grid: { display: false } },
      },
    },
  });
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
  // Auto-set to suggested limit derived from historical peak
  if (data.rolling.suggested_limit) {
    softLimitInput.value = data.rolling.suggested_limit;
  }
  const getSoftLimit = () => parseInt(softLimitInput.value, 10) || 1_100_000;

  renderCards(data.totals);
  renderRolling(data.rolling, getSoftLimit());

  softLimitInput.addEventListener('change', () => {
    // re-render rolling panel on limit change
    document.getElementById('rolling-grid').innerHTML = '';
    const old = document.getElementById('chart-sparkline');
    Chart.getChart(old)?.destroy();
    const projMsg = document.getElementById('rolling-panel').querySelector('div[style*="margin-top:8px"]');
    if (projMsg) projMsg.remove();
    renderRolling(data.rolling, getSoftLimit());
  });

  renderCavemanComparison(data.caveman_comparison);
  renderDaily(data.by_day);
  renderByProject(data.by_project);
  renderModels(data.sessions);
  renderCache(data.totals);
}

main();

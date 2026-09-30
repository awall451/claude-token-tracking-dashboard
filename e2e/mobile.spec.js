// Phone-viewport audit: the dashboard must fit the viewport at every project (no
// horizontal page scroll — wide tables scroll inside their own wrapper), controls
// must be big enough to tap, and the session modal must stay on screen.
// Each test drops a screenshot in e2e/shots/<project>/ for eyeballing.
import { test, expect } from '@playwright/test';
import fs from 'node:fs';
import path from 'node:path';

const SHOTS = path.join(process.cwd(), 'shots');
const MIN_TAP = 40; // px; Apple HIG 44, Material 48 — 40 leaves room for borders

const isMobile = (project) => project !== 'desktop';

// Each route and the element that proves it rendered.
const ROUTES = [
  { hash: '#/projects', name: 'projects', ready: '#projects-table-wrap tbody tr' },
  { hash: '#/sessions', name: 'sessions', ready: '#sessions-table-wrap tbody tr' },
  { hash: '#/usage',    name: 'usage',    ready: '#cards .card' },
  { hash: '#/caveman',  name: 'caveman',  ready: '#caveman-panel' },
];

async function load(page, hash = '#/usage', ready = '#cards .card') {
  await page.goto('/' + hash);
  await expect(page.locator(ready).first()).toBeVisible({ timeout: 15000 });
  await page.waitForTimeout(500);
}

async function shot(page, project, name, fullPage = false) {
  const dir = path.join(SHOTS, project);
  fs.mkdirSync(dir, { recursive: true });
  await page.screenshot({ path: path.join(dir, `${name}.png`), fullPage });
}

async function overflow(page) {
  return page.evaluate(() => {
    const el = document.documentElement;
    return Math.max(0, el.scrollWidth - el.clientWidth);
  });
}

// Elements that poke outside the viewport horizontally, skipping content that
// lives inside an intentional horizontal scroller (.scroll-x).
async function offenders(page) {
  return page.evaluate(() => {
    const vw = document.documentElement.clientWidth;
    const out = [];
    for (const el of document.querySelectorAll('body *')) {
      if (el.closest('.scroll-x') && !el.classList.contains('scroll-x')) continue;
      const r = el.getBoundingClientRect();
      if (r.width === 0) continue;
      if (r.right > vw + 1 || r.left < -1) {
        const id = el.id ? '#' + el.id : '';
        const cls = typeof el.className === 'string' && el.className.trim() ? '.' + el.className.trim().split(/\s+/).join('.') : '';
        out.push(`${el.tagName.toLowerCase()}${id}${cls} [${Math.round(r.left)}..${Math.round(r.right)}]`);
      }
      if (out.length >= 10) break;
    }
    return out;
  });
}

async function expectFits(page, label) {
  const ov = await overflow(page);
  const who = await offenders(page);
  expect(ov, `${label}: ${ov}px horizontal page overflow; offenders:\n  ${who.join('\n  ')}`).toBe(0);
  expect(who, `${label}: elements outside the viewport`).toEqual([]);
}

async function expectTappable(page, selector, label) {
  const small = await page.evaluate(({ selector, min }) => {
    const bad = [];
    for (const el of document.querySelectorAll(selector)) {
      const r = el.getBoundingClientRect();
      if (r.width === 0 || r.height === 0) continue;
      if (r.height < min || r.width < min) bad.push(`${el.id || el.textContent.trim().slice(0, 20) || el.tagName} ${Math.round(r.width)}x${Math.round(r.height)}`);
    }
    return bad;
  }, { selector, min: MIN_TAP });
  expect(small, `${label}: tap targets under ${MIN_TAP}px:\n  ${small.join('\n  ')}`).toEqual([]);
}

for (const r of ROUTES) {
  test(`${r.name} fits the viewport`, async ({ page }, info) => {
    await load(page, r.hash, r.ready);
    await shot(page, info.project.name, r.name);
    await shot(page, info.project.name, `${r.name}-full`, true);
    await expectFits(page, r.name);
  });
}

test('project drill-down fits, and back returns to the list', async ({ page }, info) => {
  await load(page, '#/projects', '#projects-table-wrap tbody tr');
  const row = page.locator('#projects-table-wrap tbody tr').first();
  const name = await row.getAttribute('data-project');
  await row.click();
  await expect(page).toHaveURL(/#\/project\//);
  await expect(page.locator('#project-title')).toBeVisible();
  await expect(page.locator('#project-title')).toHaveAttribute('title', name);
  await page.waitForTimeout(500);
  await shot(page, info.project.name, 'project-detail');
  await expectFits(page, 'project-detail');
  if (isMobile(info.project.name)) await expectTappable(page, '#project-view .seg-btn, #project-view .back', 'project controls');
  // Full-page capture briefly drops touch emulation (pointer: coarse), so measure first.
  await shot(page, info.project.name, 'project-detail-full', true);
  await page.goBack();
  await expect(page).toHaveURL(/#\/projects/);
  await expect(page.locator('#projects-table-wrap tbody tr').first()).toBeVisible();
});

test('tabs switch views and are tappable', async ({ page }, info) => {
  await load(page, '#/projects', '#projects-table-wrap tbody tr');
  if (isMobile(info.project.name)) await expectTappable(page, '#tabs a', 'tabs');
  for (const [tab, ready] of [['Usage', '#cards .card'], ['Caveman', '#caveman-panel'], ['Sessions', '#sessions-table-wrap tbody tr'], ['Projects', '#projects-table-wrap']]) {
    await page.locator('#tabs a', { hasText: tab }).click();
    await expect(page.locator(ready).first()).toBeVisible();
    await expect(page.locator('#tabs a.active')).toHaveText(tab);
  }
});

test('range control changes the projects range', async ({ page }) => {
  await load(page, '#/projects', '#projects-table-wrap tbody tr');
  await page.locator('#projects-view .seg-btn', { hasText: 'All' }).click();
  await expect(page).toHaveURL(/range=all/);
  await expect(page.locator('#projects-view .seg-btn.active')).toHaveText('All');
});

test('controls are tappable on phones', async ({ page }, info) => {
  test.skip(!isMobile(info.project.name), 'phones only');
  await load(page, '#/usage');
  await expectTappable(page, '#plan-select, #soft-limit, #project-groupby', 'usage controls');
  await load(page, '#/projects', '#projects-table-wrap tbody tr');
  await expectTappable(page, '#projects-group, #projects-view .seg-btn', 'projects controls');
  await load(page, '#/sessions', '#sessions-table-wrap tbody tr');
  await expectTappable(page, '#sessions-project, .sessions-show-more', 'sessions controls');
});

test('wide tables scroll inside their own wrapper', async ({ page }) => {
  for (const [hash, ready, sel] of [['#/sessions', '#sessions-table-wrap tbody tr', '#sessions-table-wrap'],
                                    ['#/usage', '#cards .card', '#impact-table-wrap'],
                                    ['#/projects', '#projects-table-wrap tbody tr', '#projects-table-wrap']]) {
    await load(page, hash, ready);
    const wrap = page.locator(sel);
    await wrap.scrollIntoViewIfNeeded();
    const box = await wrap.boundingBox();
    const vw = page.viewportSize().width;
    expect(box.x, `${sel} left edge`).toBeGreaterThanOrEqual(0);
    expect(box.x + box.width, `${sel} right edge`).toBeLessThanOrEqual(vw);
  }
});

test('session modal fits and closes', async ({ page }, info) => {
  await load(page, '#/sessions', '#sessions-table-wrap tbody tr');
  const row = page.locator('#sessions-table-wrap tbody tr').first();
  await row.scrollIntoViewIfNeeded();
  await row.click();
  const modal = page.locator('#session-modal');
  await expect(modal).toBeVisible();
  await page.waitForTimeout(400);
  await shot(page, info.project.name, 'session-modal');
  await expectFits(page, 'session-modal');
  const box = await page.locator('#session-modal > *').first().boundingBox();
  const vp = page.viewportSize();
  expect(box.x, 'modal left edge').toBeGreaterThanOrEqual(0);
  expect(box.x + box.width, 'modal right edge').toBeLessThanOrEqual(vp.width);
  if (isMobile(info.project.name)) await expectTappable(page, '#modal-close', 'modal close');
  await page.locator('#modal-close').click();
  await expect(modal).toBeHidden();
});

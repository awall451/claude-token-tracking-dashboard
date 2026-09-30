// Tier 3 — Playwright phone-viewport audit (same pattern as gantry).
//
// Nothing here starts a server. Point BASE_URL at whatever is running:
//   docker compose up -d            → http://localhost:9420 (default)
//   python3 server/serve.py         → same
//
// Screenshots land in e2e/shots/<project>/ (gitignored).
import { defineConfig, devices } from '@playwright/test';

export default defineConfig({
  testDir: '.',
  outputDir: 'test-results',
  fullyParallel: false,
  workers: 2,
  retries: 0,
  reporter: [['list']],
  use: {
    baseURL: process.env.BASE_URL ?? 'http://localhost:9420',
    trace: 'off',
    video: 'off',
    screenshot: 'off',
  },
  projects: [
    // iPhone presets default to WebKit; we only use Chromium and audit layout.
    { name: 'iphone-portrait',  use: { ...devices['iPhone 14'],           browserName: 'chromium' } },
    { name: 'iphone-landscape', use: { ...devices['iPhone 14 landscape'], browserName: 'chromium' } },
    { name: 'pixel-portrait',   use: { ...devices['Pixel 7'] } },
    { name: 'pixel-landscape',  use: { ...devices['Pixel 7 landscape'] } },
    { name: 'desktop',          use: { ...devices['Desktop Chrome'], viewport: { width: 1280, height: 800 } } },
  ],
});

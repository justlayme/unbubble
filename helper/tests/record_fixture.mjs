// Scrolls a rendered thread at iPhone screen-recording resolution (393 x 852 pt
// at 2.25x = 884 x 1917 px) the way a person flicks back through Messages:
// starts at the newest message, flicks upward with momentum, pauses, sometimes
// nudges back down. Writes one PNG per recorded frame (30 fps).
//
//   node tests/record_fixture.mjs DIR [seed]
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

const dir = resolve(process.argv[2]);
let seed = Number(process.argv[3] ?? 3);
const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
mkdirSync(join(dir, "frames"), { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM ?? "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 393, height: 852 }, deviceScaleFactor: 2.25 });
await page.goto("file://" + join(dir, "chat.html"));
await page.evaluate(() => document.fonts.ready);
const max = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
let y = max;
let frame = 0;
const shot = async () => {
  await page.evaluate((v) => window.scrollTo(0, v), Math.round(y));
  await page.screenshot({ path: join(dir, "frames", `f${String(frame++).padStart(5, "0")}.png`) });
};
// Hold on the newest messages first, as a real recording opens.
for (let i = 0; i < 12; i++) await shot();
while (y > 0) {
  // A flick: 300-900 pt of travel, fast start, decelerating.
  const travel = 300 + rand() * 600;
  const frames = 14 + Math.floor(rand() * 16);
  const start = y;
  for (let i = 1; i <= frames; i++) {
    const t = i / frames;
    y = Math.max(0, start - travel * (1 - Math.pow(1 - t, 3)));
    await shot();
    if (y === 0) break;
  }
  // Sometimes nudge back down a little, as people do to reread.
  if (rand() < 0.15 && y > 0) {
    const back = 80 + rand() * 120;
    for (let i = 1; i <= 8; i++) { y = Math.min(max, y + back / 8); await shot(); }
  }
  const pause = 3 + Math.floor(rand() * 10);
  for (let i = 0; i < pause; i++) await shot();
}
for (let i = 0; i < 15; i++) await shot();
console.log(`${frame} frames, scroll height ${max} pt`);
await browser.close();

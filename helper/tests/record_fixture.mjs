// Scrolls a rendered thread at iPhone screen-recording resolution (393 x 852 pt
// at 2.25x = 884 x 1917 px) the way a person flicks back through Messages:
// starts at the newest message, flicks upward with momentum, pauses, sometimes
// nudges back down. Writes one PNG per recorded frame (30 fps).
//
//   node tests/record_fixture.mjs DIR [seed] [--dpr 3] [--cc] [--banner]
//
// --cc     starts and ends on Control Center, as every real screen recording does
// --banner drops a notification banner over the header partway through
import { chromium } from "playwright";
import { mkdirSync } from "node:fs";
import { join, resolve } from "node:path";

const argv = process.argv.slice(2);
const flag = (n) => argv.includes(n);
const opt = (n, d) => (argv.includes(n) ? argv[argv.indexOf(n) + 1] : d);
const dir = resolve(argv[0]);
let seed = Number(argv[1] && !argv[1].startsWith("--") ? argv[1] : 3);
const dpr = Number(opt("--dpr", 2.25));
const rand = () => ((seed = (seed * 16807) % 2147483647) / 2147483647);
mkdirSync(join(dir, "frames"), { recursive: true });

const browser = await chromium.launch({ executablePath: process.env.CHROMIUM ?? "/opt/pw-browsers/chromium" });
const page = await browser.newPage({ viewport: { width: 393, height: 852 }, deviceScaleFactor: dpr });
await page.goto("file://" + join(dir, "chat.html"));
await page.evaluate(() => document.fonts.ready);
const max = await page.evaluate(() => document.documentElement.scrollHeight - innerHeight);
let y = max;
let frame = 0;
// Overlays a real recording catches: Control Center (start and end) and a notification banner.
await page.addStyleTag({ content: `
  #cc { position:fixed; inset:0; z-index:50; background:rgba(20,20,22,.55); backdrop-filter:blur(24px) saturate(1.4);
        display:grid; grid-template-columns:repeat(4, 1fr); gap:14px; padding:90px 26px; align-content:start; transition:none; }
  #cc i { height:70px; border-radius:18px; background:rgba(120,120,128,.55); color:#fff; font:500 12px/1.2 Inter; padding:10px; font-style:normal; }
  #cc i.big { grid-column:span 2; height:154px; } #cc i.rec { background:#e8423f; }
  #banner { position:fixed; z-index:40; top:10px; left:8px; right:8px; height:74px; border-radius:22px; background:rgba(242,242,247,.92);
        backdrop-filter:blur(20px); box-shadow:0 8px 30px rgba(0,0,0,.18); font:400 15px/1.3 Inter; padding:14px 16px 0 66px; color:#000; }
  #banner::before { content:""; position:absolute; left:14px; top:15px; width:40px; height:40px; border-radius:10px; background:#34c759; }
` });
const setOverlay = (id, html, style) => page.evaluate(([id, html, style]) => {
  let el = document.getElementById(id);
  if (html === null) { el?.remove(); return; }
  if (!el) { el = document.createElement("div"); el.id = id; document.body.append(el); el.innerHTML = html; }
  el.style.cssText = style;
}, [id, html, style]);
const CC = '<i class="big">Wi‑Fi  Bluetooth  AirDrop</i><i class="big">Now Playing</i><i>Focus</i><i>Mirror</i><i class="rec">Screen Recording</i><i>Torch</i><i>Timer</i><i>Camera</i><i>Calc</i><i>Brightness</i>';
const shot = async () => {
  await page.evaluate((v) => window.scrollTo(0, v), Math.round(y));
  await page.screenshot({ path: join(dir, "frames", `f${String(frame++).padStart(5, "0")}.png`) });
};
if (flag("--cc")) {
  // The recording starts with Control Center open, then it is swiped away.
  for (let i = 0; i < 18; i++) { await setOverlay("cc", CC, ""); await shot(); }
  for (let i = 1; i <= 9; i++) { await setOverlay("cc", CC, `transform:translateY(${-100 * (i / 9) ** 2}%)`); await shot(); }
  await setOverlay("cc", null);
}
// Hold on the newest messages first, as a real recording opens.
for (let i = 0; i < 12; i++) await shot();
let bannerAt = flag("--banner") ? 60 : -1;
while (y > 0) {
  // A flick: 300-900 pt of travel, fast start, decelerating.
  const travel = 300 + rand() * 600;
  const frames = 14 + Math.floor(rand() * 16);
  const start = y;
  for (let i = 1; i <= frames; i++) {
    const t = i / frames;
    y = Math.max(0, start - travel * (1 - Math.pow(1 - t, 3)));
    if (bannerAt >= 0 && frame >= bannerAt && frame < bannerAt + 50) {
      const k = Math.min(1, (frame - bannerAt) / 6, (bannerAt + 50 - frame) / 6);
      await setOverlay("banner", "<b>Alexis</b><br>omw, 10 min", `transform:translateY(${(k - 1) * 110}px)`);
    } else await setOverlay("banner", null);
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
if (flag("--cc")) {
  // ...and ends by pulling Control Center down to stop it.
  for (let i = 1; i <= 9; i++) { await setOverlay("cc", CC, `transform:translateY(${-100 * (1 - i / 9) ** 2}%)`); await shot(); }
  for (let i = 0; i < 14; i++) await shot();
}
console.log(`${frame} frames, scroll height ${max} pt`);
await browser.close();

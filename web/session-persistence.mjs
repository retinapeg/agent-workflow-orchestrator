// Prove a trusted device pairs once: survive browser close/reopen, a controller restart,
// and a Tailscale reconnect without a new code. Real service, real HTTPS origin.
import {chromium} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {mkdirSync, writeFileSync} from 'node:fs';

const root = process.cwd();
const url = process.env.ARENA_URL || 'https://leonards-macbook-air-1.tail11ad03.ts.net';
const home = process.env.ARENA_HOME || root + '/.arena-control';
const label = process.env.ARENA_SERVICE_LABEL || 'local.agent-arena.control.1a6834e84b5a';
const evidence = root + '/docs/build-evidence/session-persistence';
mkdirSync(evidence, {recursive: true});
const steps = [];
const log = (m) => { const line = new Date().toISOString() + ' ' + m; console.log(line); steps.push(line); };
const control = (...a) => execFileSync(root + '/.venv/bin/python', ['-m', 'agent_arena.control', '--home', home, ...a], {cwd: root, encoding: 'utf8'}).trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const phone = {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true};

async function openPaired(storageState, expectHeading = true) {
  const browser = await chromium.launch();
  const context = await browser.newContext({...phone, storageState});
  const page = await context.newPage();
  await page.goto(url);
  if (expectHeading) await page.getByRole('heading', {name: 'Build control.', exact: true}).waitFor({timeout: 60000});
  return {browser, context, page};
}
async function waitUp() {
  for (let n = 0; n < 90; n++) {
    try { const r = await fetch(url + '/api/v1/overview'); if (r.status === 401) return n; } catch {}
    await sleep(1000);
  }
  throw Error('service did not come back');
}

let browser;
try {
  // 1. Pair once with a one-time code.
  const code = control('pair');
  browser = await chromium.launch();
  let context = await browser.newContext(phone);
  let page = await context.newPage();
  await page.goto(url);
  await page.getByLabel('One-time pairing code').fill(code);
  await page.getByRole('button', {name: 'Pair device', exact: true}).click();
  await page.getByRole('navigation',{name:'Primary'}).waitFor();await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:'Overview',exact:true}).click().catch(()=>{});await page.getByRole('heading', {name: 'Build control.', exact: true}).waitFor();
  const cookies = await context.cookies();
  const session = cookies.find((c) => c.name === 'arena_session');
  if (!session || !session.httpOnly || !session.secure || session.sameSite !== 'Strict') throw Error('cookie attributes wrong: ' + JSON.stringify(session));
  const days = (session.expires * 1000 - Date.now()) / 86400000;
  if (days < 29) throw Error('cookie lifetime is ' + days.toFixed(1) + ' days');
  log(`Paired once; cookie HttpOnly+Secure+SameSite=Strict, expires in ${days.toFixed(1)} days`);
  const storageState = await context.storageState();
  await page.screenshot({path: evidence + '/01-paired.png', fullPage: false});
  await browser.close();

  // 2. Close and reopen the browser: no code asked.
  ({browser, context, page} = await openPaired(storageState));
  log('Reopened browser: paired without a code');
  await page.screenshot({path: evidence + '/02-reopened.png', fullPage: false});
  await browser.close();

  // 3. Restart the controller, reopen: still paired.
  execFileSync('launchctl', ['kickstart', '-k', `gui/${process.getuid()}/${label}`]);
  const waited = await waitUp();
  ({browser, context, page} = await openPaired(storageState));
  log(`Controller restarted (service back after ${waited}s): paired without a code`);
  await page.screenshot({path: evidence + '/03-after-restart.png', fullPage: false});
  await browser.close();

  // 4. Drop and re-establish Tailscale, reopen: still paired.
  execFileSync('tailscale', ['down']);
  await sleep(3000);
  execFileSync('tailscale', ['up', '--accept-routes', '--timeout=60s']);
  const back = await waitUp();
  ({browser, context, page} = await openPaired(storageState));
  log(`Tailscale reconnected (origin reachable after ${back}s): paired without a code`);
  await page.screenshot({path: evidence + '/04-after-tailscale-reconnect.png', fullPage: false});

  // 5. Device list shows this device; explicit unpair ends the session.
  await page.getByRole('button', {name: 'Device session'}).click();
  await page.getByText('Paired devices').waitFor();
  await page.screenshot({path: evidence + '/05-devices.png', fullPage: true});
  await page.getByRole('button', {name: 'Unpair this device'}).click();
  await page.getByLabel('One-time pairing code').waitFor();
  const after = await page.request.get(url + '/api/v1/overview');
  if (after.status() !== 401) throw Error('session survived logout');
  log('Unpair ends the session immediately (401 afterwards)');
  writeFileSync(evidence + '/result.json', JSON.stringify({passed: true, url, cookie: {httpOnly: session.httpOnly, secure: session.secure, sameSite: session.sameSite, days: Number(days.toFixed(1))}, steps}, null, 2));
  console.log('SESSION_PERSISTENCE_PASSED');
} catch (e) {
  writeFileSync(evidence + '/result.json', JSON.stringify({passed: false, error: String(e), steps}, null, 2));
  throw e;
} finally {
  if (browser) await browser.close().catch(() => {});
}

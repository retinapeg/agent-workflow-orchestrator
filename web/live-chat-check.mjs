// Live acceptance of the conversational director on the real HTTPS origin:
// questions, GitHub exploration, project inspection, multi-turn planning, explicit execution,
// close/reopen persistence, and the resulting build. Real director, real agents.
import {chromium} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {appendFileSync, mkdirSync, writeFileSync} from 'node:fs';

const root = process.cwd();
const url = process.env.ARENA_URL || 'https://leonards-macbook-air-1.tail11ad03.ts.net';
const out = process.env.ARENA_EVIDENCE || 'docs/build-evidence/chat-acceptance';
const turns = JSON.parse(process.env.ARENA_TURNS || '[]');
const execute = process.env.ARENA_EXECUTE || '';
const waitBuild = process.env.ARENA_WAIT_BUILD === '1';
mkdirSync(out, {recursive: true});
const log = (m) => { const line = new Date().toISOString() + ' ' + m; console.log(line); appendFileSync(out + '/timeline.log', line + '\n'); };
const control = (...a) => execFileSync(root + '/.venv/bin/python', ['-m', 'agent_arena.control', '--home', root + '/.arena-control', ...a], {cwd: root, encoding: 'utf8'}).trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const phone = {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true};
const result = {url, turns: [], passed: false};
let browser = await chromium.launch();
let shots = 0;
async function shot(page, name) { shots += 1; await page.screenshot({path: `${out}/${String(shots).padStart(2, '0')}-${name}.png`, fullPage: false}); }
async function send(page, text) {
  const t0 = Date.now();
  await page.getByLabel('Message Arena').fill(text);
  await page.getByRole('button', {name: 'Send', exact: true}).click();
  // Wait for a new assistant message after this user message.
  const conv = () => page.evaluate(async () => {
    const id = localStorage.getItem('arena.conversation');
    if (!id) return null;
    const r = await fetch('/api/v1/conversations/' + id, {cache: 'no-store'});
    return r.ok ? await r.json() : null;
  });
  let detail = null;
  for (let n = 0; n < 300; n++) {
    await sleep(2000);
    detail = await conv();
    const msgs = detail?.messages || [];
    const lastUser = [...msgs].reverse().find((m) => m.role === 'user' && m.text === text);
    const reply = lastUser ? msgs.find((m) => m.role === 'assistant' && m.id > lastUser.id) : null;
    if (reply) {
      const sec = ((Date.now() - t0) / 1000).toFixed(0);
      log(`Q: ${text}\n   A (${reply.model || '?'} · ${sec}s${reply.inspected ? ' · inspected ' + reply.inspected : ''}${reply.project_selected ? ' · selected ' + reply.project_selected : ''}${reply.build_started ? ' · BUILD STARTED ' + reply.goal_id : ''}${reply.offer_build ? ' · offers build' : ''}${reply.launch_error ? ' · launch_error ' + reply.launch_error : ''}): ${reply.text.replace(/\s+/g, ' ').slice(0, 400)}`);
      result.turns.push({question: text, reply: reply.text, meta: {model: reply.model, seconds: Number(sec), inspected: reply.inspected, project_selected: reply.project_selected, build_started: reply.build_started, goal_id: reply.goal_id, offer_build: reply.offer_build, error: reply.error}});
      return {reply, detail};
    }
  }
  throw Error('No reply to: ' + text);
}
try {
  const code = control('pair');
  let context = await browser.newContext(phone);
  let page = await context.newPage();
  const errors = [];
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(url);
  await page.getByLabel('One-time pairing code').fill(code);
  await page.getByRole('button', {name: 'Pair device', exact: true}).click();
  await page.getByRole('navigation', {name: 'Primary'}).waitFor();
  await page.getByRole('navigation', {name: 'Primary'}).getByRole('button', {name: 'Chat', exact: true}).click();
  await page.getByLabel('Message Arena').waitFor();
  await page.getByRole('button', {name: 'New chat', exact: true}).click().catch(() => {});
  await sleep(500);
  await shot(page, 'chat-empty');
  let last = null;
  for (const text of turns) {
    last = await send(page, text);
    await shot(page, 'turn');
  }
  // Close the app and come back: the thread, project and spec must be there.
  const storage = await context.storageState();
  const ls = await page.evaluate(() => ({...localStorage}));
  await browser.close();
  log('Browser closed');
  await sleep(5000);
  browser = await chromium.launch();
  context = await browser.newContext({...phone, storageState: storage});
  page = await context.newPage();
  await page.addInitScript((kv) => { for (const [k, v] of Object.entries(kv)) localStorage.setItem(k, v); }, ls);
  await page.goto(url);
  await page.getByRole('navigation', {name: 'Primary'}).getByRole('button', {name: 'Chat', exact: true}).click();
  await page.getByLabel('Message Arena').waitFor();
  await sleep(1500);
  const restored = await page.evaluate(async () => {
    const id = localStorage.getItem('arena.conversation');
    const r = await fetch('/api/v1/conversations/' + id, {cache: 'no-store'});
    return r.ok ? await r.json() : null;
  });
  result.restored = {messages: restored?.messages?.length || 0, project: restored?.project?.name || null, spec: !!restored?.spec, plan_tasks: (restored?.spec?.plan?.milestones || []).reduce((n, m) => n + (m.tasks || []).length, 0)};
  log(`Reopened: ${result.restored.messages} messages restored, project ${result.restored.project}, plan tasks ${result.restored.plan_tasks}`);
  await shot(page, 'reopened');
  if (execute) {
    last = await send(page, execute);
    await shot(page, 'execute');
    if (!last.reply.build_started) throw Error('Execution was not started: ' + (last.reply.launch_error || last.reply.text.slice(0, 200)));
    result.goal_id = last.reply.goal_id;
    if (waitBuild) {
      await page.getByRole('navigation', {name: 'Primary'}).getByRole('button', {name: 'Build', exact: true}).click();
      await sleep(1500);
      await shot(page, 'build-started');
      const t0 = Date.now();
      let states = '';
      while (Date.now() - t0 < 2400000) {
        const snap = await page.evaluate(async () => await (await fetch('/api/v1/overview', {cache: 'no-store'})).json());
        const tasks = snap.tasks.filter((t) => t.goal_id === result.goal_id);
        const s = tasks.map((t) => `${t.milestone || t.phase}:${t.state}`).join(' | ');
        if (s !== states) { log(s); states = s; await shot(page, 'build'); }
        const builds = tasks.filter((t) => !(t.phase === 'plan' && t.director));
        if (tasks.some((t) => ['blocked', 'needs_permission', 'needs_auth', 'interrupted', 'cancelled', 'awaiting_input'].includes(t.state))) throw Error('Needs a human: ' + s);
        if (builds.length && builds.every((t) => t.state === 'completed' && t.integrated_commit)) { result.integrated = [...new Set(builds.map((t) => t.integrated_commit))]; result.milestones = [...new Set(builds.map((t) => t.milestone))]; break; }
        await sleep(5000);
      }
      if (!result.integrated) throw Error('Build did not integrate in time: ' + states);
      log(`Integrated: ${result.integrated.join(', ')}; milestones ${result.milestones.join(' · ')}`);
      await shot(page, 'build-done');
    }
  }
  if (errors.length) throw Error('Page errors: ' + errors.join('\n'));
  result.passed = true;
} catch (e) {
  result.error = String(e);
  log('FAILED: ' + String(e));
  throw e;
} finally {
  writeFileSync(out + '/result.json', JSON.stringify(result, null, 2));
  await browser.close().catch(() => {});
  log(result.passed ? 'CHAT_ACCEPTANCE_PASSED' : 'CHAT_ACCEPTANCE_INCOMPLETE');
}

// One real build through the phone UI: submit, close the browser, reconnect, watch,
// optionally accept and integrate. Real subscription agents only; nothing is mocked.
import {chromium} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {appendFileSync, mkdirSync, writeFileSync} from 'node:fs';
import {resolve} from 'node:path';

if (process.env.ARENA_LIVE !== '1') throw Error('ARENA_LIVE=1 is required: this drives real subscription agents');
const root = process.cwd();
const {ARENA_HOME: home, ARENA_URL: url, ARENA_PROJECT: projectId, ARENA_INSTRUCTIONS: instructions} = process.env;
if (!home || !url || !projectId || !instructions) throw Error('ARENA_HOME, ARENA_URL, ARENA_PROJECT and ARENA_INSTRUCTIONS are required');
const integrate = process.env.ARENA_INTEGRATE === '1';
const limitMs = Number(process.env.ARENA_LIMIT_SECONDS || 1800) * 1000;
const offlineMs = Number(process.env.ARENA_OFFLINE_SECONDS || 45) * 1000;
const evidence = resolve(root, process.env.ARENA_EVIDENCE || 'docs/build-evidence/first-build');
mkdirSync(evidence, {recursive: true});

const log = (message) => {
  const line = new Date().toISOString() + ' ' + message;
  console.log(line);
  appendFileSync(evidence + '/timeline.log', line + '\n');
};
const control = (...args) => execFileSync(root + '/.venv/bin/python', ['-m', 'agent_arena.control', '--home', home, ...args], {cwd: root, encoding: 'utf8'}).trim();
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const phone = {viewport: {width: 390, height: 844}, isMobile: true, hasTouch: true};
const errors = [];
const attention = ['blocked', 'needs_permission', 'needs_auth', 'interrupted', 'cancelled', 'awaiting_input'];

async function open(browser, storageState) {
  const context = await browser.newContext({...phone, ...(storageState ? {storageState} : {})});
  context.on('page', (p) => p.on('pageerror', (e) => errors.push(e.message)));
  const page = await context.newPage();
  page.on('pageerror', (e) => errors.push(e.message));
  await page.goto(url);
  return {context, page};
}
const overview = (page) => page.evaluate(async () => await (await fetch('/api/v1/overview', {cache: 'no-store'})).json());
const mine = (snapshot, goalId) => snapshot.tasks.filter((t) => t.goal_id === goalId);
const describe = (tasks) => tasks.map((t) => `${t.phase}:${t.title.slice(0, 40)} → ${t.state}`).join(' | ');

let browser = await chromium.launch();
let shots = 0;
const result = {passed: false, url, project: projectId, instructions, integrate, timeline: evidence + '/timeline.log'};
try {
  // 1. Pair and submit through the real UI.
  const code = control('pair');
  let {context, page} = await open(browser);
  await page.getByLabel('One-time pairing code').fill(code);
  await page.getByRole('button', {name: 'Pair device', exact: true}).click();
  await page.getByRole('heading', {name: 'Build control.', exact: true}).waitFor();
  log('Paired a phone-sized browser session');
  const before = await overview(page);
  await page.getByRole('button', {name: 'New request', exact: true}).click();
  await page.getByLabel('Your project').selectOption(projectId);
  await page.getByLabel('What would you like to change?').fill(instructions);
  const t0 = Date.now();
  await page.getByRole('button', {name: 'Build this for me', exact: true}).click();
  await page.getByRole('dialog').waitFor({state: 'hidden'});
  const acknowledgedMs = Date.now() - t0;
  const submitted = await overview(page);
  const created = submitted.tasks.filter((t) => t.project_id === projectId && !before.tasks.some((b) => b.id === t.id));
  if (created.length !== 1) throw Error('Expected exactly one new request task, saw ' + created.length);
  const goalId = created[0].goal_id;
  result.goal_id = goalId;
  result.request_task = created[0].id;
  result.acknowledged_ms = acknowledgedMs;
  log(`Request persisted as ${created[0].id} (goal ${goalId}); UI acknowledged in ${acknowledgedMs} ms; state=${created[0].state}`);
  await page.screenshot({path: evidence + '/01-submitted.png', fullPage: true});

  // 2. Close the whole browser. The controller must keep working without it.
  const storageState = await context.storageState();
  await browser.close();
  log(`Browser closed; staying offline for ${offlineMs / 1000}s while the controller works`);
  await sleep(offlineMs);

  // 3. Reconnect in a fresh browser with the same paired device cookie.
  browser = await chromium.launch();
  ({context, page} = await open(browser, storageState));
  await page.getByRole('heading', {name: 'Build control.', exact: true}).waitFor();
  let snapshot = await overview(page);
  let tasks = mine(snapshot, goalId);
  if (!tasks.length) throw Error('Reconnected view does not show the submitted request');
  const whileOffline = snapshot.events.filter((e) => e.time * 1000 > t0).length;
  log(`Reconnected: ${describe(tasks)}; ${whileOffline} persisted events since submission`);
  result.reconnected_state = tasks.map((t) => ({id: t.id, phase: t.phase, state: t.state}));
  await page.screenshot({path: evidence + '/02-reconnected.png', fullPage: true});

  // 4. Watch until every build task reaches verified staging, or something needs a human.
  const started = Date.now();
  let last = '';
  const transitions = [];
  while (Date.now() - started < limitMs) {
    try {
      snapshot = await overview(page);
    } catch {
      await sleep(1000);
      continue;
    }
    tasks = mine(snapshot, goalId);
    const state = describe(tasks);
    if (state !== last) {
      log(state);
      transitions.push({time: Date.now() / 1000, state});
      last = state;
      if (shots < 14) {
        shots += 1;
        await page.screenshot({path: evidence + `/03-progress-${String(shots).padStart(2, '0')}.png`, fullPage: true});
      }
    }
    const stuck = tasks.filter((t) => attention.includes(t.state));
    if (stuck.length) throw Error('Needs a human: ' + stuck.map((t) => `${t.title}: ${t.state} (${t.reason})`).join('; '));
    const children = tasks.filter((t) => t.phase !== 'plan' || t.state === 'ready_for_approval');
    const builds = tasks.filter((t) => !(t.phase === 'plan' && t.director));
    if (builds.length && builds.every((t) => ['ready_for_approval', 'completed'].includes(t.state))) break;
    if (!builds.length && tasks.every((t) => t.state === 'completed')) throw Error('Director completed without creating build tasks');
    await sleep(2000);
    void children;
  }
  tasks = mine(snapshot, goalId);
  const builds = tasks.filter((t) => !(t.phase === 'plan' && t.director));
  if (!builds.length || !builds.every((t) => ['ready_for_approval', 'completed'].includes(t.state))) throw Error('Timed out before verified staging: ' + describe(tasks));
  const attempts = snapshot.attempts.filter((a) => tasks.some((t) => t.id === a.task_id));
  const agents = Object.fromEntries(snapshot.agents.map((a) => [a.id, a]));
  result.transitions = transitions;
  result.seconds_to_verified = (Date.now() - t0) / 1000;
  result.director = snapshot.goals.find((g) => g.id === goalId)?.specification || null;
  result.tasks = tasks.map((t) => ({id: t.id, title: t.title, phase: t.phase, state: t.state, scope: t.scope, reason: t.reason, repairs: t.repairs}));
  result.attempts = attempts.map((a) => ({
    id: a.id, task_id: a.task_id, phase: a.phase, state: a.state, provider: agents[a.account_id]?.provider, account: agents[a.account_id]?.name,
    model: a.model, effort: a.effort, seconds: a.result?.seconds, provider_started: a.result?.provider_started, provider_ended: a.result?.provider_ended,
    usage: a.result?.usage, checks: a.result?.checks, review: a.result?.review, candidate: a.result?.candidate, reason: a.result?.reason, session_ids: a.result?.session_ids,
  }));
  const providers = new Set(result.attempts.filter((a) => a.phase === 'build' && a.state === 'passed').map((a) => a.provider));
  const builds_passed = result.attempts.filter((a) => a.phase === 'build' && a.state === 'passed');
  result.providers_that_built = [...providers];
  result.concurrent = builds_passed.some((a, i) => builds_passed.slice(i + 1).some((b) => a.account !== b.account && Math.max(a.provider_started, b.provider_started) < Math.min(a.provider_ended, b.provider_ended)));
  log(`Verified staging reached in ${result.seconds_to_verified.toFixed(0)}s; builders: ${[...providers].join(', ')}; concurrent=${result.concurrent}`);
  await page.screenshot({path: evidence + '/04-verified.png', fullPage: true});

  if (integrate) {
    // 5. Accept the exact staged commit and integrate it into the source's local main, from the phone UI.
    await page.getByRole('navigation', {name: 'Primary'}).getByRole('button', {name: 'Review', exact: true}).click();
    await page.getByRole('button', {name: 'Inspect evidence & diff'}).last().click();
    await page.getByRole('button', {name: 'Read combined diff'}).click();
    await page.locator('pre').waitFor();
    await page.screenshot({path: evidence + '/05-review.png', fullPage: true});
    await page.getByRole('button', {name: 'Accept staged change', exact: true}).click();
    await page.getByRole('dialog').waitFor({state: 'hidden'});
    log('Accepted the exact staged candidate from the phone UI');
    await page.getByRole('button', {name: 'Inspect evidence & diff'}).last().click();
    await page.getByRole('button', {name: 'Integrate into main', exact: true}).click();
    await page.getByRole('dialog').waitFor({state: 'hidden'});
    let publication;
    for (let n = 0; n < 90; n++) {
      snapshot = await overview(page);
      publication = snapshot.approvals.find((a) => a.publication && tasks.some((t) => t.id === a.task_id));
      if (['integrated', 'failed'].includes(publication?.publication?.state)) break;
      await sleep(1000);
    }
    if (publication?.publication?.state !== 'integrated') throw Error('Main integration did not complete: ' + JSON.stringify(publication?.publication));
    const project = snapshot.projects.find((p) => p.id === projectId);
    const main = execFileSync('git', ['-C', project.repo, 'rev-parse', 'main'], {encoding: 'utf8'}).trim();
    if (main !== publication.candidate) throw Error(`Source main ${main} does not match approved candidate ${publication.candidate}`);
    result.publication = publication.publication;
    result.integrated_commit = main;
    result.source_repo = project.repo;
    log(`Integrated into ${project.repo} main at ${main}`);
    await page.close();
    page = await context.newPage();
    await page.goto(url);
    await page.getByRole('heading', {name: 'Build control.', exact: true}).waitFor();
    await page.getByRole('navigation', {name: 'Primary'}).getByRole('button', {name: 'Projects', exact: true}).click();
    await page.screenshot({path: evidence + '/06-integrated.png', fullPage: true});
  }
  if (errors.length) throw Error('Browser errors: ' + errors.join('\n'));
  result.passed = true;
} catch (error) {
  result.error = String(error);
  log('FAILED: ' + String(error));
  throw error;
} finally {
  result.browser_errors = errors;
  result.ended = new Date().toISOString();
  writeFileSync(evidence + '/result.json', JSON.stringify(result, null, 2));
  await browser.close().catch(() => {});
  log(result.passed ? 'LIVE_BUILD_PASSED' : 'LIVE_BUILD_INCOMPLETE');
}

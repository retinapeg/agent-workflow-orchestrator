// Round 2 acceptance on a COPY of the real control state (tests/control/real_state_server.py):
// one section per slice, 390 px with an Android user agent, screenshots under
// docs/build-evidence/usability/round2/. Dispatch is disabled; the live database and the
// service on 8791 are never touched. ARENA_SLICES=A,B limits the sections that run.
import {chromium} from '@playwright/test';
import {spawn, spawnSync} from 'node:child_process';
import {mkdtempSync,mkdirSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
const root=process.cwd(),state=mkdtempSync(tmpdir()+'/arena-real-'),port='8795',base='http://127.0.0.1:'+port;
const output=root+'/docs/build-evidence/usability/round2';mkdirSync(output,{recursive:true});
const ANDROID='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';
const wanted=(process.env.ARENA_SLICES||'ABCDEF').toUpperCase();
const server=spawn(root+'/.venv/bin/python',['-m','uvicorn','tests.control.real_state_server:create_app','--factory','--host','127.0.0.1','--port',port],{cwd:root,env:{...process.env,ARENA_UI_STATE:state,ARENA_UI_PORT:port},stdio:'pipe'});
let serverLog='';server.stdout.on('data',d=>serverLog+=d);server.stderr.on('data',d=>serverLog+=d);
let browser;const result={state,viewport:390,userAgent:ANDROID,dispatch:false,slices:{}};
const nav=page=>page.getByRole('navigation',{name:'Primary'});
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
// Edit the COPY of the state directly (the fixture refuses most mutations over HTTP).
const sql=(statement)=>{const r=spawnSync(root+'/.venv/bin/python',['-c',`import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);db.execute(sys.argv[2]);db.commit();print(db.total_changes)`,state+'/control.sqlite3',statement],{encoding:'utf8'});if(r.status!==0)throw Error('sql failed: '+r.stderr);return r.stdout.trim()};
const api=(page,path)=>page.evaluate(async p=>{const r=await fetch('/api/v1/'+p,{cache:'no-store'});return r.ok?await r.json():{__status:r.status}},path);
try{
 for(let n=0;n<80;n++){if(server.exitCode!==null)throw Error('Fixture server exited with '+server.exitCode+' (is port '+port+' free?)\n'+serverLog.slice(-800));try{if((await fetch(base)).ok)break}catch{}await sleep(250);}
 browser=await chromium.launch();
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:ANDROID,deviceScaleFactor:2});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);
 await page.getByLabel('One-time pairing code').fill('real-state-one-use-code');
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await nav(page).waitFor();

 if(wanted.includes('A')){
  // A. "Since you were away": the paired session was last seen 12 hours ago.
  const r={};
  sql(`UPDATE sessions SET last_seen=strftime('%s','now')-43200`);
  await page.reload();await nav(page).waitFor();
  const session=await api(page,'session');r.awaySince=session.away_since;
  if(typeof session.away_since!=='number')throw Error('Session does not report the absence: '+JSON.stringify(session));
  await nav(page).getByRole('button',{name:'Overview',exact:true}).click();
  const panel=page.locator('.away-panel');await panel.waitFor({timeout:10000});
  const rows=panel.locator('.list-row');r.rows=await rows.count();
  r.labels=(await rows.locator('.tone').allInnerTexts()).slice(0,12);
  if(!r.rows)throw Error('Away panel has no rows');
  if(await overflow(page))throw Error('Horizontal overflow on Home with the away panel');
  await page.screenshot({path:output+'/a-since-away-390.png'});
  // Viewing marks it seen on the host; the panel stays until the owner leaves Home.
  await sleep(800);
  r.seenAfterView=(await api(page,'session')).away_since;
  if(r.seenAfterView!==null)throw Error('Absence not marked seen after viewing');
  if(!(await panel.isVisible()))throw Error('Panel vanished while being read');
  // One tap opens the build.
  const first=await rows.first().locator('b').innerText();
  await rows.first().click();
  await page.locator('#goal-pick').waitFor();
  r.opened={row:first,build:await page.locator('.page-sub').first().innerText()};
  await page.screenshot({path:output+'/a-tap-into-build-390.png'});
  await nav(page).getByRole('button',{name:'Overview',exact:true}).click();
  await page.getByRole('heading',{name:'Build control.',exact:true}).waitFor();
  if(await page.locator('.away-panel').count())throw Error('Away panel shown again after it was seen');
  result.slices.A=r;console.log('A',JSON.stringify(r));
 }

 if(wanted.includes('B')){
  // B. Task detail on demand and the Transcript view.
  const r={};
  const snap=await api(page,'overview');
  const archivedIds=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));
  const goals=[...snap.goals].sort((a,b)=>b.created-a.created);
  const integrated=goals.find(g=>!archivedIds.has(g.project_id)&&snap.tasks.some(t=>t.goal_id===g.id&&t.integrated_commit));
  await nav(page).getByRole('button',{name:'Build',exact:true}).click();
  await page.locator('#goal-pick').waitFor();
  await page.locator('#goal-pick').selectOption(integrated.id);
  // The first row of the Agent stack is the first non-cancelled child task (by creation).
  const children=snap.tasks.filter(t=>t.goal_id===integrated.id&&!(t.director&&(t.phase==='plan'||t.phase==='replan'))).sort((a,b)=>a.created-b.created).filter(t=>t.state!=='cancelled');
  const target=children[0];
  await page.locator('.agent-row').first().click();
  const dialog=page.getByRole('dialog');await dialog.waitFor();
  const summary=dialog.locator('details.spec summary',{hasText:'Instructions'});
  await summary.waitFor({timeout:10000});
  await summary.click();
  await page.waitForFunction(()=>!document.querySelector('details.spec summary')?.textContent?.includes('loading'),null,{timeout:10000});
  const shown=(await dialog.locator('.prewrap').first().innerText()).replace(/\s+/g,' ').trim();
  const full=await api(page,'tasks/'+target.id);
  const expected=String(full.instructions||'').replace(/\s+/g,' ').trim();
  r.instructions={task:target.id,chars:shown.length,listExcerpt:(target.instructions||'').length,truncatedInList:!!target.instructions_truncated};
  if(shown!==expected)throw Error('Dialog instructions differ from GET tasks/{id}: '+shown.length+' vs '+expected.length);
  if(target.instructions_truncated&&shown.length<=target.instructions.length)throw Error('Dialog shows only the list excerpt');
  await page.screenshot({path:output+'/b-task-detail-390.png'});
  // The Transcript view renders agent lines, never raw JSON.
  await dialog.getByRole('button',{name:'Transcript'}).first().click();
  const tr=dialog.locator('.transcript-view');await tr.waitFor({timeout:15000});
  await page.waitForFunction(()=>{const t=document.querySelector('.transcript-view .transcript');return t&&t.textContent.trim().length>40},null,{timeout:20000});
  const lines=(await tr.locator('.transcript').innerText()).split('\n').map(l=>l.trim()).filter(Boolean);
  r.transcript={lines:lines.length,head:await tr.locator('.transcript-head').innerText(),first:lines[0]?.slice(0,120),last:lines[lines.length-1]?.slice(0,120)};
  if(lines.some(l=>l.startsWith('{')))throw Error('Transcript shows raw JSON');
  if(lines.length<3)throw Error('Transcript too short: '+lines.join(' | '));
  await tr.scrollIntoViewIfNeeded();
  await page.screenshot({path:output+'/b-transcript-390.png'});
  if(await overflow(page))throw Error('Horizontal overflow in the task dialog');
  await dialog.getByRole('button',{name:'Close dialog'}).click();
  await dialog.waitFor({state:'hidden'});
  result.slices.B=r;console.log('B',JSON.stringify(r));
 }

 const after=await api(page,'overview');
 if(after.__status)throw Error('Overview unreadable at the end: '+after.__status);
 if(errors.length)throw Error(errors.join('\n'));
 result.passed=true;result.errors=errors;
 writeFileSync(output+'/result.json',JSON.stringify(result,null,2));
 console.log('ROUND2_ACCEPTANCE_PASSED');
}catch(error){result.passed=false;result.failure=String(error);writeFileSync(output+'/result.json',JSON.stringify(result,null,2));throw error}
finally{
 if(browser)await browser.close();
 const exited=new Promise(r=>{if(server.exitCode!==null)r();else{server.once('exit',r);setTimeout(r,5000)}});
 server.kill('SIGTERM');await exited;
 writeFileSync(output+'/server.txt',serverLog);
 rmSync(state,{recursive:true,force:true});
}

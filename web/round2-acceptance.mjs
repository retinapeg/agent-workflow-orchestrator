// Round 2 acceptance on a COPY of the real control state (tests/control/real_state_server.py):
// one section per slice, 390 px with an Android user agent, screenshots under
// docs/build-evidence/usability/round2/. Dispatch is disabled; the live database and the
// service on 8791 are never touched. ARENA_SLICES=A,B limits the sections that run (the
// other slices keep their last recorded result in result.json); ARENA_REAL_DB names the
// database to copy (default .arena-control/control.sqlite3); ARENA_EVIDENCE redirects the
// output (a trial run that must not replace the recorded evidence).
import {spawn, spawnSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {mkdtempSync,mkdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
const root=process.cwd(),state=mkdtempSync(tmpdir()+'/arena-real-'),port='8795',base='http://127.0.0.1:'+port;
const output=process.env.ARENA_EVIDENCE||root+'/docs/build-evidence/usability/round2';mkdirSync(output,{recursive:true});
const ANDROID='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';
const wanted=(process.env.ARENA_SLICES||'ABCDEF').toUpperCase();
const server=spawn(root+'/.venv/bin/python',['-m','uvicorn','tests.control.real_state_server:create_app','--factory','--host','127.0.0.1','--port',port],{cwd:root,env:{...process.env,ARENA_UI_STATE:state,ARENA_UI_PORT:port},stdio:'pipe'});
let serverLog='';server.stdout.on('data',d=>serverLog+=d);server.stderr.on('data',d=>serverLog+=d);
let previous={};try{previous=JSON.parse(readFileSync(output+'/result.json','utf8'))}catch{}
let browser;const result={state,viewport:390,userAgent:ANDROID,dispatch:false,ran:wanted,platform:process.platform,slices:{...(previous.slices||{})}};
const nav=page=>page.getByRole('navigation',{name:'Primary'});
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
// The repository's Playwright (the Mac has its browsers); where only another Playwright
// build has browsers installed (a container), that one: ARENA_PLAYWRIGHT names its module.
async function launch(){
 try{return await (await import('@playwright/test')).chromium.launch()}
 catch(error){try{return await (await import(process.env.ARENA_PLAYWRIGHT||'/opt/npm-tools/node_modules/playwright/index.mjs')).chromium.launch()}catch{throw error}}
}
// Edit the COPY of the state directly (the fixture refuses most mutations over HTTP).
const sql=(statement)=>{const r=spawnSync(root+'/.venv/bin/python',['-c',`import sqlite3,sys;db=sqlite3.connect(sys.argv[1]);db.execute(sys.argv[2]);db.commit();print(db.total_changes)`,state+'/control.sqlite3',statement],{encoding:'utf8'});if(r.status!==0)throw Error('sql failed: '+r.stderr);return r.stdout.trim()};
// Python against the copy with `db` and the JSON payload `p` (seeding records the UI reads).
const py=(code,payload={})=>{const r=spawnSync(root+'/.venv/bin/python',['-c','import json,sqlite3,sys,time\ndb=sqlite3.connect(sys.argv[1])\np=json.loads(sys.argv[2])\n'+code+'\ndb.commit()',state+'/control.sqlite3',JSON.stringify(payload)],{encoding:'utf8'});if(r.status!==0)throw Error('python failed: '+r.stderr);return r.stdout.trim()};
// A conversation as the host stores it, with its messages ([role, text, data] in order).
const seedChat=(conv,messages)=>{const id='conv_'+randomBytes(6).toString('hex');py(`d={"title":"New conversation","created":time.time(),"updated":time.time(),"project_id":None,"spec":None,"status":"idle",**p["conv"]}
db.execute("INSERT INTO conversations(id,data) VALUES(?,?)",(p["id"],json.dumps(d)))
for role,text,data in p["messages"]:
    db.execute("INSERT INTO messages(conversation_id,time,role,text,data) VALUES(?,?,?,?,?)",(p["id"],time.time(),role,text,json.dumps(data)))`,{id,conv,messages});return id};
const api=(page,path)=>page.evaluate(async p=>{const r=await fetch('/api/v1/'+p,{cache:'no-store'});return r.ok?await r.json():{__status:r.status}},path);
// Open one conversation in Chat the way the app does: the remembered thread on this device.
const openChat=async(page,id)=>{await page.evaluate(i=>localStorage.setItem('arena.conversation',i),id);await page.reload();await nav(page).waitFor();await nav(page).getByRole('button',{name:'Chat',exact:true}).click();await page.getByLabel('Message Arena').waitFor();};
const flat=async loc=>(await loc.innerText()).replace(/\s+/g,' ').trim();
try{
 for(let n=0;n<80;n++){if(server.exitCode!==null)throw Error('Fixture server exited with '+server.exitCode+' (is port '+port+' free?)\n'+serverLog.slice(-800));try{if((await fetch(base)).ok)break}catch{}await sleep(250);}
 browser=await launch();
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

 if(wanted.includes('C')){
  // C. Director turn visibility on conversations seeded in the copy: a turn in progress
  // (step, the command it is on, a live elapsed clock, "Still working" past 90 s), an
  // error with Retry that re-sends through the real send path, and a turn orphaned by a
  // restart that is closed with Retry when the phone reads it.
  const r={};
  const snap=await api(page,'overview');
  const project=snap.projects.filter(p=>!p.archived&&p.enabled!==false).sort((a,b)=>(b.created||0)-(a.created||0))[0]||null;
  const secs=t=>{const m=/(?:(\d+)m )?(\d+)s\b/.exec(t);return m?Number(m[1]||0)*60+Number(m[2]):-1};
  const asked='Where is the bell sound played?';
  const thinkingId=seedChat({title:asked,project_id:project?.id||null,status:'thinking',step:'reading repository',step_detail:'rg -n "bell" src/',step_since:Date.now()/1000-4,turn_started:Date.now()/1000-30,turn_repo:'arena-smoke-20261028',commands:3},[['user',asked,{}]]);
  await openChat(page,thinkingId);
  const bubble=page.locator('.msg.thinking');await bubble.waitFor({timeout:15000});
  await page.waitForFunction(()=>document.querySelector('.msg.thinking .thinking-detail'),null,{timeout:10000});
  const early=await flat(bubble);await sleep(2300);const later=await flat(bubble);
  r.thinking={early,later,project:project?.name||null};
  if(!/^Reading /.test(early)||!early.includes('rg -n "bell" src/')||/Still working/.test(early))throw Error('Thinking bubble does not show the step and command: '+early);
  if(!(secs(later)>secs(early)&&secs(early)>=29))throw Error('Elapsed time is not live: '+early+' → '+later);
  // Past 90 s the bubble says it is still working (the host's turn start moves back 70 s).
  py(`db.execute("UPDATE conversations SET rev=rev+1,data=json_set(data,'$.turn_started',?) WHERE id=?",(time.time()-100,p["id"]))`,{id:thinkingId});
  await page.waitForFunction(()=>/Still working/.test(document.querySelector('.msg.thinking')?.textContent||''),null,{timeout:10000});
  r.thinking.still=await flat(bubble);
  if(!r.thinking.still.includes('rg -n "bell" src/')||secs(r.thinking.still)<100)throw Error('Still-working line lost the step: '+r.thinking.still);
  if(await overflow(page))throw Error('Horizontal overflow on Chat while thinking');
  await page.screenshot({path:output+'/c-director-thinking-390.png'});
  // An error: plain language, the technical detail folded away, Retry on the newest only.
  const errorText='The director took longer than 4 minutes and was stopped. Nothing was changed. Tap Retry to ask again.';
  const question='What is running right now?';
  const errorId=seedChat({title:question,project_id:project?.id||null},[['user',question,{}],['assistant',errorText,{error:true,error_kind:'timeout',detail:'codex exec: turn stopped after 240 s (SIGTERM)',retry_text:question}]]);
  await openChat(page,errorId);
  const err=page.locator('.msg.arena.error').last();await err.waitFor({timeout:15000});
  r.error={text:await flat(err.locator('.bubble')),retry:await err.getByRole('button',{name:'Retry',exact:true}).count(),detail:await err.locator('details.error-detail summary').count()};
  if(r.error.text!==errorText||r.error.retry!==1||r.error.detail!==1)throw Error('Error bubble: '+JSON.stringify(r.error));
  if(await err.getByRole('button',{name:'Retry',exact:true}).isDisabled())throw Error('Retry is disabled on an idle conversation');
  if(await overflow(page))throw Error('Horizontal overflow on Chat with an error');
  await page.screenshot({path:output+'/c-director-error-390.png'});
  // Retry re-sends the question through POST conversations/{id}/messages. With every slot
  // switched off in the copy, the director answers at once and no CLI runs.
  py(`db.execute("CREATE TABLE acceptance_accounts AS SELECT id,data FROM accounts")
db.execute("UPDATE accounts SET data=json_set(data,'$.enabled',json('false'))")`);
  await err.getByRole('button',{name:'Retry',exact:true}).click();
  let conv=null;for(let n=0;n<60;n++){conv=await api(page,'conversations/'+errorId);if(conv.messages?.length>=4&&conv.status==='idle')break;await sleep(500)}
  py(`db.execute("UPDATE accounts SET data=(SELECT data FROM acceptance_accounts a WHERE a.id=accounts.id)")
db.execute("DROP TABLE acceptance_accounts")`);
  const [again,answer]=conv.messages.slice(-2);
  r.retry={resent:again?.text,reply:answer?.text,kind:answer?.error_kind,retry:answer?.retry_text};
  if(again?.role!=='user'||again.text!==question||answer?.role!=='assistant'||answer.error_kind!=='no_account'||answer.retry_text!==question)throw Error('Retry did not re-send through the send path: '+JSON.stringify(r.retry));
  await page.locator('.msg.arena.error').nth(1).waitFor({timeout:10000});
  r.retry.buttons=await page.getByRole('button',{name:'Retry',exact:true}).count();
  if(r.retry.buttons!==1)throw Error('Retry offered on '+r.retry.buttons+' messages; only the newest should offer it');
  // A turn whose process died with a restart (started 400 s ago): reading it closes it.
  const orphanId=seedChat({title:'Plan the export feature',project_id:project?.id||null,status:'thinking',step:'reading repository',step_detail:'git log -5',step_since:Date.now()/1000-390,turn_started:Date.now()/1000-400,commands:2},[['user','Plan the export feature',{}]]);
  await openChat(page,orphanId);
  const closed=page.locator('.msg.arena.error').last();await closed.waitFor({timeout:15000});
  r.recovered={text:await flat(closed.locator('.bubble')),retry:await closed.getByRole('button',{name:'Retry',exact:true}).count(),thinking:await page.locator('.msg.thinking').count()};
  if(r.recovered.text!=='The director was interrupted (Arena restarted). Tap Retry.'||r.recovered.retry!==1||r.recovered.thinking)throw Error('Orphaned turn not closed: '+JSON.stringify(r.recovered));
  result.slices.C=r;console.log('C',JSON.stringify(r));
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

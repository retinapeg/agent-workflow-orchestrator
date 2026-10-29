// Round 2 acceptance on a COPY of the real control state (tests/control/real_state_server.py):
// one section per slice, 390 px with an Android user agent, screenshots under
// docs/build-evidence/usability/round2/. Dispatch is disabled; the live database and the
// service on 8791 are never touched. ARENA_SLICES=A,B limits the sections that run (the
// other slices keep their last recorded result in result.json); ARENA_REAL_DB names the
// database to copy (default .arena-control/control.sqlite3); ARENA_EVIDENCE redirects the
// output (a trial run that must not replace the recorded evidence). Round 4: navigation is the
// owner's ☰ menu at every width (no sidebar, no bottom navigation); a build opens as its
// plain-English story, and its technical view (#goal-pick) is an Advanced mode screen, under
// the story's ⋯ → Technical build view once Advanced mode is on.
import {spawn, spawnSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {mkdtempSync,mkdirSync,readFileSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
const root=process.cwd(),state=mkdtempSync(tmpdir()+'/arena-real-'),port='8795',base='http://127.0.0.1:'+port;
const output=process.env.ARENA_EVIDENCE||root+'/docs/build-evidence/usability/round2';mkdirSync(output,{recursive:true});
const ANDROID='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';
const wanted=(process.env.ARENA_SLICES||'ABCDEF').toUpperCase();
// The fixture's director starts after `spawned` and before the server first answers
// (`ready`): a seeded turn that started before `spawned` was the previous controller's
// (closed at once when read); one standing for a turn this controller is answering must
// start after `ready`, so its elapsed time can be at most the server's uptime.
const spawned=Date.now()/1000;let ready=0;
const server=spawn(root+'/.venv/bin/python',['-m','uvicorn','tests.control.real_state_server:create_app','--factory','--host','127.0.0.1','--port',port],{cwd:root,env:{...process.env,ARENA_UI_STATE:state,ARENA_UI_PORT:port},stdio:'pipe'});
let serverLog='';server.stdout.on('data',d=>serverLog+=d);server.stderr.on('data',d=>serverLog+=d);
let previous={};try{previous=JSON.parse(readFileSync(output+'/result.json','utf8'))}catch{}
let browser;const result={state,viewport:390,userAgent:ANDROID,dispatch:false,ran:wanted,platform:process.platform,slices:{...(previous.slices||{})}};
// Open a screen (Round 4: no sidebar at any width). The lion and wordmark go Home; everything
// else is in the ☰ menu, where Projects, Agents and Review are listed only in Advanced mode:
// asked for one of those, this turns Advanced mode on from the menu's own switch first.
const go=async(page,name)=>{if(name==='Home'){await page.getByRole('button',{name:'Arena home',exact:true}).click();return}await page.getByRole('button',{name:/^Menu\b/}).click();const sheet=page.getByRole('dialog',{name:'Menu'}),item=sheet.getByRole('button',{name,exact:true});await sheet.getByRole('switch',{name:'Advanced mode'}).waitFor();if(['Projects','Agents','Review'].includes(name)&&!(await item.count()))await sheet.getByRole('switch',{name:'Advanced mode'}).click();await item.click();};
// Advanced mode on or off from the ☰ menu's switch (remembered on this device).
const advancedMode=async(page,on)=>{await page.getByRole('button',{name:/^Menu\b/}).click();const sheet=page.getByRole('dialog',{name:'Menu'}),sw=sheet.getByRole('switch',{name:'Advanced mode'});if(await sw.getAttribute('aria-checked')!==String(on))await sw.click();await page.keyboard.press('Escape');await sheet.waitFor({state:'hidden'});};
// The app is up (paired, snapshot read): its menu button is on screen.
const appUp=page=>page.getByRole('button',{name:/^Menu\b/}).waitFor();
// The technical view of a build opened as a story (Advanced mode): ⋯ → Technical build view.
const advanced=async page=>{await advancedMode(page,true);await page.getByRole('button',{name:'Build options',exact:true}).click();await page.getByRole('menuitem',{name:'Technical build view',exact:true}).click();await page.locator('#goal-pick').waitFor();};
// The technical build view from Build history (Advanced mode).
const technical=async page=>{await advancedMode(page,true);await go(page,'Build history');await page.getByRole('button',{name:'Technical build view',exact:true}).click();await page.locator('#goal-pick').waitFor();};
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const uptime=async s=>{const wait=(ready+s)*1000-Date.now();if(wait>0)await sleep(wait)};
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
const openChat=async(page,id)=>{await page.evaluate(i=>localStorage.setItem('arena.conversation',i),id);await page.reload();await appUp(page);await go(page,'Chats');await page.getByLabel('Message Arena').waitFor();};
const flat=async loc=>(await loc.innerText()).replace(/\s+/g,' ').trim();
// The director's plan task, by the host's rule (story.is_plan_task).
const planTask=t=>!t.planner_attempt&&(t.phase==='plan'||t.phase==='replan'||'planned_tasks' in t);
// Home's "Finished recently" line as the host's records tell it: in the last 24 hours, builds
// (not archived, nor their project) whose work all finished, and builds that stopped.
const recentFromRecords=async page=>{
 const snap=await api(page,'overview');const since=snap.server_time-86400;
 const shelved=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));
 let finished=0,stopped=0;
 for(const g of snap.goals.filter(g=>!g.archived&&!shelved.has(g.project_id))){
  const s=await api(page,'goals/'+g.id+'/story');
  const work=snap.tasks.filter(t=>t.goal_id===g.id&&!planTask(t));
  if(s.status==='completed'&&Math.max(0,...work.filter(t=>t.state==='completed').map(t=>t.updated||0))>=since)finished++;
  if(s.status==='blocked'&&Math.max(0,...(s.blocks||[]).filter(b=>b.state==='blocked').map(b=>b.updated||0))>=since)stopped++;
 }
 return `Finished recently: ${finished?`${finished} build${finished===1?'':'s'}`:'none'}${stopped?` · ${stopped} stopped`:''}`;
};
// Home's line agrees with the records read just before or just after it (a build can cross the
// 24-hour mark in between).
const recentOnHome=async page=>{const before=await recentFromRecords(page);const shown=await flat(page.locator('.finished-line'));const after=await recentFromRecords(page);return {shown,expected:before===after?before:[before,after],ok:shown===before||shown===after}};
// Round 3: Chat's default view is plain English. No commit hash, file name, path, exit code or
// shell command ("$ …", "pytest …") outside a disclosure the owner opens.
const MACHINERY=[[/\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b/,'a commit hash'],[/\b[\w-]+\.(?:py|tsx?|jsx?|json|toml|ya?ml|sh|md|css|html)\b/,'a file name'],[/(?:^|\s)(?:~|\.{0,2})\/[\w.-]+\/[\w./-]+/,'a path'],[/Traceback|exit=-?\d+|exit code|timeout=True|stderr/i,'terminal output'],[/(?:^|\s)\$ |\bpytest\b|\brg -n\b|\bgit log\b/,'a shell command']];
const machinery=text=>MACHINERY.map(([re,what])=>{const m=re.exec(text);return m?`${what} ("${m[0].trim()}")`:''}).filter(Boolean);
try{
 for(let n=0;n<80;n++){if(server.exitCode!==null)throw Error('Fixture server exited with '+server.exitCode+' (is port '+port+' free?)\n'+serverLog.slice(-800));try{if((await fetch(base)).ok){ready=Date.now()/1000;break}}catch{}await sleep(250);}
 if(!ready)throw Error('Fixture server never answered\n'+serverLog.slice(-800));
 browser=await launch();
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:ANDROID,deviceScaleFactor:2});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);
 await page.getByLabel('One-time pairing code').fill('real-state-one-use-code');
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await appUp(page);

 if(wanted.includes('A')){
  // A. Back after time away (Round 4: Home has no "Since you were away" banner). The paired
  // session was last seen 12 hours ago, or before the copy's newest recorded activity if that is
  // older (a copy ages), so the absence spans something. Home shows what it always shows: one
  // line, "Finished recently: …", counted from the host's records for the last 24 hours, which
  // opens Build history, where one tap opens a build. The app is parked first so no request of
  // its own lands after the change and records the device as seen.
  const r={};
  await page.goto('about:blank');await sleep(1500);
  sql(`UPDATE sessions SET last_seen=MIN(strftime('%s','now')-43200,(SELECT MAX(json_extract(data,'$.updated')) FROM tasks)-600)`);
  const session=await (await page.request.get(base+'/api/v1/session')).json();r.awaySince=session.away_since;
  if(typeof session.away_since!=='number')throw Error('Session does not report the absence: '+JSON.stringify(session));
  await page.goto(base);await appUp(page);
  await go(page,'Home');
  await page.getByLabel('Talk to Arena').waitFor();
  r.banner=await page.locator('.away-panel, .away-banner').count()+await page.getByText(/Since you were away/i).count();
  if(r.banner)throw Error('Home shows an away banner');
  r.lines=await page.locator('.finished-line').count();
  if(r.lines!==1)throw Error('Home has '+r.lines+' "Finished recently" lines');
  r.recent=await recentOnHome(page);
  if(!r.recent.ok)throw Error('Home says "'+r.recent.shown+'"; the records say '+JSON.stringify(r.recent.expected));
  if(await overflow(page))throw Error('Horizontal overflow on Home after an absence');
  await page.screenshot({path:output+'/a-home-after-away-390.png'});
  // The line opens Build history; one tap there opens the build.
  await page.locator('.finished-line').click();
  await page.getByRole('heading',{name:'Build history',exact:true}).waitFor();
  r.sections=await page.locator('.history .home-section').evaluateAll(s=>s.map(x=>x.getAttribute('aria-label')));
  await page.screenshot({path:output+'/a-build-history-390.png'});
  const row=page.locator('.history .finished-row').first();
  const first=await row.locator('b').innerText();
  await row.click();
  await page.locator('.screen.story .story-title').waitFor();
  r.opened={row:first,build:await page.locator('.screen.story .story-title').innerText()};
  await page.screenshot({path:output+'/a-tap-into-build-390.png'});
  result.slices.A=r;console.log('A',JSON.stringify(r));
 }

 if(wanted.includes('B')){
  // B. Task detail on demand and the Transcript view.
  const r={};
  const snap=await api(page,'overview');
  const archivedIds=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));
  const goals=[...snap.goals].sort((a,b)=>b.created-a.created);
  const integrated=goals.find(g=>!archivedIds.has(g.project_id)&&snap.tasks.some(t=>t.goal_id===g.id&&t.integrated_commit));
  // The technical view (Advanced mode), from Build history (the build's record lives there).
  await technical(page);
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
  // restart that is closed with Retry when the phone reads it. The turn in progress stands
  // for one this controller is answering, so it starts after the controller did: the
  // section waits until the server has been up longer than the elapsed time it shows.
  const r={};
  const snap=await api(page,'overview');
  const project=snap.projects.filter(p=>!p.archived&&p.enabled!==false).sort((a,b)=>(b.created||0)-(a.created||0))[0]||null;
  const secs=t=>{const m=/(?:(\d+)m )?(\d+)s\b/.exec(t);return m?Number(m[1]||0)*60+Number(m[2]):-1};
  const asked='Where is the bell sound played?';
  await uptime(31);
  const thinkingId=seedChat({title:asked,project_id:project?.id||null,status:'thinking',step:'reading repository',step_detail:'rg -n "bell" src/',step_since:Date.now()/1000-4,turn_started:Date.now()/1000-30,turn_repo:'arena-smoke-20261028',commands:3},[['user',asked,{}]]);
  await openChat(page,thinkingId);
  const bubble=page.locator('.msg.thinking');await bubble.waitFor({timeout:15000});
  await page.waitForFunction(()=>document.querySelector('.msg.thinking details.thinking-details'),null,{timeout:10000});
  const early=await flat(bubble);await sleep(2300);const later=await flat(bubble);
  r.thinking={early,later,project:project?.name||null};
  // Round 3: the plain step ("Reading the code in <project>"); the command stays folded under
  // a closed "Details" disclosure until the owner opens it.
  const reading='Reading the code in '+(project?.name||'arena-smoke-20261028');
  if(!early.startsWith(reading)||early.includes('rg -n')||/Still working/.test(early))throw Error('Thinking bubble is not the plain step: '+early);
  if(machinery(early).length)throw Error('Thinking bubble shows '+machinery(early).join(', '));
  if(await bubble.locator('details.thinking-details').evaluate(d=>d.open))throw Error('The command is shown by default');
  await bubble.locator('details.thinking-details > summary').click();
  r.thinking.details=await flat(bubble.locator('details.thinking-details'));
  if(!r.thinking.details.includes('rg -n "bell" src/'))throw Error('Details does not hold the command: '+r.thinking.details);
  await bubble.locator('details.thinking-details > summary').click();
  if(!(secs(later)>secs(early)&&secs(early)>=29))throw Error('Elapsed time is not live: '+early+' → '+later);
  // Past 90 s the bubble says it is still working (the host's turn start moves back 70 s).
  await uptime(101);
  py(`db.execute("UPDATE conversations SET rev=rev+1,data=json_set(data,'$.turn_started',?) WHERE id=?",(time.time()-100,p["id"]))`,{id:thinkingId});
  await page.waitForFunction(()=>{const t=document.querySelector('.msg.thinking')?.innerText||'';const m=/(?:(\d+)m )?(\d+)s\b/.exec(t);return /Still working/.test(t)&&!!m&&Number(m[1]||0)*60+Number(m[2])>=100},null,{timeout:10000});
  r.thinking.still=await flat(bubble);
  if(!r.thinking.still.includes(reading)||r.thinking.still.includes('rg -n')||secs(r.thinking.still)<100)throw Error('Still-working line lost the step: '+r.thinking.still);
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
  // A turn the previous controller was answering when Arena restarted, 30 s before this one
  // started (a self-build activation): nothing can answer it any more, so reading it closes
  // it at once, minutes before the stale-turn backstop would.
  const orphanId=seedChat({title:'Plan the export feature',project_id:project?.id||null,status:'thinking',step:'reading repository',step_detail:'git log -5',step_since:spawned-25,turn_started:spawned-30,commands:2},[['user','Plan the export feature',{}]]);
  await openChat(page,orphanId);
  const closed=page.locator('.msg.arena.error').last();await closed.waitFor({timeout:15000});
  r.recovered={text:await flat(closed.locator('.bubble')),retry:await closed.getByRole('button',{name:'Retry',exact:true}).count(),thinking:await page.locator('.msg.thinking').count(),turnAge:Math.round(Date.now()/1000-spawned+30)};
  if(r.recovered.text!=='The director was interrupted before it could answer. Tap Retry.'||r.recovered.retry!==1||r.recovered.thinking)throw Error('Orphaned turn not closed: '+JSON.stringify(r.recovered));
  result.slices.C=r;console.log('C',JSON.stringify(r));
 }

 if(wanted.includes('D')){
  // D. Chat as the front door. The reply that started a build is that build's card, kept
  // current from the snapshot and the build's story, in plain English (Round 3): "Merged into
  // main" once on main (the commit only in the chip's tooltip); Review only where the owner
  // decides (a pending approval on a project that integrates manually); "Merging into main" or
  // "Merge failed" (with the story's plain reason) on a project that integrates automatically,
  // never Review; live with the agent, model and what it is doing in the host's words (never
  // the raw command) while an attempt runs. A build is seeded in the copy and moved through
  // those states (cloned from the newest integrated build's records). And a new chat shows
  // the project it starts on before anything is sent.
  const r={};
  const snap=await api(page,'overview');
  const archivedIds=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));
  const isPlan=t=>t.director&&(t.phase==='plan'||t.phase==='replan');
  const childrenOf=g=>snap.tasks.filter(t=>t.goal_id===g.id&&!isPlan(t));
  const integrated=[...snap.goals].sort((a,b)=>b.created-a.created).find(g=>{const live=childrenOf(g).filter(t=>t.state!=='cancelled');return !archivedIds.has(g.project_id)&&live.length>0&&live.every(t=>t.state==='completed')&&live.some(t=>t.integrated_commit)});
  if(!integrated)throw Error('The copy has no integrated build on an active project');
  const project=snap.projects.find(p=>p.id===integrated.project_id);
  const done=childrenOf(integrated).filter(t=>t.integrated_commit).sort((a,b)=>(b.updated||0)-(a.updated||0))[0];
  const sha=String(done.integrated_commit).slice(0,7);
  const card=()=>page.locator('.chat-build').last();
  const reviews=()=>card().getByRole('button',{name:'Review',exact:true}).count();
  // Waits for the card to say `text` (the snapshot is polled; the live tail every 2.5 s).
  const cardSays=async(text,what)=>{try{await page.waitForFunction(t=>{const c=[...document.querySelectorAll('.chat-build')].pop();return !!c&&c.textContent.replace(/\s+/g,' ').includes(t)},text,{timeout:20000})}catch{throw Error(what+' · card: '+(await flat(card()).catch(()=>'(none)')))}};
  const noOverflow=async where=>{if(await overflow(page))throw Error('Horizontal overflow on Chat with '+where)};
  // The card's default view: plain English only.
  const plainCard=async where=>{const text=await flat(card());const found=machinery(text);if(found.length)throw Error(`The ${where} card shows ${found.join(', ')}: ${text}`);return text};
  const story=goal=>api(page,'goals/'+goal+'/story');

  // 1. An integrated build: "Merged into main", the commit only in the chip's tooltip, no
  // Review, one tap opens that build.
  const builtChat=seedChat({title:'Add bell control',project_id:project.id},[['user','Build it.',{}],['assistant','Starting the build.',{build_started:true,goal_id:integrated.id,task_id:done.id,project:project.name}]]);
  await openChat(page,builtChat);
  await cardSays('Merged into main','Integrated build');
  r.integrated={goal:integrated.id,sha,card:await plainCard('integrated'),review:await reviews(),tooltip:await card().locator('.chip[title]').getAttribute('title')};
  if(r.integrated.review)throw Error('Review offered on an integrated build');
  if(r.integrated.card.includes(sha)||r.integrated.tooltip!=='Commit '+sha+' on main')throw Error('The commit is not (only) in the tooltip: '+JSON.stringify(r.integrated));
  await noOverflow('an integrated build card');
  await page.screenshot({path:output+'/d-chat-build-card-390.png'});
  await card().getByRole('button',{name:'Open the build',exact:true}).click();
  await page.locator('.screen.story').waitFor();
  await advanced(page);
  r.integrated.opened=await page.locator('#goal-pick').inputValue();
  if(r.integrated.opened!==integrated.id)throw Error('Open the build opened '+r.integrated.opened);

  // A build of our own on the same project, cloned from the integrated one's records.
  const hex=()=>randomBytes(8).toString('hex');
  const plan=snap.tasks.find(t=>t.goal_id===integrated.id&&isPlan(t))||done;
  const codex=snap.agents.find(a=>a.provider==='codex')||snap.agents[0];
  const ids={goal:'goal_'+hex(),plan:'task_'+hex(),task:'task_'+hex(),approval:'approval_'+hex(),attempt:'attempt_'+hex(),project:project.id,policy:project.integration_policy||'manual',tplPlan:plan.id,tplTask:done.id,tplApproval:done.approval_id||null,tplAttempt:snap.attempts.find(a=>a.task_id===done.id)?.id||null,account:codex.id,candidate:'d15ea5e'+'0'.repeat(33),base:'ba5eba1'+'0'.repeat(33),title:'Add a persistent overnight queue mode',taskTitle:'Queue requests overnight and run them economically',artifact:state+'/acceptance-attempts/live'};
  const PRELUDE=`now=time.time()
row=lambda table,ident:json.loads(db.execute(f"SELECT data FROM {table} WHERE id=?",(ident,)).fetchone()[0]) if ident else {}
def put(table,ident,data,state=None):
    if state is None: db.execute(f"UPDATE {table} SET rev=rev+1,data=? WHERE id=?",(json.dumps(data),ident))
    else: db.execute(f"UPDATE {table} SET rev=rev+1,state=?,data=? WHERE id=?",(state,json.dumps(data),ident))
def policy(value):
    project=row("projects",p["project"]);project["integration_policy"]=value;put("projects",p["project"],project)
`;
  const step=(code,extra={})=>py(PRELUDE+code,{...ids,...extra});
  step(`plan={**row("tasks",p["tplPlan"]),"title":p["title"],"phase":"plan","director":True,"created":now-300,"updated":now-280,"reason":"Planned one task"}
child={k:v for k,v in row("tasks",p["tplTask"]).items() if k not in ("integrated_commit","approval_id","staged","candidate","candidate_base","build_attempt","review_attempt","director_review_attempt","attempt_id")}
child.update(title=p["taskTitle"],created=now-270,updated=now-30,reason="Queued",milestone="M1 — Overnight queue")
db.execute("INSERT INTO goals(id,project_id,data) VALUES(?,?,?)",(p["goal"],p["project"],json.dumps({"title":p["title"],"request":p["title"],"created":now-300})))
db.execute("INSERT INTO tasks(id,project_id,goal_id,state,data) VALUES(?,?,?,?,?)",(p["plan"],p["project"],p["goal"],"completed",json.dumps(plan)))
db.execute("INSERT INTO tasks(id,project_id,goal_id,state,data) VALUES(?,?,?,?,?)",(p["task"],p["project"],p["goal"],"ready",json.dumps(child)))`);
  const seededChat=seedChat({title:'Overnight queue',project_id:project.id},[['user','Build the overnight queue.',{}],['assistant','Starting the build.',{build_started:true,goal_id:ids.goal,task_id:ids.task,project:project.name}]]);

  try{
   // 2. Manual integration, a verified candidate waiting: Review opens that exact candidate.
   step(`policy("manual")
approval={k:v for k,v in row("approvals",p["tplApproval"]).items() if k not in ("decision","publication")}
approval.update(candidate=p["candidate"],base=p["base"],covered_tasks=[p["task"]],created=now-20,operation="accept_staged_change")
approval.setdefault("checks",[])
db.execute("INSERT INTO approvals(id,task_id,state,data) VALUES(?,?,?,?)",(p["approval"],p["task"],"pending",json.dumps(approval)))
task=row("tasks",p["task"]);task.update(reason="Combined checks passed; exact staging commit awaits acceptance",staged=p["candidate"],approval_id=p["approval"],updated=now-20)
put("tasks",p["task"],task,"ready_for_approval")`);
   await openChat(page,seededChat);
   // The story's status and its plain need ("A verified change is waiting for your approval.").
   const waiting=await story(ids.goal);
   await cardSays(waiting.needs_you?.[0]?.text||'waiting for your approval','Manual approval');
   r.review={goal:ids.goal,task:ids.task,card:await plainCard('review'),review:await reviews(),status:waiting.status_label};
   if(r.review.review!==1)throw Error('No Review on a build waiting for the owner: '+r.review.card);
   if(!r.review.card.includes(waiting.status_label))throw Error('The card does not show the story status '+waiting.status_label+': '+r.review.card);
   await noOverflow('a build waiting for review');
   await page.screenshot({path:output+'/d-chat-review-card-390.png'});
   await card().getByRole('button',{name:'Review',exact:true}).click();
   const dialog=page.getByRole('dialog');await dialog.waitFor();
   r.review.dialog={title:await dialog.getByRole('heading',{level:2}).first().innerText(),accept:await dialog.getByRole('button',{name:'Accept staged change',exact:true}).count(),candidate:(await flat(dialog)).includes(ids.candidate.slice(0,10))?1:0};
   if(r.review.dialog.title!==ids.taskTitle||r.review.dialog.accept!==1||!r.review.dialog.candidate)throw Error('Review dialog: '+JSON.stringify(r.review.dialog));
   await dialog.getByRole('button',{name:'Close dialog'}).click();await dialog.waitFor({state:'hidden'});

   // 3. Automatic integration: the policy already accepted the candidate and integration is
   // queued. The task still reads "ready for approval", but nothing waits on the owner.
   step(`policy("auto_local_main")
approval=row("approvals",p["approval"])
approval.update(decision={"decision":"accept","by":"integration_policy","policy":"auto_local_main","time":now},publication={"state":"queued","branch":"main","expected_source":p["base"],"requested":now,"automatic":True})
put("approvals",p["approval"],approval,"accept")
task=row("tasks",p["task"]);task.update(reason="Verified; integrating into local main under the project's policy",updated=now)
put("tasks",p["task"],task)`);
   await cardSays('Merging into main','Automatic integration');
   r.auto={card:await plainCard('merging'),review:await reviews()};
   if(r.auto.review)throw Error('Review offered while integrating automatically: '+r.auto.card);
   await page.screenshot({path:output+'/d-chat-integrating-card-390.png'});
   // ...and when that integration fails, the card says so (still no Review).
   step(`approval=row("approvals",p["approval"])
approval["publication"].update(state="failed",reason="Source main moved since the candidate was verified; nothing was changed")
put("approvals",p["approval"],approval)`);
   await cardSays('Merge failed','Failed automatic integration');
   // Why, from the story's plain failure; never the raw publication reason.
   const failed=await story(ids.goal);
   if(!failed.failure?.what_failed)throw Error('The story records no failure for the failed merge: '+JSON.stringify(failed.failure));
   await cardSays(failed.failure.what_failed,'Failed integration, plain reason');
   r.autoFailed={card:await plainCard('failed merge'),review:await reviews(),what:failed.failure.what_failed};
   if(r.autoFailed.review||r.autoFailed.card.includes('Source main moved'))throw Error('Failed integration card: '+r.autoFailed.card);

   // 4. Live: an attempt runs; the card shows the agent, model, elapsed and its last line.
   mkdirSync(ids.artifact,{recursive:true});
   writeFileSync(ids.artifact+'/transcript.jsonl',[{type:'thread.started',thread_id:'acceptance'},{type:'item.completed',item:{id:'i1',type:'agent_message',text:'Running the bell tests.'}},{type:'item.started',item:{id:'i2',type:'command_execution',command:"/bin/zsh -lc 'pytest -q tests/test_bell.py'",status:'in_progress'}}].map(x=>JSON.stringify(x)).join('\n')+'\n');
   step(`approval=row("approvals",p["approval"]);put("approvals",p["approval"],approval,"superseded")
task=row("tasks",p["task"]);task.update(reason="Building",updated=now,approval_id=None,staged=None)
put("tasks",p["task"],task,"running")
attempt={k:v for k,v in row("attempts",p["tplAttempt"]).items() if k not in ("result","ended","identity")}
attempt.update(created=now-75,model="gpt-6.1-sol",effort="high",phase="build",artifact_dir=p["artifact"],identity=None,reason="Routed to Codex")
db.execute("INSERT INTO attempts(id,task_id,account_id,token,state,data) VALUES(?,?,?,?,?,?)",(p["attempt"],p["task"],p["account"],0,"running",json.dumps(attempt)))`);
   // What the agent is doing, in the host's words (the story's `now`), never the command.
   let now=null;for(let n=0;n<30&&!now;n++){now=(await story(ids.goal)).blocks?.find(b=>b.id===ids.task)?.now||null;if(!now)await sleep(500)}
   if(!now)throw Error('The story has no plain "now" for the live block');
   await cardSays(now,'Live build');
   r.live={card:await plainCard('live'),now,agent:codex.name,review:await reviews(),watch:await card().getByRole('button',{name:'Watch the build',exact:true}).count()};
   if(!r.live.card.includes(codex.name+' · gpt-6.1-sol · high')||r.live.review||r.live.watch!==1)throw Error('Live card: '+JSON.stringify(r.live));
   await noOverflow('a live build card');
   await page.screenshot({path:output+'/d-chat-live-card-390.png'});

   // 5. Then it lands on main: the same card turns into "Integrated · sha".
   step(`db.execute("UPDATE attempts SET state='passed',data=json_set(data,'$.ended',?) WHERE id=?",(now,p["attempt"]))
task=row("tasks",p["task"]);task.update(reason="Verified candidate integrated into local main",integrated_commit="c0ffee0"+"1"*33,updated=now)
put("tasks",p["task"],task,"completed")`);
   await cardSays('Merged into main','Integrated after the live build');
   r.live.after=await plainCard('merged');
   r.live.tooltip=await card().locator('.chip[title]').getAttribute('title');
   if(await reviews())throw Error('Review offered on an integrated build: '+r.live.after);
   if(r.live.tooltip!=='Commit c0ffee0 on main')throw Error('The merged commit is not in the tooltip: '+r.live.tooltip);
  }finally{
   step('policy(p["policy"])');  // the copy's project keeps its own integration policy
  }

  // 6. A new chat from the empty state shows the project the host starts it on before
  // anything is sent, so the first message only goes to a project the owner saw.
  const listed=await api(page,'conversations');
  const fresh=await api(page,'overview');
  const usable=new Set(fresh.projects.filter(p=>!p.archived&&p.enabled!==false).map(p=>p.id));
  const expectedId=(listed.find(c=>usable.has(c.project_id))||[...fresh.goals].sort((a,b)=>(b.created||0)-(a.created||0)).find(g=>usable.has(g.project_id))||{}).project_id||null;
  const expected=expectedId?fresh.projects.find(p=>p.id===expectedId).name:'No project selected';
  const known=new Set(listed.map(c=>c.id));
  await page.evaluate(()=>localStorage.removeItem('arena.conversation'));
  await page.reload();await appUp(page);
  await go(page,'Chats');
  try{await page.waitForFunction(()=>!!localStorage.getItem('arena.conversation'),null,{timeout:15000})}catch{throw Error('No chat was started from the empty state; the chip says: '+(await page.locator('.chat-project').innerText()).trim())}
  await page.waitForFunction(n=>(document.querySelector('.chat-project')?.textContent||'').trim()===n,expected,{timeout:15000}).catch(()=>{});
  const createdId=await page.evaluate(()=>localStorage.getItem('arena.conversation'));
  const opened=await api(page,'conversations/'+createdId);
  r.newChat={expected,project:(await page.locator('.chat-project').innerText()).trim(),projectId:opened.project_id,messages:opened.messages?.length,created:!known.has(createdId),placeholder:await page.getByLabel('Message Arena').getAttribute('placeholder')};
  if(r.newChat.project!==expected||r.newChat.projectId!==expectedId||r.newChat.messages!==0||!r.newChat.created)throw Error('New chat: '+JSON.stringify(r.newChat));
  if(expectedId&&r.newChat.placeholder!==`Message Arena about ${expected}…`)throw Error('The composer does not name the project: '+r.newChat.placeholder);
  await noOverflow('a new chat');
  await page.screenshot({path:output+'/d-new-chat-project-390.png'});
  result.slices.D=r;console.log('D',JSON.stringify(r));
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

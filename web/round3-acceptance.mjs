// Round 3 acceptance on a COPY of the real control state (tests/control/real_state_server.py),
// following the owner's definition of done (docs/ROUND3_BRIEF.md §7) at 390 px with an Android
// user agent; dispatch is off, and the live database and service are never touched.
//   1. Home shows only the relevant projects (the archived proof projects are absent).
//   2. Tapping an active build opens its story: real parallel blocks. A live build is seeded in
//      the copy: two running attempts (Claude and Codex), each with a real transcript, plus a
//      block that waits for both.
//   3. Tapping a block explains it in plain English: stage rail, recent updates, Advanced folded.
//   4. What's running on my Mac: managed, discovered and unverified activity, from a fixed
//      discovery fixture (ARENA_DISCOVERY_FIXTURE) for stable screenshots, plus one real
//      discovery call on this machine proving the endpoint runs.
//   5. A finished build is ticked, archived (gone from Home), found under Archived and restored;
//      a running build cannot be ticked, and the host refuses to archive it.
//   6. A real blocked build in the state shows a plain-English failure card, no stack trace.
//   7. Reloading, then closing and reopening, keeps the build live and the screen sane.
// Also: Home's box opens Chat with the turn under way, no horizontal overflow at 360 and 390 px,
// and the running-block wave stands still under reduced motion. Round 3 B fixes, each its own
// section: A "Since you were away" counts each build once, from its blocks; C a real build
// whose work was cancelled reads as cancelled, never finished; M a failed automatic merge is
// stopped, not building; S an agent gone quiet is not shown as live or counted as working;
// T Chat's default view is plain English (no hashes, exit codes or commands); N every "needs
// you" count agrees, and archived work drops out of all of them. ARENA_REAL_DB names the
// database to copy; ARENA_EVIDENCE redirects the output; ARENA_SECTIONS=1,2,L runs some
// sections only (8 is Home → Chat, L the layout checks). Evidence: docs/build-evidence/round3/.
import {spawn, spawnSync} from 'node:child_process';
import {randomBytes} from 'node:crypto';
import {mkdirSync,mkdtempSync,rmSync,utimesSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
const wanted=(process.env.ARENA_SECTIONS||'1,2,3,4,5,6,7,8,A,C,M,S,T,N,L').toUpperCase().split(',').map(x=>x.trim());
const root=process.cwd(),state=mkdtempSync(tmpdir()+'/arena-r3-'),port=process.env.ARENA_UI_PORT||'8798',base='http://127.0.0.1:'+port;
const output=process.env.ARENA_EVIDENCE||root+'/docs/build-evidence/round3';mkdirSync(output,{recursive:true});
const ANDROID='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';
const python=root+'/.venv/bin/python';
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const now=()=>Date.now()/1000;
const hex=()=>randomBytes(8).toString('hex');
const scratch=mkdtempSync(tmpdir()+'/arena-r3-files-');

// The discovery the fixture serves: what an owner's Mac looks like mid-build. Times are set at
// start; the managed entry names the build seeded below.
const ids={goal:'goal_'+hex(),plan:'task_'+hex(),a:'task_'+hex(),b:'task_'+hex(),c:'task_'+hex(),attA:'attempt_'+hex(),attB:'attempt_'+hex()};
const t0=now();
const discovery={
 generated:t0,
 groups:{
  managed:[{kind:'arena_build',label:'Arena build: Overnight queue',repo:'~/Projects/arena-smoke-20261028',project:'Arena Smoke 20261028',pid:null,started:t0-1500,last_activity:t0-40,headless:true,ports:[],evidence:['Attempt workspace ~/.arena/workspaces/attempt_'+ids.attA.slice(8),'Transcript written 40 s ago'],arena:{goal_id:ids.goal,task_id:ids.a,attempt_id:ids.attA}}],
  external:[
   {kind:'claude',label:'Claude Code',repo:'~/Projects/LeoOS',project:null,pid:4242,started:t0-3600,last_activity:t0-120,headless:false,ports:[],evidence:['claude running in ~/Projects/LeoOS','Session file updated 2 min ago','2 subagent sessions written in the last 10 min'],arena:null},
   {kind:'dev_server',label:'Vite development server',repo:'~/Projects/agent-exchange',project:null,pid:5151,started:t0-7200,last_activity:null,headless:true,ports:[5173],evidence:['vite listening on port 5173'],arena:null},
  ],
  unknown:[{kind:'codex',label:'Codex',repo:null,project:null,pid:6262,started:t0-900,last_activity:null,headless:true,ports:[],evidence:['codex process; macOS withheld its working folder'],arena:null}],
 },
 summary:['Arena is building Arena Smoke 20261028: 2 agents.','Claude is working in LeoOS (active 2 min ago).','A development server is running for agent-exchange on port 5173.','A Codex session is running, but macOS withheld its working folder.'],
 limitations:['macOS withheld the working folder of 1 process.','Subagents inside a Claude Code session are inferred from its session files.',"Other users' processes are not inspected."],
};
const fixtureFile=scratch+'/discovery.json';writeFileSync(fixtureFile,JSON.stringify(discovery));

const env={...process.env,PYTHONPATH:root+'/src',ARENA_UI_STATE:state,ARENA_UI_PORT:port,ARENA_DISCOVERY_FIXTURE:fixtureFile};
// A seeded director turn must start after this controller did, or the host rightly closes it
// as one orphaned by a restart.
const spawned=now();
const server=spawn(python,['-m','uvicorn','tests.control.real_state_server:create_app','--factory','--host','127.0.0.1','--port',port],{cwd:root,env,stdio:'pipe'});
let serverLog='';server.stdout.on('data',d=>serverLog+=d);server.stderr.on('data',d=>serverLog+=d);
const result={state,viewport:390,userAgent:ANDROID,dispatch:false,platform:process.platform,checks:{}};
let browser;
// The repository's Playwright (the Mac has its browsers); where only another Playwright
// build has browsers installed (a container), that one: ARENA_PLAYWRIGHT names its module.
async function launch(){
 try{return await (await import('@playwright/test')).chromium.launch()}
 catch(error){try{return await (await import(process.env.ARENA_PLAYWRIGHT||'/opt/npm-tools/node_modules/playwright/index.mjs')).chromium.launch()}catch{throw error}}
}
// Python against the copy with `db` and the JSON payload `p` (seeding the records the UI reads).
const py=(code,payload={})=>{const r=spawnSync(python,['-c','import json,sqlite3,sys,time\ndb=sqlite3.connect(sys.argv[1])\np=json.loads(sys.argv[2])\n'+code+'\ndb.commit()',state+'/control.sqlite3',JSON.stringify(payload)],{encoding:'utf8',env});if(r.status!==0)throw Error('python failed: '+r.stderr);return r.stdout.trim()};
const api=(page,path)=>page.evaluate(async p=>{const r=await fetch('/api/v1/'+p,{cache:'no-store'});return r.ok?await r.json():{__status:r.status}},path);
// A change through the host's own API, as the app makes it (session CSRF, idempotency key).
const apiPost=(page,path,body)=>page.evaluate(async([p,b])=>{const s=await (await fetch('/api/v1/session')).json();const r=await fetch('/api/v1/'+p,{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf,'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify(b)});return {status:r.status,body:await r.json().catch(()=>null)}},[path,body]);
// The director's plan task, by the host's rule (story.is_plan_task).
const isPlan=t=>!t.planner_attempt&&(t.phase==='plan'||t.phase==='replan'||'planned_tasks' in t);
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
const flat=async loc=>(await loc.innerText()).replace(/\s+/g,' ').trim();
// The bar a build's progress should draw, from its story: width from the host's stage-weighted
// fraction; "0 of 3 done · 2 under way · 1 waiting" (waiting: neither done, under way nor stopped).
const expectedBar=s=>{const p=s.progress,stopped=(s.blocks||[]).filter(b=>b.state==='blocked').length,waiting=Math.max(0,p.total-p.done-(p.in_progress||0)-stopped);return {width:Math.min(100,Math.max(0,Math.round((typeof p.fraction==='number'?p.fraction:p.total?p.done/p.total:0)*100)))+'%',label:`${p.done} of ${p.total} done${p.in_progress?` · ${p.in_progress} under way`:''}${waiting?` · ${waiting} waiting`:''}`}};
// A build's title as plain views show it: without the composer's legacy chip prefix.
const plainTitle=t=>{const s=String(t||'').replace(/^\s*(?:add to an existing project|build a new feature|fix a bug|improve arena)\s*:\s*/i,'').trim();return s?s[0].toUpperCase()+s.slice(1):String(t||'')};
// Open a screen from the menu: the ☰ sheet on a phone, the sidebar on a wide screen.
const go=async(page,name)=>{const menu=page.getByRole('button',{name:/^Menu\b/}),side=page.getByRole('navigation',{name:'Primary'});await menu.or(side).first().waitFor();if(await menu.isVisible()){await menu.click();await page.getByRole('dialog',{name:'Menu'}).getByRole('button',{name,exact:true}).click();}else await side.getByRole('button',{name,exact:true}).click();};
const home=async page=>{await go(page,'Home');await page.getByLabel('Tell Arena what to build').waitFor();};
const shot=(page,name,full=false)=>page.screenshot({path:`${output}/r3-${name}.png`,fullPage:full});
// Owner-facing text must not show machinery: hashes, file names or paths, stack traces, exit codes.
const MACHINERY=[[/\b(?=[0-9a-f]*[a-f])(?=[0-9a-f]*\d)[0-9a-f]{7,40}\b/,'a commit hash'],[/\b[\w-]+\.(?:py|tsx?|jsx?|json|toml|ya?ml|sh|md|css|html)\b/,'a file name'],[/(?:^|\s)(?:~|\.{0,2})\/[\w.-]+\/[\w./-]+/,'a path'],[/Traceback|File "|exit=-?\d+|exit code|stderr/i,'terminal output']];
const machinery=text=>MACHINERY.map(([re,what])=>{const m=re.exec(text);return m?`${what} ("${m[0].trim()}")`:''}).filter(Boolean);
// Every visible control has an accessible name (label, aria-label/-labelledby, or its text).
const unnamed=page=>page.evaluate(()=>{
 const named=el=>{
  const aria=el.getAttribute('aria-label');if(aria&&aria.trim())return true;
  const by=el.getAttribute('aria-labelledby');if(by&&by.split(/\s+/).some(id=>(document.getElementById(id)?.textContent||'').trim()))return true;
  if(el.id&&[...document.querySelectorAll('label')].some(l=>l.htmlFor===el.id&&l.textContent.trim()))return true;
  if(el.tagName==='INPUT'&&el.closest('label')?.textContent.trim())return true;
  if(['BUTTON','A','SUMMARY'].includes(el.tagName)&&el.textContent.trim())return true;
  return !!(el.getAttribute('title')||'').trim();
 };
 return [...document.querySelectorAll('button,a[href],input,select,textarea,summary,[role=button]')].filter(el=>el.getClientRects().length&&getComputedStyle(el).visibility!=='hidden').filter(el=>!named(el)).map(el=>el.outerHTML.slice(0,140));
});
const visibleText=page=>page.evaluate(()=>{
 // Visible text of main, without anything inside a closed <details> (Advanced) or hidden.
 const out=[];const walk=n=>{if(n.nodeType===3){const p=n.parentElement;if(p&&p.offsetParent!==null)out.push(n.textContent);return}if(n.nodeType!==1)return;if(n.tagName==='DETAILS'&&!n.open){const s=n.querySelector('summary');if(s)out.push(s.textContent);return}for(const c of n.childNodes)walk(c)};walk(document.querySelector('main'));return out.join(' ').replace(/\s+/g,' ')});

// Claude stream-json and Codex JSON lines, as the workers write them.
const claudeTranscript=[{type:'system',subtype:'init',model:'claude-sonnet-4-5'},{type:'assistant',message:{content:[{type:'text',text:'I am adding a durable store for the overnight queue so queued builds survive a restart of the Mac.'}]}},{type:'assistant',message:{content:[{type:'tool_use',name:'Edit',input:{file_path:'/Users/leo/arena/workspaces/x/src/pomodoro/queue_store.py'}}]}}];
const codexTranscript=[{type:'thread.started',thread_id:'acceptance'},{type:'item.completed',item:{id:'i1',type:'agent_message',text:'Wiring the overnight scheduling rules and running their tests.'}},{type:'item.started',item:{id:'i2',type:'command_execution',command:"/bin/zsh -lc 'pytest -q tests/test_overnight_rules.py'",status:'in_progress'}}];

try{
 for(let n=0;n<80;n++){if(server.exitCode!==null)throw Error('Fixture server exited with '+server.exitCode+' (is port '+port+' free?)\n'+serverLog.slice(-800));try{if((await fetch(base)).ok)break}catch{}await sleep(250);}

 // Seed the live build on the copy, on the active project with the newest integrated work
 // (cloned fields from that project's own records, so the shape is the host's).
 const seeded=JSON.parse(py(`rows=lambda q,*a:[dict(r) for r in db.execute(q,a)]
db.row_factory=sqlite3.Row
projects=[{**json.loads(r["data"]),"id":r["id"]} for r in db.execute("SELECT id,data FROM projects")]
live=[x for x in projects if not x.get("archived")]
tasks=[{**json.loads(r["data"]),"id":r["id"],"state":r["state"],"project_id":r["project_id"],"goal_id":r["goal_id"]} for r in db.execute("SELECT * FROM tasks")]
done=[t for t in tasks if t.get("integrated_commit") and any(x["id"]==t["project_id"] for x in live)]
done.sort(key=lambda t:t.get("updated",0),reverse=True)
tpl=done[0]
project=next(x for x in live if x["id"]==tpl["project_id"])
plan=next((t for t in tasks if t["goal_id"]==tpl["goal_id"] and t.get("director") and t.get("phase")=="plan"),tpl)
att=db.execute("SELECT id,data FROM attempts WHERE task_id=?",(tpl["id"],)).fetchone()
accounts=[{**json.loads(r["data"]),"id":r["id"]} for r in db.execute("SELECT id,data FROM accounts")]
claude=next(a for a in accounts if a.get("provider")=="claude")
codex=next(a for a in accounts if a.get("provider")=="codex")
print(json.dumps({"project":project["id"],"name":project["name"],"tplTask":tpl["id"],"tplPlan":plan["id"],"tplAttempt":att["id"],"claude":claude["id"],"codex":codex["id"]}))`));
 const live={...ids,...seeded,dirA:scratch+'/attempt-a',dirB:scratch+'/attempt-b',title:'Add an overnight queue that survives restarts'};
 // Both agents wrote to their transcripts just now (a long run must not leave them looking quiet).
 const freshen=()=>{const t=Date.now()/1000;for(const d of [live.dirA,live.dirB])utimesSync(d+'/transcript.jsonl',t,t)};
 mkdirSync(live.dirA,{recursive:true});mkdirSync(live.dirB,{recursive:true});
 writeFileSync(live.dirA+'/transcript.jsonl',claudeTranscript.map(x=>JSON.stringify(x)).join('\n')+'\n');
 writeFileSync(live.dirB+'/transcript.jsonl',codexTranscript.map(x=>JSON.stringify(x)).join('\n')+'\n');
 py(`now=time.time()
row=lambda table,ident:json.loads(db.execute(f"SELECT data FROM {table} WHERE id=?",(ident,)).fetchone()[0])
strip=("integrated_commit","approval_id","staged","candidate","candidate_base","build_attempt","review_attempt","director_review_attempt","attempt_id","question","cancel_requested","repair_evidence")
base={k:v for k,v in row("tasks",p["tplTask"]).items() if k not in strip}
spec={"outcome":"Queue requests during the day and run them overnight without losing anything.","summary":"Two blocks run side by side, the durable queue and the scheduling rules; a morning summary follows once both are done.","strategy":"parallel","acceptance":["Queued builds survive a restart","The cheapest queued work runs first","A morning summary lists what finished"],"constraints":[],"observations":[]}
plan={**row("tasks",p["tplPlan"]),"title":p["title"],"phase":"plan","director":True,"created":now-1500,"updated":now-1420,"reason":"Planned three blocks: two run in parallel, the third waits for both","specification":spec}
db.execute("INSERT INTO goals(id,project_id,data) VALUES(?,?,?)",(p["goal"],p["project"],json.dumps({"title":p["title"],"request":p["title"]+". Queue requests during the day and run them overnight, economically, without losing anything if the Mac restarts.","created":now-1500})))
db.execute("INSERT INTO tasks(id,project_id,goal_id,state,data) VALUES(?,?,?,?,?)",(p["plan"],p["project"],p["goal"],"completed",json.dumps(plan)))
blocks=[(p["a"],"running","Durable queue storage","Keeps queued builds safe when the Mac restarts, so overnight work is never lost.","M1 — Queue storage",now-1400),
        (p["b"],"running","Overnight scheduling rules","Decides which queued build runs next overnight, so the cheapest work runs first.","M2 — Scheduling",now-1390),
        (p["c"],"waiting_dependency","Morning summary of queued work","Tells you each morning what the overnight queue finished.","M3 — Morning summary",now-1380)]
for ident,st,title,purpose,milestone,created in blocks:
    t={**base,"title":title,"purpose":purpose,"milestone":milestone,"phase":"build","director":True,"created":created,"updated":now-60,"reason":"Building" if st=="running" else "Waiting for its dependencies","repairs":0}
    db.execute("INSERT INTO tasks(id,project_id,goal_id,state,data) VALUES(?,?,?,?,?)",(ident,p["project"],p["goal"],st,json.dumps(t)))
db.execute("INSERT INTO task_dependencies(task_id,dependency_id) VALUES(?,?)",(p["c"],p["a"]))
db.execute("INSERT INTO task_dependencies(task_id,dependency_id) VALUES(?,?)",(p["c"],p["b"]))
tpl={k:v for k,v in row("attempts",p["tplAttempt"]).items() if k not in ("result","ended","identity")}
for ident,task,account,model,started,folder in [(p["attA"],p["a"],p["claude"],"sonnet",now-900,p["dirA"]),(p["attB"],p["b"],p["codex"],"gpt-6-sol",now-600,folder_b:=p["dirB"])]:
    a={**tpl,"created":started,"model":model,"effort":"high","phase":"build","artifact_dir":folder,"identity":None,"reason":"Routed by the director"}
    db.execute("INSERT INTO attempts(id,task_id,account_id,token,state,data) VALUES(?,?,?,?,?,?)",(ident,task,account,0,"running",json.dumps(a)))
ev=lambda t,kind,**d:db.execute("INSERT INTO events(time,kind,data) VALUES(?,?,?)",(t,kind,json.dumps({"schema":1,**d})))
ev(now-1500,"goal.created",goal_id=p["goal"],title=p["title"])
ev(now-1420,"task.changed",task_id=p["plan"],state="completed",reason="Planned three blocks")
ev(now-1380,"task.changed",task_id=p["c"],state="waiting_dependency",reason="Waiting for its dependencies")
ev(now-900,"routing.selected",task_id=p["a"],attempt_id=p["attA"],provider="claude",model="sonnet",reason="Director's choice")
ev(now-900,"task.changed",task_id=p["a"],state="running",reason="Bounded worker started")
ev(now-600,"routing.selected",task_id=p["b"],attempt_id=p["attB"],provider="codex",model="gpt-6-sol",reason="Director's choice")
ev(now-600,"task.changed",task_id=p["b"],state="running",reason="Bounded worker started")
# Every agent slot off in the copy: a director turn (a block question, Home's box) answers at
# once that no slot is on, and no CLI ever runs from this acceptance.
db.execute("UPDATE accounts SET data=json_set(data,'$.enabled',json('false'))")`,live);
 result.seeded={goal:ids.goal,project:live.name,blocks:[ids.a,ids.b,ids.c]};

 browser=await launch();
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:ANDROID,deviceScaleFactor:2});
 let page=await context.newPage();const errors=[];const watch=p=>p.on('pageerror',e=>errors.push(e.message));watch(page);
 await page.goto(base);
 await page.getByLabel('One-time pairing code').fill('real-state-one-use-code');
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByLabel('Tell Arena what to build').waitFor();
 const snap=await api(page,'overview');

 // 1. Home: only relevant projects. No archived project anywhere on Home, finished folded open.
 if(wanted.includes('1')){
  const r={};
  const archived=snap.projects.filter(p=>p.archived).map(p=>p.name);
  const active=snap.projects.filter(p=>!p.archived).map(p=>p.name);
  await page.getByRole('button',{name:/^Building /}).first().waitFor({timeout:15000});
  await shot(page,'home');
  const finished=page.getByRole('button',{name:/^Finished/});
  await finished.click();
  const text=await page.locator('main').innerText();
  r.archivedProjects=archived;r.activeProjects=active;
  r.shownArchived=archived.filter(n=>text.includes(n));
  r.rows=await page.locator('.active-row').allInnerTexts().then(a=>a.map(s=>s.replace(/\s+/g,' ').trim()));
  r.ready=await page.locator('.ready-row b').allInnerTexts();
  if(!archived.length)throw Error('The copy has no archived project to prove the filter');
  if(r.shownArchived.length)throw Error('Archived projects on Home: '+r.shownArchived.join(', '));
  const legacy=/(?:add to an existing project|build a new feature|fix a bug|improve arena)\s*:/i.exec(text);
  if(legacy)throw Error('Home shows a legacy title prefix: '+legacy[0]);
  if(!r.rows.some(s=>s.startsWith('Building '+live.name)))throw Error('No "Building '+live.name+'" row: '+JSON.stringify(r.rows));
  if(!/2 agents working/.test(r.rows.join(' ')))throw Error('The live row does not count two working agents: '+r.rows.join(' | '));
  // Progress credits the stages the running blocks have reached (the host's stage-weighted
  // fraction) and says what is under way, exactly as the build's story reports it.
  await page.waitForTimeout(1500); // the build's story (progress) is read once Home shows it
  const homeStory=await api(page,'goals/'+ids.goal+'/story');
  r.progress=homeStory.progress;
  r.expected=expectedBar(homeStory);
  // Two blocks are worked on now and the third waits for both.
  if(r.expected.label!=='0 of 3 done · 2 under way · 1 waiting')throw Error('Progress label for two running blocks and one waiting: '+r.expected.label);
  r.bar={width:await page.locator('.active-row .progress i').first().evaluate(i=>i.style.width),label:await page.locator('.active-row .sr-only').first().innerText()};
  if(!(r.progress.fraction>0)||!(r.progress.in_progress>0)||r.progress.done!==0||r.progress.total!==3)throw Error('The live build reports no progress under way: '+JSON.stringify(r.progress));
  if(r.bar.width==='0%'||r.bar.width!==r.expected.width||r.bar.label!==r.expected.label)throw Error('Home progress: '+JSON.stringify(r));
  const words=machinery(await visibleText(page));if(words.length)throw Error('Home shows '+words.join(', '));
  await finished.click();
  result.checks.home=r;console.log('1',JSON.stringify(r));
 }

 // 2. Tap the active build: its story, with the parallel blocks in dependency order.
 if(wanted.includes('2')){
  const r={};
  await page.getByRole('button',{name:new RegExp('^Building '+live.name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&'))}).click();
  await page.locator('.timeline-blocks .block-card').nth(2).waitFor({timeout:15000});
  const story=await api(page,'goals/'+ids.goal+'/story');
  r.status=story.status;r.progress=story.progress;r.agents=story.agents_working;
  r.blocks=story.blocks.map(b=>({title:b.title,state:b.state,label:b.state_label,agent:b.agent&&`${b.agent.provider} ${b.agent.model}`,now:b.now,connects:b.connects.map(c=>c.relation+':'+c.title)}));
  r.cards=(await page.locator('.timeline-blocks .block-card').allInnerTexts()).map(s=>s.replace(/\s+/g,' ').trim());
  r.head=await flat(page.locator('.screen-head'));
  const running=story.blocks.filter(b=>b.state==='running');
  if(running.length!==2)throw Error('Expected two running blocks: '+JSON.stringify(r.blocks));
  if(!running.some(b=>b.agent?.provider==='claude')||!running.some(b=>b.agent?.provider==='codex'))throw Error('The parallel blocks are not Claude and Codex: '+JSON.stringify(r.blocks));
  if(running.some(b=>!b.now))throw Error('A running block has no live description: '+JSON.stringify(r.blocks));
  const waiting=story.blocks.find(b=>b.title===live.c||b.connects.filter(c=>c.relation==='needs').length===2);
  if(!waiting)throw Error('The waiting block does not say what it needs: '+JSON.stringify(r.blocks));
  if(story.blocks[story.blocks.length-1].connects.filter(c=>c.relation==='needs').length!==2)throw Error('Blocks are not in dependency order: '+JSON.stringify(r.blocks));
  if(!r.cards.some(c=>c.includes('Claude (sonnet)'))||!r.cards.some(c=>c.includes('Codex (gpt-6-sol)')))throw Error('Cards do not name the agents: '+r.cards.join(' | '));
  for(const b of running)if(!r.cards.some(c=>c.includes(b.now)))throw Error('Card does not show what the agent is doing now: '+b.now);
  if(!/2 agents working/.test(r.head))throw Error('Story head: '+r.head);
  r.bar={width:await page.locator('.story-progress .progress i').evaluate(i=>i.style.width),label:await flat(page.locator('.story-progress'))};
  r.expected=expectedBar(story);
  if(r.bar.label!==r.expected.label||r.bar.width!==r.expected.width)throw Error('Story progress: '+JSON.stringify({bar:r.bar,expected:r.expected}));
  if(result.checks.home&&r.bar.width!==result.checks.home.bar.width)throw Error('Home and the story disagree on progress: '+JSON.stringify({home:result.checks.home.bar,story:r.bar}));
  if(await page.locator('.block-card.running .wave').count()!==2)throw Error('Running blocks lack the accent wave');
  if(!(await page.getByRole('button',{name:'Stop build',exact:true}).isVisible()))throw Error('No Stop build while the build runs');
  await page.evaluate(()=>window.scrollTo(0,0));
  await shot(page,'story');await shot(page,'story-full',true);
  // Stop asks first (the fixture would refuse the stop itself); Keep building leaves it be.
  await page.getByRole('button',{name:'Stop build',exact:true}).click();
  const confirm=page.getByRole('group',{name:'Stop this build?'});await confirm.waitFor();
  r.confirm=await flat(confirm);
  await confirm.getByRole('button',{name:'Keep building',exact:true}).click();
  await confirm.waitFor({state:'detached'});
  const words=machinery(await visibleText(page));if(words.length)throw Error('The story shows '+words.join(', ')+': '+(await visibleText(page)).slice(0,400));
  if(await overflow(page))throw Error('Horizontal overflow on the story');
  result.checks.story=r;console.log('2',JSON.stringify(r));
 }

 // 3. Tap a block: what it is doing and why, the stage rail, updates (live), Advanced folded.
 if(wanted.includes('3')){
  const r={};
  if(!(await page.locator('.timeline-blocks .block-card').count())){await home(page);await page.getByRole('button',{name:/^Building /}).first().click();await page.locator('.timeline-blocks .block-card').nth(2).waitFor({timeout:15000});}
  await page.locator('.block-card.agent-claude').first().click();
  await page.getByRole('heading',{name:'What this block is doing'}).waitFor({timeout:15000});
  const detail=await api(page,'tasks/'+ids.a+'/story');
  r.what=detail.what;r.why=detail.why;r.connects=detail.connects_text;r.rail=detail.stage_rail;r.updates=detail.updates.slice(0,6);r.now=detail.now;
  r.railShown=await page.locator('.progress-rail li').allInnerTexts();
  r.live=await page.locator('.updates-card').getByText('Live',{exact:true}).count();
  r.advancedOpen=await page.locator('details.advanced').evaluate(d=>d.open);
  r.nowShown=await flat(page.locator('.updates li.now'));
  if(r.railShown.join('·')!=='Design·Implement·Test·Review·Merge')throw Error('Stage rail: '+r.railShown.join('·'));
  if(!r.live)throw Error('No Live chip on a live block');
  if(r.advancedOpen)throw Error('Advanced is open by default');
  if(!detail.now||!r.nowShown.includes(detail.now))throw Error('"Now" is not the first update: '+r.nowShown);
  if(!detail.what||!(await flat(page.locator('.plain-card'))).includes(detail.what))throw Error('What the block does is not shown');
  const words=machinery(await visibleText(page));if(words.length)throw Error('The block detail shows '+words.join(', ')+' outside Advanced');
  if(await overflow(page))throw Error('Horizontal overflow on the block detail');
  await shot(page,'block');await shot(page,'block-full',true);
  // Advanced opens on request and holds the technical record.
  await page.locator('details.advanced > summary').click();
  r.advanced=await flat(page.locator('details.advanced'));
  await shot(page,'block-advanced',true);
  // A question about this block goes to the director with the block named, then opens Chat.
  r.question='Why does this block need its own storage?';
  await page.getByLabel('Ask a question about this block').fill(r.question);
  await page.getByRole('button',{name:'Ask the director',exact:true}).click();
  await page.getByLabel('Message Arena').waitFor();
  await page.waitForFunction(q=>[...document.querySelectorAll('.msg.user .bubble')].some(b=>b.textContent.includes(q)),r.question,{timeout:15000});
  // The bubble shows at once (optimistically); the host records the message when its POST lands.
  const cid=await page.evaluate(()=>localStorage.getItem('arena.conversation'));
  let conv;
  for(const until=Date.now()+15000;;await sleep(300)){
   conv=await api(page,'conversations/'+cid);
   if(conv.messages?.some(m=>m.role==='user'&&m.text===r.question)||Date.now()>until)break;
  }
  r.chat={project:conv.project?.name||null,asked:!!conv.messages?.some(m=>m.role==='user'&&m.text===r.question)};
  if(!r.chat.asked)throw Error('The block question did not reach the conversation');
  if(r.chat.project!==live.name)throw Error('The conversation is not on the block\'s project: '+r.chat.project);
  await shot(page,'block-question-chat');
  result.checks.block=r;console.log('3',JSON.stringify({...r,advanced:r.advanced.slice(0,200)}));
 }

 // 4. What's running on my Mac: three groups, plain sentences, limitations; one real call.
 if(wanted.includes('4')){
  const r={};
  await go(page,"What's running on my Mac");
  await page.getByRole('heading',{name:'In short'}).waitFor({timeout:15000});
  for(const [key,title] of [['managed','Managed by Arena'],['external','Discovered on your Mac'],['unknown',"Couldn't verify"]]){
   const group=page.getByRole('region',{name:title});
   r[key]={title,rows:await group.locator('.activity-row').count(),text:await flat(group)};
   if(r[key].rows!==discovery.groups[key].length)throw Error(`${title}: ${r[key].rows} rows, fixture has ${discovery.groups[key].length}`);
  }
  r.summary=await page.locator('.mac-summary li').allInnerTexts();
  r.limits=await page.locator('.mac-limits li').allInnerTexts();
  if(r.summary.join('|')!==discovery.summary.join('|'))throw Error('Summary differs from the discovery: '+r.summary.join(' | '));
  if(r.limits.length!==discovery.limitations.length)throw Error('Limitations not shown');
  if(!/active 2 min ago/.test(r.external.text))throw Error('No "active N min ago": '+r.external.text);
  if(await overflow(page))throw Error('Horizontal overflow on the Mac screen');
  await shot(page,'mac');await shot(page,'mac-full',true);
  // The real discovery of this machine through the same endpoint (read-only; the fixture
  // server answers it for this header): it runs, states its limits, and reconciles the
  // seeded live build, whose agents have no process here, as managed but unconfirmed.
  r.real=await page.evaluate(async()=>{const res=await fetch('/api/v1/mac-activity',{cache:'no-store',headers:{'X-Arena-Discovery':'real'}});const j=await res.json();return {status:res.status,groups:Object.fromEntries(Object.entries(j.groups||{}).map(([k,v])=>[k,v.length])),managed:(j.groups?.managed||[]).map(a=>a.label),summary:j.summary||[],limitations:j.limitations||[]}});
  if(r.real.status!==200)throw Error('Real discovery failed: '+JSON.stringify(r.real));
  if(!r.real.limitations.length)throw Error('The real discovery states no limitations');
  if(!r.real.managed.some(l=>/no agent process was found/i.test(l)))throw Error('Live Arena work with no process is not shown as unconfirmed: '+JSON.stringify(r.real.managed));
  // A director reply about the Mac carries the discovery and renders the same view, compactly.
  const cid='conv_'+hex();
  py(`now=time.time()
db.execute("INSERT INTO conversations(id,data) VALUES(?,?)",(p["id"],json.dumps({"title":"What's running on my Mac?","created":now,"updated":now,"project_id":None,"spec":None,"status":"idle"})))
db.execute("INSERT INTO messages(conversation_id,time,role,text,data) VALUES(?,?,?,?,?)",(p["id"],now,"user","What's running on my Mac?","{}"))
db.execute("INSERT INTO messages(conversation_id,time,role,text,data) VALUES(?,?,?,?,?)",(p["id"],now,"assistant","Arena is building Arena Smoke 20261028 with two agents. Outside Arena, Claude is working in LeoOS and a development server is running for agent-exchange. One Codex session could not be verified.",json.dumps({"mac_activity":p["mac"]})))`,{id:cid,mac:discovery});
  await page.evaluate(i=>localStorage.setItem('arena.conversation',i),cid);
  await go(page,'Chats');
  const card=page.locator('.msg.arena .mac-activity.compact').last();
  await card.waitFor({timeout:15000});
  r.chatCard={groups:await card.locator('.group-title').allInnerTexts(),rows:await card.locator('.activity-row').count()};
  if(r.chatCard.rows!==4||r.chatCard.groups.length!==3)throw Error('Chat reply card: '+JSON.stringify(r.chatCard));
  if(await overflow(page))throw Error('Horizontal overflow on the chat Mac card');
  await card.scrollIntoViewIfNeeded();
  await shot(page,'mac-in-chat');
  result.checks.mac=r;console.log('4',JSON.stringify({...r,managed:r.managed.rows,external:r.external.rows,unknown:r.unknown.rows}));
 }

 // 5. Archive a finished build with a checkbox, then restore it; a running build never.
 if(wanted.includes('5')){
  const r={};
  const before=await api(page,'overview');
  const archivedIds=new Set(before.projects.filter(p=>p.archived).map(p=>p.id));
  const isPlan=t=>t.director&&(t.phase==='plan'||t.phase==='replan');
  const finished=before.goals.filter(g=>!archivedIds.has(g.project_id)&&!g.archived).filter(g=>{const work=before.tasks.filter(t=>t.goal_id===g.id&&!isPlan(t)&&t.state!=='cancelled');return work.length&&work.every(t=>t.state==='completed')}).sort((a,b)=>b.created-a.created);
  const target=finished.find(g=>!before.goals.some(o=>o.id!==g.id&&plainTitle(o.display_title)===plainTitle(g.display_title)))||finished[0];
  if(!target)throw Error('No finished build to archive');
  target.display_title=plainTitle(target.display_title);
  r.target={id:target.id,title:target.display_title};
  const records=snapshotCounts(before,target.id);
  await home(page);
  await page.getByRole('button',{name:'Select',exact:true}).click();
  // The running build's box is there, disabled, and says why.
  const runningBox=page.getByRole('checkbox',{name:new RegExp('^Building '+live.name.replace(/[.*+?^${}()|[\]\\]/g,'\\$&')+' is running')});
  r.runningDisabled=await runningBox.isDisabled();
  if(!r.runningDisabled)throw Error('A running build can be selected');
  const box=page.getByRole('checkbox',{name:'Select '+target.display_title,exact:true});
  await box.check();
  await shot(page,'select');
  await page.getByRole('button',{name:'Archive selected (1)',exact:true}).click();
  await page.getByRole('status').filter({hasText:'Archived 1 build'}).waitFor({timeout:10000});
  const after=await api(page,'overview');
  r.archived=after.goals.find(g=>g.id===target.id)?.archived;
  if(!r.archived)throw Error('The host did not record the archive');
  const finishedToggle=page.getByRole('button',{name:/^Finished/});
  if(await finishedToggle.getAttribute('aria-expanded')==='false')await finishedToggle.click();
  r.goneFromHome=!(await page.locator('main').innerText()).includes(target.display_title);
  if(!r.goneFromHome)throw Error('The archived build is still on Home');
  await shot(page,'archived-home',true);
  // The host refuses to archive the running build even if asked directly.
  r.refused=await page.evaluate(async goal=>{const s=await (await fetch('/api/v1/session')).json();const res=await fetch('/api/v1/goals/archive',{method:'POST',headers:{'Content-Type':'application/json','X-CSRF-Token':s.csrf,'Idempotency-Key':crypto.randomUUID()},body:JSON.stringify({ids:[goal]})});return await res.json()},ids.goal);
  if(r.refused.archived?.length||r.refused.skipped?.[0]?.reason!=='still running')throw Error('Archiving a running build: '+JSON.stringify(r.refused));
  // Archived lists it; restoring brings it back to Home with every record intact.
  await go(page,'Archived');
  await page.getByRole('checkbox',{name:'Select '+target.display_title,exact:true}).check();
  await shot(page,'archived');
  await page.getByRole('button',{name:'Restore selected (1)',exact:true}).click();
  await page.getByRole('status').filter({hasText:'Restored 1 build'}).waitFor({timeout:10000});
  const restored=await api(page,'overview');
  r.restored=restored.goals.find(g=>g.id===target.id)?.archived===false;
  if(!r.restored)throw Error('The build was not restored');
  const recordsAfter=snapshotCounts(restored,target.id);
  if(JSON.stringify(records)!==JSON.stringify(recordsAfter))throw Error('Records changed by archive/restore: '+JSON.stringify({records,recordsAfter}));
  r.records=records;
  await home(page);
  if(await finishedToggle.getAttribute('aria-expanded')==='false')await finishedToggle.click();
  if(!(await page.locator('main').innerText()).includes(target.display_title))throw Error('The restored build is not back on Home');
  await shot(page,'restored',true);
  // A Ready project is ticked the same way, archived (its builds leave Home) and restored.
  const projectName=after.projects.find(p=>!p.archived&&p.id!==live.project)?.name;
  if(projectName){
   await page.getByRole('button',{name:'Select',exact:true}).click();
   await page.getByRole('checkbox',{name:'Select project '+projectName,exact:true}).check();
   await page.getByRole('button',{name:'Archive selected (1)',exact:true}).click();
   await page.getByRole('status').filter({hasText:'Archived 1 project'}).waitFor({timeout:10000});
   r.projectArchived=!(await page.locator('.ready-row b').allInnerTexts()).includes(projectName);
   if(!r.projectArchived)throw Error('The archived project is still Ready on Home');
   await go(page,'Archived');
   await page.getByRole('checkbox',{name:'Select project '+projectName,exact:true}).check();
   await page.getByRole('button',{name:'Restore selected (1)',exact:true}).click();
   await page.getByRole('status').filter({hasText:'Restored 1 project'}).waitFor({timeout:10000});
   await home(page);
   r.projectRestored=(await page.locator('.ready-row b').allInnerTexts()).includes(projectName);
   if(!r.projectRestored)throw Error('The restored project is not back on Home');
  }
  result.checks.archive=r;console.log('5',JSON.stringify(r));
 }

 // 6. A real blocked build: plain-English failure card, an action, no stack trace.
 if(wanted.includes('6')){
  const r={};
  const s=await api(page,'overview');
  const archivedIds=new Set(s.projects.filter(p=>p.archived).map(p=>p.id));
  const blocked=s.goals.filter(g=>!archivedIds.has(g.project_id)&&!g.archived&&s.tasks.some(t=>t.goal_id===g.id&&t.state==='blocked')).sort((a,b)=>b.created-a.created);
  const unique=blocked.find(g=>!s.goals.some(o=>o.id!==g.id&&plainTitle(o.display_title)===plainTitle(g.display_title)))||blocked[0];
  if(!unique)throw Error('The copy has no blocked build');
  unique.display_title=plainTitle(unique.display_title);
  const raw=s.tasks.filter(t=>t.goal_id===unique.id&&t.state==='blocked').map(t=>t.reason);
  r.goal={id:unique.id,title:unique.display_title,rawReasons:raw};
  await home(page);
  const finishedToggle=page.getByRole('button',{name:/^Finished/});
  if(await finishedToggle.getAttribute('aria-expanded')==='false')await finishedToggle.click();
  await page.locator('.finished-row',{hasText:unique.display_title}).first().click();
  const card=page.getByRole('region',{name:'What went wrong'});
  await card.waitFor({timeout:15000});
  r.card=await flat(card);
  const story=await api(page,'goals/'+unique.id+'/story');
  r.failure=story.failure;
  for(const k of ['what_failed','intended','next'])if(!story.failure?.[k])throw Error('Failure has no '+k+': '+JSON.stringify(story.failure));
  if(!r.card.includes(story.failure.what_failed))throw Error('The card does not say what failed');
  const words=machinery(await visibleText(page));if(words.length)throw Error('The failure view shows '+words.join(', '));
  if(raw.some(reason=>reason&&/exit=|Traceback/.test(reason)&&r.card.includes(reason)))throw Error('The raw technical reason is on the card');
  if(await overflow(page))throw Error('Horizontal overflow on the failure view');
  await shot(page,'failure');
  result.checks.failure=r;console.log('6',JSON.stringify(r));
 }

 // 7. Reload, then close and reopen: the build stays live and the screen state is sane.
 if(wanted.includes('7')){
  const r={};
  await home(page);
  await page.getByRole('button',{name:/^Building /}).first().click();
  await page.locator('.timeline-blocks .block-card').first().waitFor();
  await page.reload();
  await page.locator('.timeline-blocks .block-card').nth(2).waitFor({timeout:15000});
  r.afterReload={screen:await page.evaluate(()=>history.state?.arena),running:await page.locator('.block-card.running').count()};
  if(r.afterReload.screen?.tab!=='story'||r.afterReload.screen?.goal!==ids.goal)throw Error('Reload lost the screen: '+JSON.stringify(r.afterReload));
  if(r.afterReload.running!==2)throw Error('After reload the blocks are not running');
  // Back from the restored story goes somewhere sane (the previous screen, else Home).
  await page.getByRole('button',{name:'Back',exact:true}).click();
  await page.getByLabel('Tell Arena what to build').waitFor({timeout:10000});
  await page.close();
  await sleep(2000);
  page=await context.newPage();watch(page);
  await page.goto(base);
  await page.getByLabel('Tell Arena what to build').waitFor();
  await page.getByRole('button',{name:/^Building /}).first().waitFor({timeout:15000});
  const s=await api(page,'overview');
  r.attempts=s.attempts.filter(a=>a.id===ids.attA||a.id===ids.attB).map(a=>a.state);
  if(r.attempts.join()!=='running,running'&&r.attempts.sort().join()!=='running,running')throw Error('The build stopped while the app was closed: '+r.attempts.join());
  await shot(page,'reopened');
  result.checks.reopen=r;console.log('7',JSON.stringify(r));
 }

 // Home's box opens Chat with the turn under way (in the current conversation).
 if(wanted.includes('8')){
  const r={};
  await home(page);
  r.text='What is running on my Mac right now?';
  await page.getByLabel('Tell Arena what to build').fill(r.text);
  await page.getByRole('button',{name:'Send to Arena',exact:true}).click();
  await page.getByLabel('Message Arena').waitFor();
  await page.waitForFunction(q=>[...document.querySelectorAll('.msg.user .bubble')].some(b=>b.textContent.includes(q)),r.text,{timeout:15000});
  // The turn runs in that conversation: here the director answers at once that every slot is
  // switched off (the copy's slots are off so no CLI runs).
  const cid=await page.evaluate(()=>localStorage.getItem('arena.conversation'));
  let conv=null,asked=-1;
  for(let n=0;n<30&&!r.reply;n++){
   conv=await api(page,'conversations/'+cid);
   asked=conv.messages.findIndex(m=>m.role==='user'&&m.text===r.text);
   r.reply=asked<0?null:conv.messages.slice(asked+1).find(m=>m.role==='assistant')?.text||null;
   if(!r.reply)await sleep(500);
  }
  if(asked<0||!r.reply)throw Error('Home\'s box did not start a director turn: '+JSON.stringify(conv?.messages?.slice(-2)));
  await page.waitForFunction(t=>[...document.querySelectorAll('.msg.arena .bubble')].some(b=>b.textContent.includes(t.slice(0,40))),r.reply,{timeout:15000});
  await shot(page,'home-to-chat');
  result.checks.homeToChat=r;console.log('8',JSON.stringify(r));
 }

 // A. "Since you were away" (blocker 1). With this device last seen 20 hours ago the banner
 // counts each build once, in one group, as its blocks tell it: finished only when its work
 // (never the plan) finished after the absence began, stopped when a part stopped then, the
 // live build not at all. Its line wraps rather than being cut, at 390 and 360 px.
 if(wanted.includes('A')){
  const r={};
  await page.goto('about:blank');await sleep(1500);
  py(`db.execute("UPDATE sessions SET last_seen=?, away_since=NULL",(time.time()-20*3600,))`);
  const host=async path=>(await page.request.get(base+'/api/v1/'+path)).json();
  const session=await host('session');r.since=session.away_since;
  if(typeof r.since!=='number')throw Error('The session does not report the absence: '+JSON.stringify(session));
  // What the host's records say happened since then.
  const snap=await host('overview');
  const shelved=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));
  const needy=new Set(snap.tasks.filter(t=>['awaiting_input','needs_auth','needs_permission'].includes(t.state)&&(t.updated||0)>=r.since).map(t=>t.goal_id));
  for(const a of snap.approvals){const t=snap.tasks.find(x=>x.id===a.task_id);const proj=snap.projects.find(x=>x.id===t?.project_id);if(a.state==='pending'&&(a.created||0)>=r.since&&t&&proj?.integration_policy!=='auto_local_main')needy.add(t.goal_id)}
  const expect={finished:[],needs:[],stopped:[]};
  for(const g of snap.goals.filter(g=>!g.archived&&!shelved.has(g.project_id))){
   if(needy.has(g.id)){expect.needs.push(g.id);continue}
   const s=await host('goals/'+g.id+'/story');
   const work=snap.tasks.filter(t=>t.goal_id===g.id&&!isPlan(t));
   if(s.status==='completed'&&Math.max(0,...work.filter(t=>t.state==='completed').map(t=>t.updated||0))>=r.since)expect.finished.push(g.id);
   if(s.status==='blocked'&&Math.max(0,...s.blocks.filter(b=>b.state==='blocked').map(b=>b.updated||0))>=r.since)expect.stopped.push(g.id);
  }
  r.expected={finished:expect.finished.length,needs:expect.needs.length,stopped:expect.stopped.length};
  const line=[r.expected.finished&&`${r.expected.finished} finished`,r.expected.needs&&`${r.expected.needs} need${r.expected.needs===1?'s':''} you`,r.expected.stopped&&`${r.expected.stopped} stopped`].filter(Boolean).join(' · ');
  if(!line)throw Error('Nothing in the copy finished or stopped during the absence');
  await page.goto(base);await page.getByLabel('Tell Arena what to build').waitFor();
  const banner=page.getByRole('region',{name:'Since you were away'});await banner.waitFor({timeout:15000});
  r.line=await flat(banner.locator('.away-counts'));
  if(r.line!==line)throw Error(`The banner says "${r.line}"; the records say "${line}"`);
  // Every word of the line is on screen (it wraps; nothing is cut) at 390 and 360 px.
  for(const width of [390,360]){
   await page.setViewportSize({width,height:844});await sleep(300);
   const cut=await banner.locator('.away-toggle').evaluate(t=>[t,...t.querySelectorAll('*')].filter(e=>{const cs=getComputedStyle(e);return (cs.overflow!=='visible'||cs.textOverflow==='ellipsis')&&(e.scrollWidth>e.clientWidth+1||e.scrollHeight>e.clientHeight+1)}).map(e=>e.className.baseVal??e.className));
   if(cut.length)throw Error(`The banner line is cut at ${width} px: ${cut.join(', ')}`);
   if(await overflow(page))throw Error('Horizontal overflow with the banner at '+width+' px');
   r['toggle'+width]=await flat(banner.locator('.away-toggle'));
  }
  await page.setViewportSize({width:390,height:844});
  await banner.getByRole('button',{name:/Since you were away/}).click();
  const rows=banner.locator('.list-row');await rows.first().waitFor();
  r.rows=(await rows.locator('b').allInnerTexts()).map(t=>t.trim());
  r.labels=(await rows.locator('.tone').allInnerTexts()).map(t=>t.trim());
  const titleOf=id=>plainTitle(snap.goals.find(g=>g.id===id).display_title);
  if(r.rows.length!==Math.min(12,r.expected.finished+r.expected.needs+r.expected.stopped))throw Error('Banner rows: '+JSON.stringify(r.rows));
  if(new Set(r.rows).size!==r.rows.length)throw Error('A build is listed twice: '+JSON.stringify(r.rows));
  for(const id of [...expect.finished,...expect.stopped])if(!r.rows.includes(titleOf(id)))throw Error('Missing from the banner: '+titleOf(id));
  if(r.rows.includes(live.title))throw Error('The live build is listed as finished or stopped');
  if(r.labels.filter(l=>l==='Stopped').length!==r.expected.stopped||r.labels.filter(l=>/^(Finished|Merged into main)$/.test(l)).length!==r.expected.finished)throw Error('Banner labels: '+JSON.stringify(r.labels));
  await shot(page,'away-390');
  await page.setViewportSize({width:360,height:780});await sleep(300);await shot(page,'away-360');
  await page.setViewportSize({width:390,height:844});
  await banner.getByRole('button',{name:'Got it',exact:true}).click();
  result.checks.away=r;console.log('A',JSON.stringify(r));
 }

 // C. A real build whose work was all cancelled (blocker 2): its plan finished, its work did
 // not. Home, Builds and its story all say it was cancelled or stopped, never finished.
 if(wanted.includes('C')){
  const r={};
  const snap=await api(page,'overview');
  const shelved=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));
  const cancelled=snap.goals.filter(g=>!g.archived&&!shelved.has(g.project_id)).filter(g=>{const tasks=snap.tasks.filter(t=>t.goal_id===g.id);const plan=tasks.find(isPlan);const work=tasks.filter(t=>t!==plan);return plan?.state==='completed'&&work.length>0&&work.every(t=>t.state==='cancelled')});
  const target=cancelled.find(g=>g.id==='goal_7a5db32a3a62aeba')||cancelled[0];
  if(!target)throw Error('The copy has no build whose work was all cancelled');
  const title=plainTitle(target.display_title);r.goal=target.id;r.title=title;
  const story=await api(page,'goals/'+target.id+'/story');r.story={status:story.status,label:story.status_label};
  if(story.status!=='cancelled')throw Error('The host does not call it cancelled: '+JSON.stringify(r.story));
  await home(page);
  const finishedToggle=page.getByRole('button',{name:/^Finished/});
  if(await finishedToggle.getAttribute('aria-expanded')==='false')await finishedToggle.click();
  const row=page.locator('.finished-row').filter({hasText:title}).first();await row.waitFor({timeout:15000});
  r.home=await flat(row);r.glyph=await row.locator('.status-glyph').getAttribute('class');
  if(!/Cancelled (?:just now|\d+[mhd] ago)/.test(r.home)||/Finished|Merged/.test(r.home)||/\bdone\b/.test(r.glyph))throw Error('Home row of the cancelled build: '+JSON.stringify(r));
  await row.scrollIntoViewIfNeeded();await shot(page,'cancelled-home');
  await go(page,'Builds');await page.locator('.history .home-section').first().waitFor();
  r.builds=await page.evaluate(t=>[...document.querySelectorAll('.history .home-section')].filter(s=>[...s.querySelectorAll('.home-row b')].some(b=>b.textContent===t)).map(s=>s.getAttribute('aria-label')),title);
  if(r.builds.join()!=='Stopped')throw Error('Builds lists the cancelled build under '+JSON.stringify(r.builds));
  await page.locator('.finished-row').filter({hasText:title}).first().click();
  await page.waitForFunction(()=>document.querySelector('.screen.story .screen-head .chip'),null,{timeout:15000});
  r.head=await flat(page.locator('.screen-head'));
  if(!r.head.includes(story.status_label)||!/Cancelled/.test(r.head))throw Error('Story head of the cancelled build: '+r.head);
  await shot(page,'cancelled-story');
  result.checks.cancelled=r;console.log('C',JSON.stringify(r));
 }

 // M. A verified change that failed to merge into main on a project that merges by itself
 // (should-fix 3): nothing is happening, so it is stopped, never "Building · Merging into
 // main", and its story says why in plain words with the director one tap away.
 if(wanted.includes('M')){
  const r={};
  const snap=await api(page,'overview');
  const target=snap.projects.find(p=>!p.archived&&p.id!==live.project);
  if(!target)throw Error('The copy has no second project for a failed merge');
  const m={project:target.id,goal:'goal_'+hex(),task:'task_'+hex(),approval:'approval_'+hex(),title:'Show the device name in the session list'};
  r.project=target.name;
  r.policy=JSON.parse(py(`now=time.time()
proj=json.loads(db.execute("SELECT data FROM projects WHERE id=?",(p["project"],)).fetchone()[0])
policy=proj.get("integration_policy")
proj["integration_policy"]="auto_local_main"
db.execute("UPDATE projects SET data=? WHERE id=?",(json.dumps(proj),p["project"]))
aid0,tid0=db.execute("SELECT id,task_id FROM approvals WHERE state='accept' ORDER BY rowid DESC LIMIT 1").fetchone()
a=json.loads(db.execute("SELECT data FROM approvals WHERE id=?",(aid0,)).fetchone()[0])
t=json.loads(db.execute("SELECT data FROM tasks WHERE id=?",(tid0,)).fetchone()[0])
t.pop("integrated_commit",None)
t.update(title=p["title"],created=now-900,updated=now-30,reason="Verified; integrating into local main under the project's policy",project_id=p["project"])
db.execute("INSERT INTO goals(id,project_id,data) VALUES(?,?,?)",(p["goal"],p["project"],json.dumps({"title":p["title"],"request":p["title"]+".","created":now-900})))
db.execute("INSERT INTO tasks(id,project_id,goal_id,state,data) VALUES(?,?,?,?,?)",(p["task"],p["project"],p["goal"],"ready_for_approval",json.dumps(t)))
a.update(publication={"state":"failed","branch":"main","reason":"Merge into main failed: conflict in web/src/App.tsx"},created=now-60,covered_tasks=[p["task"]])
db.execute("INSERT INTO approvals(id,task_id,state,data) VALUES(?,?,?,?)",(p["approval"],p["task"],"accept",json.dumps(a)))
db.execute("INSERT INTO events(time,kind,data) VALUES(?,?,?)",(now,"integration.failed",json.dumps({"schema":1,"task_id":p["task"]})))
print(json.dumps(policy))`,m));
  try{
   await go(page,'Builds');await home(page);
   const finishedToggle=page.getByRole('button',{name:/^Finished/});
   if(await finishedToggle.getAttribute('aria-expanded')==='false')await finishedToggle.click();
   const row=page.locator('.finished-row').filter({hasText:m.title}).first();await row.waitFor({timeout:15000});
   r.active=(await page.locator('.active-row').allInnerTexts()).map(t=>t.split('\n')[0].trim());
   if(r.active.includes('Building '+target.name))throw Error('A failed merge still shows as building: '+r.active.join(' | '));
   r.home=await flat(row);
   if(!/Stopped (?:just now|\d+[mhd] ago)/.test(r.home))throw Error('Home row of the failed merge: '+r.home);
   await row.scrollIntoViewIfNeeded();await shot(page,'merge-failed-home');
   await row.click();
   const card=page.getByRole('region',{name:'What went wrong'});await card.waitFor({timeout:15000});
   const story=await api(page,'goals/'+m.goal+'/story');
   r.story={status:story.status,what:story.failure?.what_failed};r.card=await flat(card);
   if(story.status!=='blocked'||!story.failure?.what_failed||!r.card.includes(story.failure.what_failed))throw Error('The failed merge\'s story: '+JSON.stringify(r));
   if(!(await card.getByRole('button',{name:'Ask the director',exact:true}).count()))throw Error('No "Ask the director" on the failure');
   const words=machinery(await visibleText(page));if(words.length)throw Error('The failed merge shows '+words.join(', '));
   await shot(page,'merge-failed');
   await go(page,'Builds');await page.locator('.history .home-section').first().waitFor();
   r.builds=await page.evaluate(t=>[...document.querySelectorAll('.history .home-section')].filter(s=>[...s.querySelectorAll('.home-row b')].some(b=>b.textContent===t)).map(s=>s.getAttribute('aria-label')),m.title);
   if(r.builds.join()!=='Stopped')throw Error('Builds lists the failed merge under '+JSON.stringify(r.builds));
  }finally{
   if(r.policy!=='auto_local_main')py(`proj=json.loads(db.execute("SELECT data FROM projects WHERE id=?",(p["project"],)).fetchone()[0])
proj["integration_policy"]=p["policy"]
db.execute("UPDATE projects SET data=? WHERE id=?",(json.dumps(proj),p["project"]))`,{project:m.project,policy:r.policy});
  }
  result.checks.mergeFailed=r;console.log('M',JSON.stringify(r));
 }

 // S. An agent gone quiet (should-fix 5): the Claude block's transcript was last written 24
 // minutes ago, so the host calls it stalled. It is not shown as live (no dot, no wave, no
 // Live chip) or counted as working; it says when it last did something.
 if(wanted.includes('S')){
  const r={};const file=live.dirA+'/transcript.jsonl';
  freshen(); // the Codex block keeps working throughout
  const quietSince=Date.now()/1000-24*60-5;utimesSync(file,quietSince,quietSince);
  const quietLine=/Last activity 2[45] min ago: \S/;
  try{
   await go(page,'Builds');await home(page);
   await page.waitForFunction(n=>[...document.querySelectorAll('.active-row')].some(r=>r.textContent.includes('Building '+n)&&/quiet for 2\d min/.test(r.textContent)),live.name,{timeout:20000});
   const row=page.locator('.active-row').filter({hasText:'Building '+live.name}).first();
   r.home=await flat(row);r.dot=await row.locator('.live-dot').getAttribute('class');
   if(!/1 agent working · 1 quiet for 2[45] min/.test(r.home))throw Error('Home row with a quiet agent: '+r.home);
   await shot(page,'quiet-home');
   await row.click();
   const quiet=page.locator('.block-card.agent-claude').first();await quiet.waitFor({timeout:15000});
   await page.waitForFunction(()=>/Last activity 2\d min ago/.test(document.querySelector('.block-card.agent-claude')?.textContent||''),null,{timeout:15000});
   r.card=await flat(quiet);r.cardMarks={dots:await quiet.locator('.live-dot').count(),waves:await quiet.locator('.wave').count(),name:await quiet.getAttribute('aria-label')};
   const working=page.locator('.block-card.agent-codex').first();r.workingMarks={dots:await working.locator('.live-dot').count(),waves:await working.locator('.wave').count()};
   r.head=await flat(page.locator('.screen-head'));
   if(r.cardMarks.dots||r.cardMarks.waves||!quietLine.test(r.card))throw Error('The quiet block looks live: '+JSON.stringify(r));
   if(!/quiet for 2[45] min/.test(r.cardMarks.name))throw Error('The quiet block\'s accessible name does not say so: '+r.cardMarks.name);
   if(r.workingMarks.dots!==1||r.workingMarks.waves!==1)throw Error('The working block lost its live marks: '+JSON.stringify(r.workingMarks));
   if(!/1 agent working · 1 quiet for 2[45] min/.test(r.head))throw Error('Story head with a quiet agent: '+r.head);
   await shot(page,'quiet-story');
   await quiet.click();
   await page.getByRole('heading',{name:'What this block is doing'}).waitFor({timeout:15000});
   r.detail={live:await page.locator('.updates-card').getByText('Live',{exact:true}).count(),now:await flat(page.locator('.updates li.now'))};
   if(r.detail.live||!quietLine.test(r.detail.now))throw Error('Block detail of a quiet agent: '+JSON.stringify(r.detail));
   await shot(page,'quiet-block');
  }finally{const t=Date.now()/1000;utimesSync(file,t,t)}
  result.checks.quiet=r;console.log('S',JSON.stringify(r));
 }

 // T. Chat's default view is plain English (should-fix 7): build cards for the live build, a
 // real stopped build and a real merged one read their stories (what the agent is doing, why
 // it stopped, "Merged into main" with the commit only in the tooltip), and the director's
 // thinking step names what it reads, its command folded under Details. No hash, file name,
 // path, exit code or shell command anywhere else.
 if(wanted.includes('T')){
  const r={};
  const snap=await api(page,'overview');
  const shelved=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));
  const goals=[...snap.goals].sort((a,b)=>b.created-a.created).filter(g=>!g.archived&&!shelved.has(g.project_id));
  const blocked=goals.find(g=>snap.tasks.some(t=>t.goal_id===g.id&&t.state==='blocked'&&/exit=|timeout=|Traceback/i.test(t.reason||'')))||goals.find(g=>snap.tasks.some(t=>t.goal_id===g.id&&t.state==='blocked'));
  const merged=goals.find(g=>{const work=snap.tasks.filter(t=>t.goal_id===g.id&&!isPlan(t)&&t.state!=='cancelled');return work.length&&work.every(t=>t.state==='completed')&&work.some(t=>t.integrated_commit)});
  if(!blocked||!merged)throw Error('The copy lacks a stopped or a merged build');
  const sha=String(snap.tasks.filter(t=>t.goal_id===merged.id&&t.integrated_commit).sort((a,b)=>(b.updated||0)-(a.updated||0))[0].integrated_commit).slice(0,7);
  const chat='conv_'+hex(),thinking='conv_'+hex(),command='rg -n "overnight" src/agent_arena/control/engine.py';
  py(`now=time.time()
turn=max(p["spawned"]+2,now-40)
for cid,title,status,extra in [(p["chat"],"Three builds","idle",{}),(p["thinking"],"What is Claude doing?","thinking",{"step":"reading repository","step_detail":p["command"],"step_since":max(turn,now-20),"turn_started":turn,"turn_repo":"arena-smoke-20261028","commands":3})]:
    db.execute("INSERT INTO conversations(id,data) VALUES(?,?)",(cid,json.dumps({"title":title,"created":now-600,"updated":now-5,"project_id":p["project"],"spec":None,"status":status,**extra})))
msgs=[(p["chat"],"user","Build the overnight queue",{}),(p["chat"],"assistant","Starting the build.",{"build_started":True,"goal_id":p["live"],"project":p["name"]}),(p["chat"],"user","And the overnight mode",{}),(p["chat"],"assistant","Starting the build.",{"build_started":True,"goal_id":p["blocked"]}),(p["chat"],"user","And the timer",{}),(p["chat"],"assistant","Starting the build.",{"build_started":True,"goal_id":p["merged"]}),(p["thinking"],"user","What is Claude doing?",{})]
for i,(cid,role,text,data) in enumerate(msgs):
    db.execute("INSERT INTO messages(conversation_id,time,role,text,data) VALUES(?,?,?,?,?)",(cid,now-500+i,role,text,json.dumps(data)))`,{chat,thinking,command,spawned,project:live.project,name:live.name,live:ids.goal,blocked:blocked.id,merged:merged.id});
  await home(page);
  await page.evaluate(i=>localStorage.setItem('arena.conversation',i),chat);
  await go(page,'Chats');
  const cards=page.locator('.chat-build');await cards.nth(2).waitFor({timeout:15000});
  // The live card follows the newest live attempt's block; the stopped card, the story's failure.
  const liveTask=snap.attempts.filter(a=>[ids.a,ids.b,ids.c].includes(a.task_id)&&['leased','running','verifying'].includes(a.state)).sort((a,b)=>b.created-a.created)[0].task_id;
  const now=(await api(page,'goals/'+ids.goal+'/story')).blocks.find(b=>b.id===liveTask)?.now;
  const what=(await api(page,'goals/'+blocked.id+'/story')).failure?.what_failed;
  if(!now||!what)throw Error('The stories lack a plain "now" or failure: '+JSON.stringify({now,what}));
  await page.waitForFunction(([n,w])=>{const c=[...document.querySelectorAll('.chat-build')];return c.length>=3&&c[0].textContent.includes(n)&&c[1].textContent.includes(w)},[now,what],{timeout:20000});
  r.cards=(await cards.allInnerTexts()).map(t=>t.replace(/\s+/g,' ').trim());
  for(const c of r.cards){const found=[...machinery(c),...(/(?:^|\s)\$ |\bpytest\b|\brg -n\b/.test(c)?['a shell command']:[])];if(found.length)throw Error('A chat build card shows '+found.join(', ')+': '+c)}
  r.code=await page.locator('.chat-build code').count();if(r.code)throw Error('A chat build card shows code');
  const raw=snap.tasks.find(t=>t.goal_id===blocked.id&&t.state==='blocked')?.reason||'';
  if(raw&&r.cards[1].includes(raw.slice(0,24)))throw Error('The stopped card shows the raw reason: '+r.cards[1]);
  r.tooltip=await cards.nth(2).locator('.chip[title]').getAttribute('title');
  if(!r.cards[2].includes('Merged into main')||r.cards[2].includes(sha)||r.tooltip!==`Commit ${sha} on main`)throw Error('The merged card: '+JSON.stringify({card:r.cards[2],tooltip:r.tooltip}));
  if(await overflow(page))throw Error('Horizontal overflow on Chat with build cards');
  await cards.first().scrollIntoViewIfNeeded();await shot(page,'chat-plain',true);
  // The director's thinking step.
  await go(page,'Home');
  await page.evaluate(i=>localStorage.setItem('arena.conversation',i),thinking);
  await go(page,'Chats');
  const bubble=page.locator('.msg.thinking');
  try{await bubble.waitFor({timeout:15000})}catch{throw Error('No thinking bubble: '+JSON.stringify({chat:(await flat(page.locator('.chat-thread'))).slice(0,300),host:await api(page,'conversations/'+thinking).then(c=>({status:c.status,step:c.step,turn_started:c.turn_started,spawned,messages:c.messages?.length}))}))}
  r.thinking=await flat(bubble);
  if(!r.thinking.startsWith('Reading the code in '+live.name)||r.thinking.includes('rg -n')||machinery(r.thinking).length)throw Error('Thinking bubble: '+r.thinking);
  if(await bubble.locator('details.thinking-details').evaluate(d=>d.open))throw Error('The director\'s command shows by default');
  await shot(page,'chat-thinking');
  await bubble.locator('details.thinking-details > summary').click();
  r.details=await flat(bubble.locator('details.thinking-details'));
  if(!r.details.includes(command))throw Error('Details does not hold the command: '+r.details);
  result.checks.chat=r;console.log('T',JSON.stringify(r));
 }

 // N. One "needs you" list (should-fix 10): Home's Needs you, the ☰ attention dot and its
 // accessible name, Review's count, the sidebar count and the amber band always agree, and a
 // need of an archived build or project leaves every one of them.
 if(wanted.includes('N')){
  const r={};
  const snap=await api(page,'overview');
  const shelved=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));
  const task=snap.tasks.filter(t=>t.state==='blocked'&&t.project_id!==live.project&&!shelved.has(t.project_id)&&!snap.goals.find(g=>g.id===t.goal_id)?.archived).sort((a,b)=>(b.updated||0)-(a.updated||0))[0];
  if(!task)throw Error('The copy has no stopped part to turn into a sign-in request');
  r.task=task.id;r.project=snap.projects.find(p=>p.id===task.project_id).name;
  const touch=`db.execute("INSERT INTO events(time,kind,data) VALUES(?,?,?)",(time.time(),"task.changed",json.dumps({"schema":1})))`;
  const setState=state=>py(`db.execute("UPDATE tasks SET state=? WHERE id=?",(p["state"],p["id"]))\n`+touch,{id:task.id,state});
  const flag=(table,id,on)=>py(`row=json.loads(db.execute("SELECT data FROM ${table} WHERE id=?",(p["id"],)).fetchone()[0])
row["archived"]=p["on"]
db.execute("UPDATE ${table} SET data=? WHERE id=?",(json.dumps(row),p["id"]))
`+touch,{id,on});
  const label=n=>n?`Menu, ${n} item${n===1?'':'s'} need${n===1?'s':''} you`:'Menu';
  const settle=n=>page.waitForFunction(l=>document.querySelector('button.menu-btn')?.getAttribute('aria-label')===l,label(n),{timeout:20000});
  const counts=async()=>{
   await home(page);
   const menu=page.getByRole('button',{name:/^Menu\b/});
   const c={home:(await page.locator('.home-needs').count())?Number(await page.locator('.home-needs .count').first().innerText()):0,label:await menu.getAttribute('aria-label'),dot:await menu.evaluate(b=>b.classList.contains('attention'))};
   await go(page,'Builds');await page.locator('.history').waitFor();
   c.band=(await page.locator('.needs-band').count())?await page.locator('.needs-band').getAttribute('aria-label'):null;
   await go(page,'Review');await page.getByRole('heading',{name:'Review',exact:true}).waitFor();
   c.review=(await page.locator('main .panel.accent-amber').count())?Number(await page.locator('main .panel.accent-amber .count').first().innerText()):0;
   c.sidebar=Number(String(await page.evaluate(()=>document.querySelector('.sidebar .nav-count')?.textContent||'0')).replace(/\D/g,'')||0);
   return c;
  };
  const agree=(c,n)=>c.home===n&&c.review===n&&c.sidebar===n&&c.dot===(n>0)&&c.label===label(n)&&(n?!!c.band&&c.band.startsWith(`Needs you: ${n} item`):c.band===null);
  try{
   setState('needs_auth');await settle(1);
   r.waiting=await counts();if(!agree(r.waiting,1))throw Error('The needs counts disagree: '+JSON.stringify(r.waiting));
   await home(page);await shot(page,'needs-home');
   // The build is archived (by the host when it allows it, else flagged in the copy): its need leaves every count.
   const archived=await apiPost(page,'goals/archive',{ids:[task.goal_id]});
   r.archivedBy=(archived.body?.archived||[]).includes(task.goal_id)?'host':'copy';
   if(r.archivedBy==='copy')flag('goals',task.goal_id,true);
   await settle(0);r.buildArchived=await counts();if(!agree(r.buildArchived,0))throw Error('An archived build still needs you: '+JSON.stringify(r.buildArchived));
   if(r.archivedBy==='host')await apiPost(page,'goals/unarchive',{ids:[task.goal_id]});else flag('goals',task.goal_id,false);
   await settle(1);
   // Its project archived with the need still there (the host now refuses that; flagged in the copy).
   flag('projects',task.project_id,true);
   await settle(0);r.projectArchived=await counts();if(!agree(r.projectArchived,0))throw Error('An archived project still needs you: '+JSON.stringify(r.projectArchived));
   flag('projects',task.project_id,false);
   await settle(1);r.restored=await counts();if(!agree(r.restored,1))throw Error('The restored need is not counted everywhere: '+JSON.stringify(r.restored));
  }finally{setState(task.state)}
  await settle(0);
  result.checks.needs=r;console.log('N',JSON.stringify(r));
 }

 // No horizontal overflow at 360 and 390 px on any screen; the wave stands still when the
 // platform prefers reduced motion.
 if(wanted.includes('L')){
  const r={};
  freshen();
  for(const width of [390,360]){
   await page.setViewportSize({width,height:780});
   for(const name of ['Home','Chats',"What's running on my Mac",'Builds','Projects','Agents','Review','Archived']){
    await go(page,name);await sleep(500);
    if(await overflow(page))throw Error(`Horizontal overflow at ${width} px on ${name}`);
    const missing=await unnamed(page);if(missing.length)throw Error(`Controls without an accessible name on ${name}: `+missing.join(' | '));
   }
   await home(page);
   await page.getByRole('button',{name:/^Building /}).first().click();
   await page.locator('.timeline-blocks .block-card').nth(2).waitFor();
   if(await overflow(page))throw Error(`Horizontal overflow at ${width} px on the story`);
   {const missing=await unnamed(page);if(missing.length)throw Error('Controls without an accessible name on the story: '+missing.join(' | '));}
   if(width===360)await shot(page,'story-360');
   await page.locator('.block-card').first().click();
   await page.getByRole('heading',{name:'What this block is doing'}).waitFor();
   if(await overflow(page))throw Error(`Horizontal overflow at ${width} px on the block detail`);
   {const missing=await unnamed(page);if(missing.length)throw Error('Controls without an accessible name on the block detail: '+missing.join(' | '));}
   await page.getByRole('button',{name:/^Menu\b/}).click();
   if(await overflow(page))throw Error(`Horizontal overflow at ${width} px with the menu open`);
   {const missing=await unnamed(page);if(missing.length)throw Error('Controls without an accessible name in the menu: '+missing.join(' | '));}
   if(width===360)await shot(page,'menu-360');
   await page.keyboard.press('Escape');
   r[width]='no overflow';
  }
  // The phone menu and the desktop sidebar list the same items.
  await page.setViewportSize({width:390,height:844});
  await page.getByRole('button',{name:/^Menu\b/}).click();
  r.menu=await page.getByRole('dialog',{name:'Menu'}).getByRole('button').evaluateAll(b=>b.map(x=>x.getAttribute('aria-label')).filter(n=>n&&n!=='Close menu'));
  await page.keyboard.press('Escape');
  await page.setViewportSize({width:1440,height:1000});
  r.sidebar=await page.getByRole('navigation',{name:'Primary'}).getByRole('button').evaluateAll(b=>b.map(x=>x.getAttribute('aria-label')));
  if(r.menu.join('|')!==r.sidebar.join('|'))throw Error('Menu and sidebar differ: '+JSON.stringify({menu:r.menu,sidebar:r.sidebar}));
  if(await page.getByRole('button',{name:/^Menu\b/}).isVisible())throw Error('The menu button shows on a wide screen');
  await home(page);await page.getByRole('button',{name:/^Building /}).first().click();await page.locator('.timeline-blocks .block-card').nth(2).waitFor();
  await shot(page,'desktop-story');
  await page.setViewportSize({width:390,height:844});
  await page.emulateMedia({reducedMotion:'reduce'});
  await home(page);
  await page.getByRole('button',{name:/^Building /}).first().click();
  await page.locator('.block-card.running .wave').first().waitFor();
  r.reducedMotion=await page.locator('.block-card.running .wave').first().evaluate(el=>getComputedStyle(el).animationName);
  if(r.reducedMotion!=='none')throw Error('The wave moves under reduced motion: '+r.reducedMotion);
  await page.emulateMedia({reducedMotion:'no-preference'});
  r.motion=await page.locator('.block-card.running .wave').first().evaluate(el=>getComputedStyle(el).animationName);
  result.checks.layout=r;console.log('layout',JSON.stringify(r));
 }

 const after=await api(page,'overview');
 if(after.__status)throw Error('Overview unreadable at the end: '+after.__status);
 if(errors.length)throw Error(errors.join('\n'));
 result.passed=true;result.errors=errors;
 writeFileSync(output+'/result.json',JSON.stringify(result,null,2)+'\n');
 console.log('ROUND3_ACCEPTANCE_PASSED');
}catch(error){result.passed=false;result.failure=String(error);writeFileSync(output+'/result.json',JSON.stringify(result,null,2)+'\n');throw error}
finally{
 if(browser)await browser.close();
 const exited=new Promise(r=>{if(server.exitCode!==null)r();else{server.once('exit',r);setTimeout(r,5000)}});
 server.kill('SIGTERM');await exited;
 writeFileSync(output+'/server.txt',serverLog.replaceAll(state,'<copy>').replaceAll(scratch,'<files>'));
 rmSync(state,{recursive:true,force:true});rmSync(scratch,{recursive:true,force:true});
}

// What archiving must never touch: a build's tasks, attempts and their recorded outcomes.
function snapshotCounts(snap,goalId){
 const tasks=snap.tasks.filter(t=>t.goal_id===goalId);
 const ids=new Set(tasks.map(t=>t.id));
 return {tasks:tasks.map(t=>`${t.id}:${t.state}:${t.integrated_commit||''}`).sort(),attempts:snap.attempts.filter(a=>ids.has(a.task_id)).map(a=>`${a.id}:${a.state}`).sort()};
}

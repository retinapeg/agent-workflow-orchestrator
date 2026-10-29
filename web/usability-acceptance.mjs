// Usability acceptance on a COPY of the real control state (tests/control/real_state_server.py).
// Dispatch is disabled; the live database and the service on 8791 are never touched.
// Navigation is the owner's ☰ menu at every width (Round 4: no sidebar, no bottom navigation);
// Projects, Agents, Review and the technical build view are Advanced mode screens.
// ARENA_EVIDENCE redirects the output.
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,rmSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
const root=process.cwd(),state=mkdtempSync(tmpdir()+'/arena-real-'),port='8794',base='http://127.0.0.1:'+port;
const output=process.env.ARENA_EVIDENCE||root+'/docs/build-evidence/usability';mkdirSync(output,{recursive:true});
const ANDROID='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';
const PROOF=/^Orchestrator live proof/;
const ACTIVE_ATTEMPT=['leased','running','verifying'];
const NEEDS_TASK=['awaiting_input','needs_auth','needs_permission','ready_for_approval'];
const server=spawn(root+'/.venv/bin/python',['-m','uvicorn','tests.control.real_state_server:create_app','--factory','--host','127.0.0.1','--port',port],{cwd:root,env:{...process.env,ARENA_UI_STATE:state,ARENA_UI_PORT:port},stdio:'pipe'});
let serverLog='';server.stdout.on('data',d=>serverLog+=d);server.stderr.on('data',d=>serverLog+=d);
let browser;const result={state,viewport:390,userAgent:ANDROID,dispatch:false,checks:{}};
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
// The repository's Playwright (the Mac has its browsers); where only another Playwright
// build has browsers installed (a container), that one: ARENA_PLAYWRIGHT names its module.
async function launch(){
 try{return await (await import('@playwright/test')).chromium.launch()}
 catch(error){try{return await (await import(process.env.ARENA_PLAYWRIGHT||'/opt/npm-tools/node_modules/playwright/index.mjs')).chromium.launch()}catch{throw error}}
}
// Open a screen (Round 4: no sidebar at any width). The lion and wordmark go Home; everything
// else is in the ☰ menu, where Projects, Agents and Review are listed only in Advanced mode:
// asked for one of those, this turns Advanced mode on from the menu's own switch first.
const go=async(page,name)=>{if(name==='Home'){await page.getByRole('button',{name:'Arena home',exact:true}).click();return}await page.getByRole('button',{name:/^Menu\b/}).click();const sheet=page.getByRole('dialog',{name:'Menu'}),item=sheet.getByRole('button',{name,exact:true});await sheet.getByRole('switch',{name:'Advanced mode'}).waitFor();if(['Projects','Agents','Review'].includes(name)&&!(await item.count()))await sheet.getByRole('switch',{name:'Advanced mode'}).click();await item.click();};
// Advanced mode on or off from the ☰ menu's switch (remembered on this device).
const advancedMode=async(page,on)=>{await page.getByRole('button',{name:/^Menu\b/}).click();const sheet=page.getByRole('dialog',{name:'Menu'}),sw=sheet.getByRole('switch',{name:'Advanced mode'});if(await sw.getAttribute('aria-checked')!==String(on))await sw.click();await page.keyboard.press('Escape');await sheet.waitFor({state:'hidden'});};
// The technical build view (Advanced mode): Build history → Technical build view.
const technical=async page=>{await advancedMode(page,true);await go(page,'Build history');await page.getByRole('button',{name:'Technical build view',exact:true}).click();};
try{
 for(let n=0;n<80;n++){if(server.exitCode!==null)throw Error('Fixture server exited with '+server.exitCode+' (is port '+port+' free?)\n'+serverLog.slice(-800));try{if((await fetch(base)).ok)break}catch{}await new Promise(r=>setTimeout(r,250));}
 if(server.exitCode!==null)throw Error('Fixture server exited with '+server.exitCode+'\n'+serverLog.slice(-800));
 browser=await launch();
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:ANDROID,deviceScaleFactor:2});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);
 await page.getByLabel('One-time pairing code').fill('real-state-one-use-code');
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByLabel('Talk to Arena').waitFor();

 // 1. Slim overview on the real state.
 const {bytes,snap}=await page.evaluate(async()=>{const r=await fetch('/api/v1/overview');const b=await r.arrayBuffer();return{bytes:b.byteLength,snap:JSON.parse(new TextDecoder().decode(b))}});
 // Absolute phone-size limit on today's state, plus a per-record budget (the list-sized
 // shape: ≤ 2 KB per attempt, ≤ 1.6 KB per task, ≤ 0.6 KB per goal, 40 KB for the rest)
 // that keeps catching a fat record after the state has grown past the absolute figure.
 const budget=40_000+2_000*snap.attempts.length+1_600*snap.tasks.length+600*snap.goals.length;
 result.checks.overviewBytes=bytes;result.checks.overviewBudget=budget;result.checks.overviewRecords={attempts:snap.attempts.length,tasks:snap.tasks.length,goals:snap.goals.length};
 if(bytes>=200_000)throw Error('Overview is '+bytes+' bytes (limit 200,000)');
 if(bytes>budget)throw Error('Overview is '+bytes+' bytes for '+snap.attempts.length+' attempts / '+snap.tasks.length+' tasks (budget '+budget+')');

 // 2. The four proof projects are archived.
 const proofs=snap.projects.filter(p=>PROOF.test(p.name));
 result.checks.proofProjects=proofs.map(p=>({name:p.name,archived:!!p.archived}));
 if(proofs.length!==4||proofs.some(p=>!p.archived))throw Error('Proof projects not all archived: '+JSON.stringify(result.checks.proofProjects));
 const archivedIds=new Set(snap.projects.filter(p=>p.archived).map(p=>p.id));

 // 3. Home lists no archived project, and neither does Build history (every build not archived).
 await go(page,'Home');
 await page.getByLabel('Talk to Arena').waitFor();
 const homeText=await page.locator('main').innerText();
 if(PROOF.test(homeText)||/Orchestrator live proof/.test(homeText))throw Error('Archived proof project visible on Home');
 await go(page,'Build history');
 await page.getByRole('heading',{name:'Build history',exact:true}).waitFor();
 const historyText=await page.locator('main').innerText();
 if(/Orchestrator live proof/.test(historyText))throw Error('Archived proof project visible in Build history');
 // ...and neither does the request dialog's project picker (the form lives on Projects).
 await go(page,'Projects');
 await page.getByRole('button',{name:'New request',exact:true}).first().click();
 await page.getByRole('dialog').waitFor();
 const requestOptions=await page.locator('#project option').allInnerTexts();
 result.checks.requestPicker=requestOptions;
 if(requestOptions.some(o=>PROOF.test(o.trim())))throw Error('Archived project in request picker');
 await page.screenshot({path:output+'/request-dialog-390.png'});
 await page.getByRole('button',{name:'Close dialog'}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});

 // ...nor the chat project picker.
 await go(page,'Chats');
 await page.locator('.chat-project').click();
 const chatPicker=page.getByRole('dialog',{name:'Choose a project'});await chatPicker.waitFor();
 const chatOptions=await chatPicker.locator('.list-row').allInnerTexts();
 result.checks.chatPicker=chatOptions.map(s=>s.split('\n')[0]);
 if(chatOptions.some(o=>/Orchestrator live proof/.test(o)))throw Error('Archived project in chat picker');
 await chatPicker.getByRole('button',{name:'Close dialog'}).click();

 // 4. The technical build view opens on the live / needs-you / newest request.
 const goalTasks=g=>snap.tasks.filter(t=>t.goal_id===g.id);
 const goals=[...snap.goals].sort((a,b)=>b.created-a.created);
 const isLive=g=>{const ids=new Set(goalTasks(g).map(t=>t.id));return snap.attempts.some(a=>ids.has(a.task_id)&&ACTIVE_ATTEMPT.includes(a.state))};
 const open=g=>!archivedIds.has(g.project_id);
 const has=(g,states)=>goalTasks(g).some(t=>states.includes(t.state));
 // The binding rule: the live request, else one that needs the owner, else the newest.
 const expected=goals.find(isLive)||goals.find(g=>open(g)&&has(g,NEEDS_TASK))||goals.find(open)||goals[0];
 const why=goals.find(isLive)?'live':goals.find(g=>open(g)&&has(g,NEEDS_TASK))?'needs-you':'newest';
 await technical(page);
 await page.locator('#goal-pick').waitFor();
 const selected=await page.locator('#goal-pick').inputValue();
 result.checks.buildsDefault={expected:expected.id,selected,rule:why,title:expected.display_title};
 if(selected!==expected.id)throw Error(`The technical build view opened on ${selected}, expected ${why} request ${expected.id}`);
 const buildOptions=await page.locator('#goal-pick option').allInnerTexts();
 if(buildOptions.some(o=>/\(archived\)/.test(o)&&!o.includes(expected.display_title||'\u0000')))throw Error('Archived requests listed without Show archived');
 if(buildOptions.some(o=>/OUTCOME:/i.test(o)))throw Error('Picker shows raw spec text');
 await page.screenshot({path:output+'/builds-default-390.png',fullPage:true});

 // 5. Live output is non-empty for an integrated build (newest one on a visible project).
 const integrated=goals.find(g=>open(g)&&goalTasks(g).some(t=>t.integrated_commit));
 if(!integrated)throw Error('No integrated build on a non-archived project in the real state');
 await page.locator('#goal-pick').selectOption(integrated.id);
 const terminal=page.locator('pre.terminal');
 await page.waitForFunction(()=>{const t=document.querySelector('pre.terminal');return t&&/\[\d\d:\d\d(:\d\d)?\]/.test(t.textContent||'')},null,{timeout:15000});
 const liveText=(await terminal.innerText()).trim();
 result.checks.liveOutput={goal:integrated.id,title:integrated.display_title,lines:liveText.split('\n').length,first:liveText.split('\n')[0]};
 if(!liveText||/No events recorded|older than the current window|Nothing has happened/.test(liveText))throw Error('Live output empty for integrated build: '+liveText.slice(0,200));
 await terminal.scrollIntoViewIfNeeded();
 await page.screenshot({path:output+'/builds-integrated-live-output-390.png'});

 // 6. No horizontal overflow on any screen at 390 px (file names keep the earlier tab names).
 result.checks.overflow={};
 for(const [tab,file] of [['Chats','chat'],['Home','overview'],['Projects','projects'],['Build history','build'],['Agents','agents'],['Review','review'],["What's running on my Mac",'mac'],['Archived','archived']]){
  await go(page,tab);
  await page.waitForTimeout(400);
  const over=await overflow(page);result.checks.overflow[tab]=over;
  await page.screenshot({path:output+'/'+file+'-390.png',fullPage:true});
  if(over)throw Error('Horizontal overflow on '+tab);
 }
 await technical(page);await page.locator('#goal-pick').waitFor();
 result.checks.overflow['Technical view']=await overflow(page);
 if(result.checks.overflow['Technical view'])throw Error('Horizontal overflow on the technical view');
 // Archived section on Projects, expanded, also fits.
 await go(page,'Projects');
 await page.locator('summary',{hasText:'Archived'}).click();
 await page.waitForTimeout(300);
 if(await overflow(page))throw Error('Horizontal overflow on Projects → Archived');
 await page.screenshot({path:output+'/projects-archived-390.png',fullPage:true});

 const after=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json());
 if(after.attempts.length!==snap.attempts.length)throw Error('Fixture dispatched an attempt');
 if(errors.length)throw Error(errors.join('\n'));
 result.passed=true;result.errors=errors;
 writeFileSync(output+'/result.json',JSON.stringify(result,null,2));
 console.log(JSON.stringify(result.checks,null,1));
 console.log('USABILITY_ACCEPTANCE_PASSED');
}catch(error){result.passed=false;result.failure=String(error);writeFileSync(output+'/result.json',JSON.stringify(result,null,2));writeFileSync(output+'/failure.txt',String(error));throw error}
finally{
 if(browser)await browser.close();
 // Stop the fixture and remove the copy of the state it served (a full copy of the control database).
 const exited=new Promise(r=>{if(server.exitCode!==null)r();else{server.once('exit',r);setTimeout(r,5000)}});
 server.kill('SIGTERM');await exited;
 writeFileSync(output+'/server.txt',serverLog);
 rmSync(state,{recursive:true,force:true});
}

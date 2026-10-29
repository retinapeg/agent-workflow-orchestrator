import {execFileSync} from 'node:child_process';
import {mkdirSync,writeFileSync, existsSync} from 'node:fs';
// The repository's Playwright on the Mac; where only another Playwright build has browsers
// (a container), that one: ARENA_PLAYWRIGHT names its module.
const PLAYWRIGHT=process.env.ARENA_PLAYWRIGHT||'/opt/npm-tools/node_modules/playwright/index.mjs';
const {chromium}=await import(existsSync(PLAYWRIGHT)?PLAYWRIGHT:'@playwright/test');
// Earlier tab names → Round 4 menu items (the technical build view is an Advanced mode screen).
const NAME={Overview:'Home',Chat:'Chats',Build:'Build history'};
// Open a screen (Round 4: no sidebar at any width). The lion and wordmark go Home; everything
// else is in the ☰ menu, where Projects, Agents and Review are listed only in Advanced mode:
// asked for one of those, this turns Advanced mode on from the menu's own switch first.
const go=async(page,name)=>{if(name==='Home'){await page.getByRole('button',{name:'Arena home',exact:true}).click();return}await page.getByRole('button',{name:/^Menu\b/}).click();const sheet=page.getByRole('dialog',{name:'Menu'}),item=sheet.getByRole('button',{name,exact:true});await sheet.getByRole('switch',{name:'Advanced mode'}).waitFor();if(['Projects','Agents','Review'].includes(name)&&!(await item.count()))await sheet.getByRole('switch',{name:'Advanced mode'}).click();await item.click();};
if(process.env.ARENA_LIVE!=='1')throw Error('Set ARENA_LIVE=1 to explicitly run the disposable subscription smoke task');
const root=process.cwd().replace(/\/web$/,'');
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','pair'],{cwd:root,encoding:'utf8'}).trim();
const browser=await chromium.launch();
const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,deviceScaleFactor:1});
const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
await page.goto('http://127.0.0.1:8790');
await page.getByLabel('One-time pairing code').fill(code);await page.getByRole('button',{name:'Pair device',exact:true}).click();
await page.getByLabel('Talk to Arena').waitFor();
await page.getByRole('button',{name:'New goal',exact:true}).click();
await page.getByLabel('Outcome',{exact:true}).fill('Make greeting output friendly and predictable');
await page.getByLabel('Instructions and completion criteria').fill('Implement greet(name) in src/greeting.py. Trim whitespace; return Hello, NAME! for a nonempty name and Hello, friend! for an empty/whitespace name. Do not change acceptance checks. This is a harmless disposable fixture.');
await page.getByRole('button',{name:'Run scoped goal',exact:true}).click();
await page.getByRole('dialog').waitFor({state:'hidden'});
console.log('REAL_GOAL_SUBMITTED_FROM_PHONE_UI');
await page.screenshot({path:root+'/docs/build-evidence/phone-running.png',fullPage:true});
const session=await context.storageState();await page.close();
const reopened=await context.newPage();await reopened.goto('http://127.0.0.1:8790');
await reopened.getByLabel('Talk to Arena').waitFor();
const started=Date.now();let snapshot,task;
while(Date.now()-started<600000){snapshot=await reopened.evaluate(async()=>await(await fetch('/api/v1/overview')).json());task=snapshot.tasks.at(-1);console.log('STATE',task?.state,task?.reason);if(task&&['ready_for_approval','completed','blocked','needs_auth','needs_permission'].includes(task.state))break;await new Promise(r=>setTimeout(r,10000));}
await reopened.screenshot({path:root+'/docs/build-evidence/phone-overview.png',fullPage:true});
for(const tab of ['Projects','Agents','Review']){await go(reopened,NAME[tab]||tab);if(await reopened.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Horizontal overflow: '+tab);await reopened.screenshot({path:root+'/docs/build-evidence/phone-'+tab.toLowerCase()+'.png',fullPage:true});}
await reopened.setViewportSize({width:1440,height:1000});await go(reopened,'Home');await reopened.screenshot({path:root+'/docs/build-evidence/desktop-overview.png',fullPage:true});
writeFileSync(root+'/docs/build-evidence/live-smoke.json',JSON.stringify({task,attempts:snapshot.attempts.filter(a=>a.task_id===task?.id),errors,seconds:(Date.now()-started)/1000,reopen_preserved:true,viewport_tested:[390,1440]},null,2));
await browser.close();if(errors.length||task?.state!=='ready_for_approval')throw Error('Live slice did not reach approval: '+JSON.stringify({state:task?.state,errors}));
console.log('LIVE_SLICE_PASSED');

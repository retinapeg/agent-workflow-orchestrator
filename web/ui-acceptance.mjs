// Browser/API acceptance only. Dispatch is disabled and no agent is simulated.
// Navigation is the owner's ☰ menu at every width (Round 4: no sidebar); Projects, Agents and
// Review are Advanced mode screens. ARENA_EVIDENCE redirects the output.
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
const root=process.cwd(),state=mkdtempSync(tmpdir()+'/arena-ui-');
const output=process.env.ARENA_EVIDENCE||root+'/docs/build-evidence/ui-acceptance';mkdirSync(output,{recursive:true});
const server=spawn(root+'/.venv/bin/python',['-m','uvicorn','tests.control.ui_server:create_app','--factory','--host','127.0.0.1','--port','8793'],{cwd:root,env:{...process.env,ARENA_UI_STATE:state,ARENA_UI_PORT:'8793'},stdio:'pipe'});
let serverLog='';server.stdout.on('data',d=>serverLog+=d);server.stderr.on('data',d=>serverLog+=d);
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
const home=page=>page.getByLabel('Talk to Arena').waitFor();
let browser;
try{
 for(let n=0;n<40;n++){try{if((await fetch('http://127.0.0.1:8793')).ok)break}catch{}await new Promise(r=>setTimeout(r,250));}
 browser=await launch();
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
 let page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8793');
 await page.getByLabel('One-time pairing code').fill('ui-fixture-one-use-code');
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await home(page);
 // The direct request form lives on Projects; Home talks to the director.
 await go(page,'Projects');
 await page.getByRole('button',{name:'New request',exact:true}).click();
 await page.getByLabel('What would you like to change?').fill('Queued UI test only; no worker is enrolled.');
 await page.getByRole('button',{name:'Build this for me',exact:true}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 const before=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json());
 if(before.tasks.length!==1||!before.paused)throw Error('Submission not persisted in paused fixture');
 await page.close();page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:8793');
 await home(page);
 // Screenshot names keep the earlier tab names so the evidence lines up across rounds.
 for(const [tab,file] of [['Home','overview'],['Projects','projects'],['Agents','agents'],['Review','review']]){
  await go(page,tab);
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Overflow on '+tab);
  await page.screenshot({path:output+'/'+file+'-390.png',fullPage:true});
 }
 await go(page,'Projects');
 await page.getByLabel('Browser API fixture priority').selectOption('2');
 await page.getByRole('status').filter({hasText:'Priority updated'}).waitFor();
 const after=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json());
 if(after.projects[0].priority!==2)throw Error('Priority control did not persist');
 if(after.tasks[0].id!==before.tasks[0].id||after.attempts.length)throw Error('Reload duplicated or dispatched task');
 await page.setViewportSize({width:1440,height:1000});
 await page.screenshot({path:output+'/desktop-1440.png',fullPage:true});
 await go(page,'Device session');
 await page.getByRole('button',{name:'Unpair this device'}).click();
 await page.getByLabel('One-time pairing code').waitFor();
 if((await page.request.get('http://127.0.0.1:8793/api/v1/overview')).status()!==401)throw Error('Logout did not revoke session');
 if(errors.length)throw Error(errors.join('\n'));
 writeFileSync(output+'/result.json',JSON.stringify({passed:true,dispatch:false,realAgents:false,viewports:[390,1440],persistedTask:after.tasks[0].id,errors},null,2));
 console.log('BROWSER_API_ACCEPTANCE_PASSED');
}catch(error){writeFileSync(output+'/failure.txt',String(error));throw error}
finally{if(browser)await browser.close();server.kill('SIGTERM');writeFileSync(output+'/server.txt',serverLog)}

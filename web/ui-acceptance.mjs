// Browser/API acceptance only. Dispatch is disabled and no agent is simulated.
import {chromium} from '@playwright/test';
import {spawn} from 'node:child_process';
import {mkdtempSync,mkdirSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
const root=process.cwd(),state=mkdtempSync(tmpdir()+'/arena-ui-');
const output=root+'/docs/build-evidence/ui-acceptance';mkdirSync(output,{recursive:true});
const server=spawn(root+'/.venv/bin/python',['-m','uvicorn','tests.control.ui_server:create_app','--factory','--host','127.0.0.1','--port','8793'],{cwd:root,env:{...process.env,ARENA_UI_STATE:state,ARENA_UI_PORT:'8793'},stdio:'pipe'});
let serverLog='';server.stdout.on('data',d=>serverLog+=d);server.stderr.on('data',d=>serverLog+=d);
let browser;
try{
 for(let n=0;n<40;n++){try{if((await fetch('http://127.0.0.1:8793')).ok)break}catch{}await new Promise(r=>setTimeout(r,250));}
 browser=await chromium.launch();
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
 let page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto('http://127.0.0.1:8793');
 await page.getByLabel('One-time pairing code').fill('ui-fixture-one-use-code');
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByRole('navigation',{name:'Primary'}).waitFor();await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:'Overview',exact:true}).click().catch(()=>{});await page.getByRole('heading',{name:'Build control.',exact:true}).waitFor();
 await page.getByRole('button',{name:'New request',exact:true}).click();
 await page.getByLabel('What would you like to change?').fill('Queued UI test only; no worker is enrolled.');
 await page.getByRole('button',{name:'Build this for me',exact:true}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 const before=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json());
 if(before.tasks.length!==1||!before.paused)throw Error('Submission not persisted in paused fixture');
 await page.close();page=await context.newPage();page.on('pageerror',e=>errors.push(e.message));await page.goto('http://127.0.0.1:8793');
 await page.getByRole('navigation',{name:'Primary'}).waitFor();await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:'Overview',exact:true}).click().catch(()=>{});await page.getByRole('heading',{name:'Build control.',exact:true}).waitFor();
 for(const tab of ['Overview','Projects','Agents','Review']){
  await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:tab,exact:true}).click();
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Overflow on '+tab);
  await page.screenshot({path:output+'/'+tab.toLowerCase()+'-390.png',fullPage:true});
 }
 await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:'Projects',exact:true}).click();
 await page.getByLabel('Browser API fixture priority').selectOption('2');
 await page.getByRole('status').filter({hasText:'Priority updated'}).waitFor();
 const after=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json());
 if(after.projects[0].priority!==2)throw Error('Priority control did not persist');
 if(after.tasks[0].id!==before.tasks[0].id||after.attempts.length)throw Error('Reload duplicated or dispatched task');
 await page.setViewportSize({width:1440,height:1000});
 await page.screenshot({path:output+'/desktop-1440.png',fullPage:true});
 await page.getByRole('button',{name:'Device session'}).click();
 await page.getByRole('button',{name:'Unpair this device'}).click();
 await page.getByLabel('One-time pairing code').waitFor();
 if((await page.request.get('http://127.0.0.1:8793/api/v1/overview')).status()!==401)throw Error('Logout did not revoke session');
 if(errors.length)throw Error(errors.join('\n'));
 writeFileSync(output+'/result.json',JSON.stringify({passed:true,dispatch:false,realAgents:false,viewports:[390,1440],persistedTask:after.tasks[0].id,errors},null,2));
 console.log('BROWSER_API_ACCEPTANCE_PASSED');
}catch(error){writeFileSync(output+'/failure.txt',String(error));throw error}
finally{if(browser)await browser.close();server.kill('SIGTERM');writeFileSync(output+'/server.txt',serverLog)}

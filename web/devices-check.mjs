import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
// The repository's Playwright on the Mac; where only another Playwright build has browsers
// (a container), that one: ARENA_PLAYWRIGHT names its module.
const PLAYWRIGHT=process.env.ARENA_PLAYWRIGHT||'/opt/npm-tools/node_modules/playwright/index.mjs';
const {chromium}=await import(existsSync(PLAYWRIGHT)?PLAYWRIGHT:'@playwright/test');
// Open a screen (Round 4: no sidebar at any width). The lion and wordmark go Home; everything
// else is in the ☰ menu, where Projects, Agents and Review are listed only in Advanced mode:
// asked for one of those, this turns Advanced mode on from the menu's own switch first.
const go=async(page,name)=>{if(name==='Home'){await page.getByRole('button',{name:'Arena home',exact:true}).click();return}await page.getByRole('button',{name:/^Menu\b/}).click();const sheet=page.getByRole('dialog',{name:'Menu'}),item=sheet.getByRole('button',{name,exact:true});await sheet.getByRole('switch',{name:'Advanced mode'}).waitFor();if(['Projects','Agents','Review'].includes(name)&&!(await item.count()))await sheet.getByRole('switch',{name:'Advanced mode'}).click();await item.click();};
const root=process.cwd(), url='https://leonards-macbook-air-1.tail11ad03.ts.net';
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch();
try{
 const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
 const page=await ctx.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message)); page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 await page.goto(url);
 await page.getByLabel('One-time pairing code').fill(code);
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByLabel('Talk to Arena').waitFor();
 const api=await page.evaluate(async()=>await(await fetch('/api/v1/devices')).json());
 console.log('API devices:',api.length, JSON.stringify(api.map(d=>({id:d.id,current:d.current,label:(d.label||'').slice(0,30)}))).slice(0,400));
 await go(page,'Device session');
 await page.getByText('Paired devices').waitFor();
 await page.waitForTimeout(1500);
 const rows=await page.locator('dialog .list-row').count();
 console.log('rendered rows:',rows,'errors:',errors);
 await page.locator('dialog').screenshot({path:'docs/build-evidence/session-persistence/05-devices-dialog.png'});
 await page.getByRole('button',{name:'Unpair this device'}).click();
}finally{await browser.close()}

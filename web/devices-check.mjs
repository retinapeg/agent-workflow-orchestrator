import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
// The repository's Playwright on the Mac; where only another Playwright build has browsers
// (a container), that one: ARENA_PLAYWRIGHT names its module.
const PLAYWRIGHT=process.env.ARENA_PLAYWRIGHT||'/opt/npm-tools/node_modules/playwright/index.mjs';
const {chromium}=await import(existsSync(PLAYWRIGHT)?PLAYWRIGHT:'@playwright/test');
// Open a screen from the menu: the ☰ sheet on a phone, the sidebar on a wide screen.
const go=async(page,name)=>{const menu=page.getByRole('button',{name:/^Menu\b/}),side=page.getByRole('navigation',{name:'Primary'});await menu.or(side).first().waitFor();if(await menu.isVisible()){await menu.click();await page.getByRole('dialog',{name:'Menu'}).getByRole('button',{name,exact:true}).click();}else await side.getByRole('button',{name,exact:true}).click();};
const root=process.cwd(), url='https://leonards-macbook-air-1.tail11ad03.ts.net';
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch();
try{
 const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
 const page=await ctx.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message)); page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 await page.goto(url);
 await page.getByLabel('One-time pairing code').fill(code);
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByLabel('Tell Arena what to build').waitFor();
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

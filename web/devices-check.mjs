import {chromium} from '@playwright/test';
import {execFileSync} from 'node:child_process';
const root=process.cwd(), url='https://leonards-macbook-air-1.tail11ad03.ts.net';
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch();
try{
 const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
 const page=await ctx.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message)); page.on('console',m=>{if(m.type()==='error')errors.push(m.text())});
 await page.goto(url);
 await page.getByLabel('One-time pairing code').fill(code);
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByRole('navigation',{name:'Primary'}).waitFor();await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:'Overview',exact:true}).click().catch(()=>{});await page.getByRole('heading',{name:'Build control.',exact:true}).waitFor();
 const api=await page.evaluate(async()=>await(await fetch('/api/v1/devices')).json());
 console.log('API devices:',api.length, JSON.stringify(api.map(d=>({id:d.id,current:d.current,label:(d.label||'').slice(0,30)}))).slice(0,400));
 await page.getByRole('button',{name:'Device session'}).click();
 await page.getByText('Paired devices').waitFor();
 await page.waitForTimeout(1500);
 const rows=await page.locator('dialog .list-row').count();
 console.log('rendered rows:',rows,'errors:',errors);
 await page.locator('dialog').screenshot({path:'docs/build-evidence/session-persistence/05-devices-dialog.png'});
 await page.getByRole('button',{name:'Unpair this device'}).click();
}finally{await browser.close()}

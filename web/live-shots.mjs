import {chromium} from '@playwright/test';
import {execFileSync} from 'node:child_process';
const root=process.cwd(), url='http://127.0.0.1:8791', out='docs/build-evidence/ui-live';
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch();
try{
 const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const page=await ctx.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 await page.getByLabel('One-time pairing code').fill(code);
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByRole('navigation',{name:'Primary'}).waitFor();await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:'Overview',exact:true}).click().catch(()=>{});await page.getByRole('heading',{name:'Build control.',exact:true}).waitFor();
 for(const tab of ['Overview','Build','Agents','Review','Projects']){
  await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:tab,exact:true}).click();
  await page.waitForTimeout(800);
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('overflow '+tab);
  await page.screenshot({path:`${out}/${tab.toLowerCase()}-390.png`,fullPage:true});
 }
 await page.setViewportSize({width:1440,height:1000});
 await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:'Build',exact:true}).click();
 await page.waitForTimeout(800);
 await page.screenshot({path:`${out}/build-1440.png`,fullPage:true});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('LIVE_UI_OK');
}finally{await browser.close()}

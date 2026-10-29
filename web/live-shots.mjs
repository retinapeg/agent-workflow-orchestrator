import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
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
const root=process.cwd(), url='http://127.0.0.1:8791', out='docs/build-evidence/ui-live';
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch();
try{
 const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const page=await ctx.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 await page.getByLabel('One-time pairing code').fill(code);
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByLabel('Talk to Arena').waitFor();
 for(const tab of ['Overview','Build','Agents','Review','Projects']){
  await go(page,NAME[tab]||tab);
  await page.waitForTimeout(800);
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('overflow '+tab);
  await page.screenshot({path:`${out}/${tab.toLowerCase()}-390.png`,fullPage:true});
 }
 await page.setViewportSize({width:1440,height:1000});
 await go(page,'Build history');
 await page.waitForTimeout(800);
 await page.screenshot({path:`${out}/build-1440.png`,fullPage:true});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('LIVE_UI_OK');
}finally{await browser.close()}

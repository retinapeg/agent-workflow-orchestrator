import {execFileSync} from 'node:child_process';
import {existsSync} from 'node:fs';
// The repository's Playwright on the Mac; where only another Playwright build has browsers
// (a container), that one: ARENA_PLAYWRIGHT names its module.
const PLAYWRIGHT=process.env.ARENA_PLAYWRIGHT||'/opt/npm-tools/node_modules/playwright/index.mjs';
const {chromium}=await import(existsSync(PLAYWRIGHT)?PLAYWRIGHT:'@playwright/test');
// Earlier tab names → Round 3 menu items.
const NAME={Overview:'Home',Chat:'Chats',Build:'Builds'};
// Open a screen from the menu: the ☰ sheet on a phone, the sidebar on a wide screen.
const go=async(page,name)=>{const menu=page.getByRole('button',{name:/^Menu\b/}),side=page.getByRole('navigation',{name:'Primary'});await menu.or(side).first().waitFor();if(await menu.isVisible()){await menu.click();await page.getByRole('dialog',{name:'Menu'}).getByRole('button',{name,exact:true}).click();}else await side.getByRole('button',{name,exact:true}).click();};
const root=process.cwd(), url='http://127.0.0.1:8791', out='docs/build-evidence/ui-live';
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch();
try{
 const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const page=await ctx.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 await page.getByLabel('One-time pairing code').fill(code);
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByLabel('Tell Arena what to build').waitFor();
 for(const tab of ['Overview','Build','Agents','Review','Projects']){
  await go(page,NAME[tab]||tab);
  await page.waitForTimeout(800);
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('overflow '+tab);
  await page.screenshot({path:`${out}/${tab.toLowerCase()}-390.png`,fullPage:true});
 }
 await page.setViewportSize({width:1440,height:1000});
 await go(page,'Builds');
 await page.waitForTimeout(800);
 await page.screenshot({path:`${out}/build-1440.png`,fullPage:true});
 if(errors.length)throw Error(errors.join('\n'));
 console.log('LIVE_UI_OK');
}finally{await browser.close()}

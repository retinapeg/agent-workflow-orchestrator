// Verify the LIVE HTTPS app shows the redesign: real origin, real data, real browser.
import {execFileSync} from 'node:child_process';
import {mkdirSync, writeFileSync, existsSync} from 'node:fs';
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
const root=process.cwd(), url=process.env.ARENA_URL||'https://leonards-macbook-air-1.tail11ad03.ts.net', out='docs/build-evidence/ui-live-nxt';
mkdirSync(out,{recursive:true});
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch(); const report={url, checks:{}};
try{
 const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const page=await ctx.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url);
 await page.screenshot({path:`${out}/pair-390.png`,fullPage:false});
 await page.getByLabel('One-time pairing code').fill(code);
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByLabel('Talk to Arena').waitFor();
 await page.waitForTimeout(1500);
 const bundle=await page.evaluate(()=>[...document.scripts].map(s=>s.src).find(s=>s.includes('/assets/index-'))||'');
 report.checks.bundle=bundle.split('/').pop();
 report.checks.hero=await page.getByLabel('Talk to Arena').count(); // Home's conversational box
 report.checks.mic=await page.getByRole('button',{name:/dictate|speak/i}).count();
 report.checks.segmented=await page.getByRole('button',{name:'Improve',exact:true}).count();
 const snap=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json());
 const blocked=snap.tasks.filter(t=>['blocked','cancelled','interrupted'].includes(t.state)).length;
 report.checks.blocked_tasks_in_data=blocked;
 const needsText=await page.locator('text=/Needs you/').first().locator('xpath=ancestor::*[self::section or self::article or self::div][1]').innerText().catch(()=>'');
 report.checks.needs_you_panel_excerpt=needsText.slice(0,200);
 report.checks.blocked_visible_on_home=await page.locator('main').getByText(/Frozen acceptance checks did not all pass|non-regular untracked path/).count();
 await page.screenshot({path:`${out}/overview-390.png`,fullPage:true});
 for(const tab of ['Build','Review','Projects','Agents']){
  await go(page,NAME[tab]||tab);
  await page.waitForTimeout(800);
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('overflow '+tab);
  await page.screenshot({path:`${out}/${tab.toLowerCase()}-390.png`,fullPage:true});
 }
 await page.setViewportSize({width:1440,height:1000});
 await go(page,'Home');
 await page.waitForTimeout(800);
 await page.screenshot({path:`${out}/overview-1440.png`,fullPage:false});
 await go(page,'Build history');
 await page.waitForTimeout(800);
 await page.screenshot({path:`${out}/build-1440.png`,fullPage:false});
 // controls still work: open and close the New request dialog (on Projects), open Device session
 await go(page,'Projects');
 await page.getByRole('button',{name:'New request',exact:true}).click();
 await page.getByLabel('What would you like to change?').waitFor();
 await page.keyboard.press('Escape');
 await page.getByRole('dialog').waitFor({state:'hidden'});
 await go(page,'Device session');
 await page.getByRole('button',{name:'Unpair this device'}).waitFor();
 await page.screenshot({path:`${out}/device-session-1440.png`,fullPage:false});
 await page.getByRole('button',{name:'Unpair this device'}).click();
 report.checks.page_errors=errors; report.passed=report.checks.hero>0&&report.checks.blocked_visible_on_home===0&&errors.length===0;
 writeFileSync(`${out}/result.json`,JSON.stringify(report,null,2));
 console.log(JSON.stringify(report.checks)); console.log(report.passed?'LIVE_NXT_OK':'LIVE_NXT_MISMATCH');
}finally{await browser.close()}

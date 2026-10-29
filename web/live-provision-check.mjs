// Live HTTPS verification: proposal step (creates nothing), Projects GitHub link, Build tree, phone + desktop.
import {execFileSync} from 'node:child_process';
import {mkdirSync, writeFileSync, existsSync} from 'node:fs';
// The repository's Playwright on the Mac; where only another Playwright build has browsers
// (a container), that one: ARENA_PLAYWRIGHT names its module.
const PLAYWRIGHT=process.env.ARENA_PLAYWRIGHT||'/opt/npm-tools/node_modules/playwright/index.mjs';
const {chromium}=await import(existsSync(PLAYWRIGHT)?PLAYWRIGHT:'@playwright/test');
// Open a screen (Round 4: no sidebar at any width). The lion and wordmark go Home; everything
// else is in the ☰ menu, where Projects, Agents and Review are listed only in Advanced mode:
// asked for one of those, this turns Advanced mode on from the menu's own switch first.
const go=async(page,name)=>{if(name==='Home'){await page.getByRole('button',{name:'Arena home',exact:true}).click();return}await page.getByRole('button',{name:/^Menu\b/}).click();const sheet=page.getByRole('dialog',{name:'Menu'}),item=sheet.getByRole('button',{name,exact:true});await sheet.getByRole('switch',{name:'Advanced mode'}).waitFor();if(['Projects','Agents','Review'].includes(name)&&!(await item.count()))await sheet.getByRole('switch',{name:'Advanced mode'}).click();await item.click();};
// The technical build view (Advanced mode, turned on by visiting Projects first): Build
// history → Technical build view.
const technical=async page=>{await go(page,'Build history');await page.getByRole('button',{name:'Technical build view',exact:true}).click();};
const root=process.cwd(), url='https://leonards-macbook-air-1.tail11ad03.ts.net', out='docs/build-evidence/ui-live-provisioning';
mkdirSync(out,{recursive:true});
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch(); const report={checks:{}};
try{
 const ctx=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true});
 const page=await ctx.newPage(); const errors=[]; page.on('pageerror',e=>errors.push(e.message));
 await page.goto(url); await page.getByLabel('One-time pairing code').fill(code); await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByLabel('Talk to Arena').waitFor();
 await page.getByRole('button',{name:'New project',exact:true}).click();
 await page.getByLabel('What should Arena build?').fill('A tiny weather widget web page that shows the forecast for my city with a dark theme.');
 await page.getByRole('button',{name:'Propose project',exact:true}).click();
 await page.getByText('Proposed project',{exact:false}).waitFor({timeout:60000});
 await page.waitForTimeout(500);
 const card=await page.locator('.proposal').first().innerText().catch(()=>'' );
 report.checks.proposal_excerpt=card.replace(/\s+/g,' ').slice(0,300);
 report.checks.approve_button=await page.getByRole('button',{name:'Approve & create'}).count();
 await page.screenshot({path:`${out}/phone-proposal.png`,fullPage:false});
 await page.getByRole('button',{name:'Change',exact:true}).click();  // creates nothing
 await go(page,'Projects');
 await page.waitForTimeout(800);
 report.checks.smoke_project_listed=await page.getByText('Arena Smoke 20261028').count();
 report.checks.github_link=await page.getByRole('link',{name:/Open .* on GitHub/}).count();
 await page.screenshot({path:`${out}/phone-projects.png`,fullPage:true});
 await technical(page);
 await page.waitForTimeout(800);
 const picker=page.getByLabel('Request').first();
 const options=await picker.locator('option').allTextContents().catch(()=>[]);
 const smoke=options.find(o=>/pomodoro/i.test(o)); if(smoke) await picker.selectOption({label:smoke});
 await page.waitForTimeout(800);
 report.checks.milestones_heading=await page.getByText('Milestones',{exact:false}).count();
 report.checks.final_integrated=await page.getByText(/Integration and delivery/).count();
 await page.screenshot({path:`${out}/phone-build-smoke.png`,fullPage:true});
 await page.setViewportSize({width:1440,height:1000});
 await go(page,'Home');
 await page.waitForTimeout(800);
 await page.screenshot({path:`${out}/desk-overview.png`,fullPage:false});
 await technical(page);
 await page.waitForTimeout(800);
 await page.screenshot({path:`${out}/desk-build.png`,fullPage:false});
 report.checks.page_errors=errors; report.passed=report.checks.approve_button===1&&report.checks.smoke_project_listed>0&&errors.length===0;
 await go(page,'Device session'); await page.getByRole('button',{name:'Unpair this device'}).click();
 writeFileSync(`${out}/result.json`,JSON.stringify(report,null,2)); console.log(JSON.stringify(report.checks)); console.log(report.passed?'LIVE_PROVISION_UI_OK':'LIVE_PROVISION_UI_MISMATCH');
}finally{await browser.close()}

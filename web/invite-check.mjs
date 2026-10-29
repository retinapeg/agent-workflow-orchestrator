// A paired device mints a code in-app; a second device pairs with it; no host command needed.
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
const root=process.cwd(), url=process.env.ARENA_URL||'https://leonards-macbook-air-1.tail11ad03.ts.net', out='docs/build-evidence/session-persistence';
mkdirSync(out,{recursive:true});
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch();
try{
 const phone={viewport:{width:390,height:844},isMobile:true,hasTouch:true};
 const a=await browser.newContext(phone); const pa=await a.newPage(); const errors=[]; pa.on('pageerror',e=>errors.push(e.message));
 await pa.goto(url); await pa.getByLabel('One-time pairing code').fill(code); await pa.getByRole('button',{name:'Pair device',exact:true}).click();
 await pa.getByLabel('Talk to Arena').waitFor();
 await go(pa,'Device session');
 await pa.getByRole('button',{name:'Add another device'}).click();
 const invite=(await pa.locator('.invite-code').textContent({timeout:15000})).trim();
 await pa.locator('dialog').screenshot({path:out+'/06-invite.png'});
 const b=await browser.newContext(phone); const pb=await b.newPage(); pb.on('pageerror',e=>errors.push(e.message));
 await pb.goto(url); await pb.getByLabel('One-time pairing code').fill(invite); await pb.getByRole('button',{name:'Pair device',exact:true}).click();
 await pb.getByLabel('Talk to Arena').waitFor();
 const again=await pb.request.post(url+'/api/v1/pair',{data:{code:invite},headers:{'Origin':url}});
 if(again.status()!==403)throw Error('invite code reusable: '+again.status());
 // tidy: unpair both automation devices
 for(const p of [pa,pb]){await go(p,'Device session').catch(()=>{});await p.getByRole('button',{name:'Unpair this device'}).click().catch(()=>{});}
 if(errors.length)throw Error(errors.join('\n'));
 writeFileSync(out+'/invite-result.json',JSON.stringify({passed:true,invite_length:invite.length},null,2));
 console.log('INVITE_FLOW_PASSED');
}finally{await browser.close()}

// A paired device mints a code in-app; a second device pairs with it; no host command needed.
import {chromium} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {mkdirSync, writeFileSync} from 'node:fs';
const root=process.cwd(), url=process.env.ARENA_URL||'https://leonards-macbook-air-1.tail11ad03.ts.net', out='docs/build-evidence/session-persistence';
mkdirSync(out,{recursive:true});
const code=execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',root+'/.arena-control','pair'],{encoding:'utf8'}).trim();
const browser=await chromium.launch();
try{
 const phone={viewport:{width:390,height:844},isMobile:true,hasTouch:true};
 const a=await browser.newContext(phone); const pa=await a.newPage(); const errors=[]; pa.on('pageerror',e=>errors.push(e.message));
 await pa.goto(url); await pa.getByLabel('One-time pairing code').fill(code); await pa.getByRole('button',{name:'Pair device',exact:true}).click();
 await pa.getByRole('heading',{name:'Build control.',exact:true}).waitFor();
 await pa.getByRole('button',{name:'Device session'}).click();
 await pa.getByRole('button',{name:'Add another device'}).click();
 const invite=(await pa.locator('.invite-code').textContent({timeout:15000})).trim();
 await pa.locator('dialog').screenshot({path:out+'/06-invite.png'});
 const b=await browser.newContext(phone); const pb=await b.newPage(); pb.on('pageerror',e=>errors.push(e.message));
 await pb.goto(url); await pb.getByLabel('One-time pairing code').fill(invite); await pb.getByRole('button',{name:'Pair device',exact:true}).click();
 await pb.getByRole('heading',{name:'Build control.',exact:true}).waitFor();
 const again=await pb.request.post(url+'/api/v1/pair',{data:{code:invite},headers:{'Origin':url}});
 if(again.status()!==403)throw Error('invite code reusable: '+again.status());
 // tidy: unpair both automation devices
 for(const p of [pa,pb]){await p.getByRole('button',{name:'Device session'}).click().catch(()=>{});await p.getByRole('button',{name:'Unpair this device'}).click().catch(()=>{});}
 if(errors.length)throw Error(errors.join('\n'));
 writeFileSync(out+'/invite-result.json',JSON.stringify({passed:true,invite_length:invite.length},null,2));
 console.log('INVITE_FLOW_PASSED');
}finally{await browser.close()}

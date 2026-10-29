// Round 2 E acceptance on a COPY of the real control state (tests/control/real_state_server.py):
// the Device session dialog shows the attempt workspaces on the Mac. Workspace directories
// live only on the Mac, so a few are created in the scratch home for real attempt ids, and
// three of those attempts are aged past seven days in the COPY's ledger. Then the dialog's
// line must agree with GET /api/v1/system and with a dry run of prune-workspaces on the copy
// (never --apply). The hint must be the runnable dry run from the Arena folder, and that
// command run from any other folder must refuse (exit 1) and create nothing. 390 px, then
// 360 px (the command wraps only between words and copies intact), Android user agent;
// dispatch disabled; the live database and the service on 8791 are never touched.
// Screenshots: docs/build-evidence/usability/round2/.
import {spawn, spawnSync} from 'node:child_process';
import {existsSync,mkdirSync,mkdtempSync,readdirSync,rmSync,utimesSync,writeFileSync} from 'node:fs';
import {tmpdir} from 'node:os';
const PLAYWRIGHT='/opt/npm-tools/node_modules/playwright/index.mjs';
const {chromium}=await import(existsSync(PLAYWRIGHT)?PLAYWRIGHT:'@playwright/test');
const root=process.cwd(),state=mkdtempSync(tmpdir()+'/arena-real-'),port=process.env.ARENA_UI_PORT||'8797',base='http://127.0.0.1:'+port;
const output=root+'/docs/build-evidence/usability/round2';mkdirSync(output,{recursive:true});
const ANDROID='Mozilla/5.0 (Linux; Android 14; Pixel 8) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130.0.0.0 Mobile Safari/537.36';
const COMMAND='.venv/bin/python -m agent_arena.control --home .arena-control prune-workspaces';
const python=root+'/.venv/bin/python',env={...process.env,PYTHONPATH:root+'/src',ARENA_UI_STATE:state,ARENA_UI_PORT:port};
const server=spawn(python,['-m','uvicorn','tests.control.real_state_server:create_app','--factory','--host','127.0.0.1','--port',port],{cwd:root,env,stdio:'pipe'});
let serverLog='';server.stdout.on('data',d=>serverLog+=d);server.stderr.on('data',d=>serverLog+=d);
let browser;const result={state,viewport:390,userAgent:ANDROID,dispatch:false};
const sleep=ms=>new Promise(r=>setTimeout(r,ms));
const overflow=page=>page.evaluate(()=>document.documentElement.scrollWidth>innerWidth);
// Read or edit the COPY of the state directly (the fixture refuses mutations over HTTP).
const py=(code,...args)=>{const r=spawnSync(python,['-c',code,state+'/control.sqlite3',...args],{encoding:'utf8',env});if(r.status!==0)throw Error('python failed: '+r.stderr);return r.stdout.trim()};
try{
 for(let n=0;n<80;n++){if(server.exitCode!==null)throw Error('Fixture server exited with '+server.exitCode+' (is port '+port+' free?)\n'+serverLog.slice(-800));try{if((await fetch(base)).ok)break}catch{}await sleep(250);}
 if(server.exitCode!==null)throw Error('Fixture server exited\n'+serverLog.slice(-800));
 // Six real attempts of finished tasks: three aged ten days (prunable), three left as they are.
 const picked=JSON.parse(py(`import json,sqlite3,sys
db=sqlite3.connect(sys.argv[1])
rows=db.execute("SELECT a.id,t.state FROM attempts a JOIN tasks t ON t.id=a.task_id WHERE t.state IN ('completed','cancelled','blocked') AND a.state NOT IN ('leased','running','verifying','reviewing','recovering') ORDER BY a.id").fetchall()
done=[r[0] for r in rows if r[1]=='completed']
print(json.dumps({'old':done[:3],'recent':done[3:6]}))`));
 if(picked.old.length<3||picked.recent.length<3)throw Error('Not enough finished attempts in the copy: '+JSON.stringify(picked));
 const aged=Math.floor(Date.now()/1000)-10*86400;
 py(`import sqlite3,sys
db=sqlite3.connect(sys.argv[1]);ids=sys.argv[3:];then=int(sys.argv[2]);q=",".join("?"*len(ids))
db.execute(f"UPDATE attempts SET data=json_set(data,'$.created',?,'$.ended',?) WHERE id IN ({q})",(then,then,*ids))
db.execute(f"UPDATE tasks SET data=json_set(data,'$.updated',?) WHERE id IN (SELECT task_id FROM attempts WHERE id IN ({q}))",(then,*ids))
db.commit()`,String(aged),...picked.old);
 // Every attempt of an aged task now has an old task; keep the "recent" ones on other tasks.
 const sameTask=JSON.parse(py(`import json,sqlite3,sys
db=sqlite3.connect(sys.argv[1]);ids=sys.argv[2:]
print(json.dumps([r[0] for r in db.execute("SELECT a.id FROM attempts a WHERE a.task_id IN (SELECT task_id FROM attempts WHERE id IN (%s))"%",".join("?"*len(ids)),ids)]))`,...picked.old));
 picked.recent=picked.recent.filter(id=>!sameTask.includes(id));
 const sizes={};
 for(const [i,id] of [...picked.old,...picked.recent].entries()){
  const dir=state+'/workspaces/'+id;mkdirSync(dir+'/src',{recursive:true});
  writeFileSync(dir+'/src/blob.bin',Buffer.alloc((i+1)*1048576,1));sizes[id]=(i+1)*1048576;
  if(picked.old.includes(id))utimesSync(dir,aged,aged);
 }
 result.created={old:picked.old,recent:picked.recent};

 browser=await chromium.launch();
 const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true,hasTouch:true,userAgent:ANDROID,deviceScaleFactor:2});
 const page=await context.newPage();const errors=[];page.on('pageerror',e=>errors.push(e.message));
 await page.goto(base);
 await page.getByLabel('One-time pairing code').fill('real-state-one-use-code');
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByRole('navigation',{name:'Primary'}).waitFor();

 const system=await page.evaluate(async()=>(await (await fetch('/api/v1/system',{cache:'no-store'})).json()).workspaces);
 result.system=system;
 const total=picked.old.length+picked.recent.length;
 if(system.count!==total)throw Error('count '+system.count+' != '+total);
 if(system.prunable_count!==picked.old.length)throw Error('prunable '+system.prunable_count+' != '+picked.old.length);
 if(system.bytes<Object.values(sizes).reduce((a,b)=>a+b,0))throw Error('bytes too small: '+system.bytes);
 // The same numbers from a dry run of the Mac command on the copy (nothing is removed).
 const dry=spawnSync(python,['-m','agent_arena.control','--home',state,'prune-workspaces','--json'],{cwd:root,encoding:'utf8',env});
 if(dry.status!==0)throw Error('dry run failed: '+dry.stderr);
 const report=JSON.parse(dry.stdout);
 result.dryRun={count:report.count,bytes:report.bytes,prunable_count:report.prunable_count,prunable_bytes:report.prunable_bytes,would_remove:report.workspaces.filter(w=>w.verdict==='prune').map(w=>w.name)};
 for(const k of ['count','bytes','prunable_count','prunable_bytes'])if(report[k]!==system[k])throw Error('dry run '+k+' '+report[k]+' != endpoint '+system[k]);
 if([...picked.old,...picked.recent].some(id=>!existsSync(state+'/workspaces/'+id)))throw Error('The dry run removed something');
 const text=spawnSync(python,['-m','agent_arena.control','--home',state,'prune-workspaces'],{cwd:root,encoding:'utf8',env}).stdout;
 result.dryRunText=text.replaceAll(state,'<copy>');
 // The hint's command (relative --home) run from a folder that is not the Arena folder:
 // it must refuse and create nothing, not report an empty Mac.
 const away=mkdtempSync(tmpdir()+'/arena-not-here-');
 const wrong=spawnSync(python,['-m','agent_arena.control','--home','.arena-control','prune-workspaces'],{cwd:away,encoding:'utf8',env});
 const created=readdirSync(away);rmSync(away,{recursive:true,force:true});
 result.wrongFolder={status:wrong.status,stderr:wrong.stderr.trim().replaceAll(away,'<elsewhere>'),created};
 if(wrong.status!==1||!wrong.stderr.includes('No Arena control state at')||created.length)throw Error('Hint command from the wrong folder: '+JSON.stringify(result.wrongFolder));

 // The Device session dialog (top-bar avatar on the phone; System status on wide screens).
 const device=page.getByRole('button',{name:'Device session',exact:true});
 await ((await device.isVisible())?device:page.getByRole('button',{name:'System status'})).click();
 const dialog=page.getByRole('dialog');await dialog.waitFor();
 const row=dialog.locator('.facts > div',{hasText:'Workspaces on the Mac'});
 await page.waitForFunction(()=>[...document.querySelectorAll('.facts > div')].some(d=>d.textContent.includes('Workspaces on the Mac')&&!/Measuring/.test(d.textContent)),null,{timeout:15000});
 const line=(await row.locator('dd').innerText()).replace(/\u00a0/g,' ').trim();
 const hint=(await dialog.locator('p.small.muted',{hasText:'prune-workspaces'}).innerText()).trim();
 result.line=line;result.hint=hint;
 if(!/^\d+ workspaces · [\d.]+ (?:B|KB|MB|GB) · \d+ prunable \(older than 7 days\)$/.test(line))throw Error('Unexpected line: '+line);
 if(!line.startsWith(total+' workspaces')||!line.includes(' · '+picked.old.length+' prunable'))throw Error('Line disagrees with the host: '+line);
 if(!hint.includes('In the Arena folder on the Mac: '+COMMAND+' (dry run; add --apply to remove)'))throw Error('Hint is not the runnable dry run: '+hint);
 if(!/ can be freed\./.test(hint))throw Error('Hint does not say what the command frees: '+hint);
 if(await dialog.getByRole('button',{name:/prune|delete|remove|free/i}).count())throw Error('The phone offers a delete button');
 if(await overflow(page))throw Error('Horizontal overflow in the Device session dialog');
 await page.screenshot({path:output+'/e-devices-disk-390.png'});
 // 360 px: every word of the commands stays on one line (never "--" / "home"), the lines
 // break only between words, and the command's text is exactly what runs.
 await page.setViewportSize({width:360,height:780});
 const hintBox=dialog.locator('p.small.muted',{hasText:'prune-workspaces'});
 await hintBox.scrollIntoViewIfNeeded();
 const wrap=await hintBox.evaluate(p=>({
  command:p.querySelector('code').textContent,
  words:[...p.querySelectorAll('code .nowrap')].map(s=>({text:s.textContent,rects:s.getClientRects().length,top:Math.round(s.getBoundingClientRect().top)})),
 }));
 result.wrap360={command:wrap.command,lines:new Set(wrap.words.filter(w=>w.text!=='--apply').map(w=>w.top)).size,broken:wrap.words.filter(w=>w.rects!==1).map(w=>w.text)};
 if(wrap.command!==COMMAND)throw Error('The command does not copy intact: '+JSON.stringify(wrap.command));
 if(wrap.words.map(w=>w.text).join(' ')!==COMMAND+' --apply')throw Error('Command words: '+JSON.stringify(wrap.words));
 if(result.wrap360.broken.length)throw Error('Words broken across lines at 360 px: '+result.wrap360.broken.join(', '));
 if(await overflow(page))throw Error('Horizontal overflow at 360 px');
 await page.screenshot({path:output+'/e-devices-disk-360.png'});
 if(errors.length)throw Error(errors.join('\n'));
 result.passed=true;
 writeFileSync(output+'/e-result.json',JSON.stringify(result,null,2)+'\n');
 console.log(JSON.stringify({line,hint,system,dryRun:result.dryRun,wrongFolder:result.wrongFolder,wrap360:result.wrap360},null,1));
 console.log(result.dryRunText);
 console.log('ROUND2_E_ACCEPTANCE_PASSED');
}catch(error){result.passed=false;result.failure=String(error);writeFileSync(output+'/e-result.json',JSON.stringify(result,null,2)+'\n');throw error}
finally{
 if(browser)await browser.close();
 const exited=new Promise(r=>{if(server.exitCode!==null)r();else{server.once('exit',r);setTimeout(r,5000)}});
 server.kill('SIGTERM');await exited;
 rmSync(state,{recursive:true,force:true});
}

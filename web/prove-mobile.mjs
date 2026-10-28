// Real-agent acceptance driver. No provider fixtures or intercepted API responses.
import {chromium} from '@playwright/test';
import {execFileSync} from 'node:child_process';
import {mkdirSync,readFileSync,writeFileSync} from 'node:fs';
import {resolve} from 'node:path';
if(process.env.ARENA_LIVE!=='1')throw Error('ARENA_LIVE=1 is required');
const root=process.cwd(),home=process.env.ARENA_HOME,url=process.env.ARENA_URL;
if(!home||!url||!process.env.ARENA_PROJECT)throw Error('Run scripts/prove_mobile.py');
const control=(...args)=>execFileSync(root+'/.venv/bin/python',['-m','agent_arena.control','--home',home,...args],{cwd:root,encoding:'utf8'}).trim();
const code=control('pair');
const browser=await chromium.launch();
const context=await browser.newContext({viewport:{width:390,height:844},isMobile:true});
let page=await context.newPage();
const errors=[];context.on('page',p=>p.on('pageerror',e=>errors.push(e.message)));
page.on('pageerror',e=>errors.push(e.message));
const evidence=resolve(root,'docs/build-evidence/complete-live');mkdirSync(evidence,{recursive:true});
try{
 await page.goto(url);
 await page.getByLabel('One-time pairing code').fill(code);
 await page.getByRole('button',{name:'Pair device',exact:true}).click();
 await page.getByRole('heading',{name:'Build control.',exact:true}).waitFor();
 const before=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json());
 await page.getByRole('button',{name:'New request',exact:true}).click();
 await page.getByLabel('Your project').selectOption(process.env.ARENA_PROJECT);
 await page.getByLabel('What would you like to change?').fill(process.env.ARENA_INSTRUCTIONS);
 await page.getByRole('button',{name:'Build this for me',exact:true}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 await page.screenshot({path:evidence+'/submitted.png',fullPage:true});
 await page.close();page=await context.newPage();await page.goto(url);
 const started=Date.now();let snapshot,tasks=[],restarted=false,last='',restartAttempts=[];
 while(Date.now()-started<1200000){
  try{snapshot=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json())}catch{await new Promise(r=>setTimeout(r,1000));continue}
  tasks=snapshot.tasks.filter(t=>t.project_id===process.env.ARENA_PROJECT&&!before.tasks.some(b=>b.id===t.id));
  const state=tasks.map(t=>t.title+': '+t.state).join('; ');if(state!==last){console.log(state);last=state}
  if(tasks.some(t=>t.state==='awaiting_input'))throw Error('Director requested a behavior decision; answer it in the phone UI before continuing proof');
  if(tasks.some(t=>['blocked','needs_permission','needs_auth','interrupted','cancelled'].includes(t.state)))throw Error(state);
  const builds=snapshot.attempts.filter(a=>tasks.some(t=>t.id===a.task_id)&&a.phase==='build'&&['running','verifying'].includes(a.state));
  if(!restarted&&builds.length>=2&&url.startsWith('http://127.0.0.1:')){
   await page.screenshot({path:evidence+'/parallel-workers.png',fullPage:true});
   restartAttempts=builds.map(a=>a.id);
   control('stop');
   for(let n=0;n<80;n++){try{await fetch(url)}catch{break}await new Promise(r=>setTimeout(r,250));}
   control('start','--port',new URL(url).port);
   restarted=true;console.log('Restarted HTTP scheduler while independent guardians worked');
  }
  const children=tasks.filter(t=>t.phase!=='plan');
  if(children.length>=2&&children.every(t=>t.state==='ready_for_approval'))break;
  await new Promise(r=>setTimeout(r,1000));
 }
 if(!tasks.filter(t=>t.phase!=='plan').every(t=>t.state==='ready_for_approval'))throw Error('Timed out before verified staging');
 const attempts=snapshot.attempts.filter(a=>tasks.some(t=>t.id===a.task_id));
 const director=attempts.find(a=>a.phase==='plan'&&a.state==='passed');
 if(!director||director.model!=='gpt-6-astra'||director.effort!=='max'||!director.result.plan?.outcome)throw Error('Director specification was not produced by the requested CLI model');
 for(const t of tasks.filter(t=>t.phase!=='plan')){if(!attempts.some(a=>a.task_id===t.id&&a.phase==='direct'&&a.state==='passed'&&a.result.review?.approved))throw Error('Director evidence review missing');}
 const builds=attempts.filter(a=>a.phase==='build'&&a.state==='passed');
 if(tasks.filter(t=>t.phase!=='plan').length!==2)throw Error('Planner did not produce exactly two build tasks');
 if(!restarted)throw Error('Controller restart was not exercised during concurrent builds');
 for(const id of restartAttempts){if(!builds.some(a=>a.id===id))throw Error('A restarted worker was lost or replaced: '+id)}
 for(const t of tasks.filter(t=>t.phase!=='plan')){
  if(attempts.filter(a=>a.task_id===t.id&&a.phase==='build').length!==1)throw Error('Duplicate or repaired build: restart proof requires exactly one invocation per child');
  const review=attempts.find(a=>a.task_id===t.id&&a.phase==='review'&&a.state==='passed');
  const build=builds.find(a=>a.task_id===t.id);
  if(!review||review.account_id===build.account_id||!review.result.review?.approved)throw Error('Independent review missing');
 }
 const providers=new Set(builds.map(a=>snapshot.agents.find(x=>x.id===a.account_id).provider));
 if(!providers.has('codex')||!providers.has('claude'))throw Error('Both real providers must author a change');
 const overlaps=builds.some((a,i)=>builds.slice(i+1).some(b=>a.account_id!==b.account_id&&Math.max(a.result.provider_started,b.result.provider_started)<Math.min(a.result.provider_ended,b.result.provider_ended)));
 if(!overlaps)throw Error('No measured concurrent provider execution');
 await page.getByRole('navigation',{name:'Primary'}).getByRole('button',{name:'Review',exact:true}).click();
 await page.getByRole('button',{name:'Inspect evidence & diff'}).last().click();
 await page.getByRole('button',{name:'Read combined diff'}).click();
 await page.screenshot({path:evidence+'/review.png',fullPage:true});
 const project=snapshot.projects.find(p=>p.id===process.env.ARENA_PROJECT);
 const input=JSON.parse(readFileSync(process.env.ARENA_PROOF_INPUT||home+'/proof-input.json','utf8'));
 for(const file of input.scope){
  const patch=execFileSync('git',['-C',project.staging,'diff',project.base,'HEAD','--',file],{encoding:'utf8'});
  if(!patch.trim())throw Error('Goal did not change required file: '+file);
 }
 await page.getByRole('button',{name:'Accept staged change',exact:true}).click();
 await page.getByRole('dialog').waitFor({state:'hidden'});
 await page.getByRole('button',{name:'Inspect evidence & diff'}).last().click();
 await page.getByRole('button',{name:'Integrate into main',exact:true}).click();
 let publication;
 for(let n=0;n<60;n++){
  snapshot=await page.evaluate(async()=>await(await fetch('/api/v1/overview')).json());
  publication=snapshot.approvals.find(a=>a.publication&&tasks.some(t=>t.id===a.task_id));
  if(['integrated','failed'].includes(publication?.publication.state))break;
  await new Promise(r=>setTimeout(r,1000));
 }
 if(publication?.publication.state!=='integrated')throw Error('Main integration failed: '+JSON.stringify(publication));
 const actual=execFileSync('git',['-C',project.repo,'rev-parse','main'],{encoding:'utf8'}).trim();
 if(actual!==publication.candidate)throw Error('Source main does not match approved commit');
 for(const width of [390,1440]){
  await page.setViewportSize({width,height:1000});
  if(await page.evaluate(()=>document.documentElement.scrollWidth>innerWidth))throw Error('Horizontal overflow');
  await page.screenshot({path:evidence+'/integrated-'+width+'.png',fullPage:true});
 }
 if(errors.length)throw Error(errors.join('\n'));
 writeFileSync(evidence+'/result.json',JSON.stringify({passed:true,source:project.repo,main:actual,attempts,publication,restarted,concurrent:overlaps,errors,seconds:(Date.now()-started)/1000},null,2));
 console.log('REAL_PHONE_TO_MAIN_WORKFLOW_PASSED '+actual);
}finally{await browser.close()}

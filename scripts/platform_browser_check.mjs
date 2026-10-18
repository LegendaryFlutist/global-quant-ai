// Run against a dedicated headless Chrome, never print passwords, cookies or tokens.
// node scripts/platform_browser_check.mjs 9223 [URL]
import fs from 'node:fs';
import path from 'node:path';
const port=process.argv[2]??'9223';const base=process.argv[3]??'http://127.0.0.1:8080';
const output=path.resolve('runs/platform_phase1');fs.mkdirSync(output,{recursive:true});
const secret=fs.readFileSync('.runtime/bootstrap-admin.txt','utf8').match(/密码：([^\n]+)/)?.[1];
if(!secret)throw Error('Local bootstrap credential file missing; use a test account if the password was changed');
const tabs=await fetch(`http://127.0.0.1:${port}/json/list`).then(r=>r.json());
const target=tabs.find(t=>t.type==='page');if(!target)throw Error('No test Chrome page');
const ws=new WebSocket(target.webSocketDebuggerUrl);await new Promise(r=>ws.addEventListener('open',r,{once:true}));
let sequence=0;const pending=new Map();const errors=[];
ws.addEventListener('message',e=>{const data=JSON.parse(e.data);if(data.id){const entry=pending.get(data.id);if(entry){pending.delete(data.id);data.error?entry.reject(Error(data.error.message)):entry.resolve(data.result);}}else if(data.method==='Runtime.exceptionThrown')errors.push(data.params.exceptionDetails.text);});
const cdp=(method,params={})=>new Promise((resolve,reject)=>{const id=++sequence;pending.set(id,{resolve,reject});ws.send(JSON.stringify({id,method,params}));});
const evaluate=async(expression)=>{const r=await cdp('Runtime.evaluate',{expression,awaitPromise:true,returnByValue:true});if(r.exceptionDetails)throw Error(r.exceptionDetails.exception?.description??r.exceptionDetails.text);return r.result.value;};
const waitFor=async expression=>{for(let i=0;i<100;i++){if(await evaluate('Boolean('+expression+')'))return;await new Promise(r=>setTimeout(r,150));}throw Error('Browser condition timed out: '+expression);};
const click=async text=>evaluate(`(()=>{const e=[...document.querySelectorAll('button')].find(e=>e.textContent.trim()===${JSON.stringify(text)});if(!e)throw Error('Button missing');e.click();return true;})()`);
const screenshot=async name=>{const r=await cdp('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});fs.writeFileSync(path.join(output,name),Buffer.from(r.data,'base64'));};
try{
 await cdp('Runtime.enable');await cdp('Page.enable');await cdp('Emulation.setDeviceMetricsOverride',{width:1536,height:1100,deviceScaleFactor:1,mobile:false});
 await cdp('Page.navigate',{url:base});await waitFor(`document.querySelector('input[type=password]')`);
 await screenshot('login.png');
 await evaluate(`(()=>{const input=document.querySelector('input[type=password]');Object.getOwnPropertyDescriptor(HTMLInputElement.prototype,'value').set.call(input,${JSON.stringify(secret)});input.dispatchEvent(new Event('input',{bubbles:true}));return true;})()`);
 await evaluate(`document.querySelector('form').requestSubmit()`);
 await waitFor(`document.querySelector('.asset-cards')?.children.length===4`);
 await screenshot('dashboard.png');
 const summary=await evaluate(`(async()=>{const r=await fetch('/api/dashboard');if(!r.ok)throw Error('Dashboard API');return r.json();})()`);
 if(summary.candle_count<=0)throw Error('No real market data imported');
 await evaluate(`document.querySelector('.asset-card').click()`);
 await waitFor(`Number(document.querySelector('[data-testid=price-chart]')?.dataset.candles)>0`);
 await screenshot('koru-4h.png');
 const counts=[];
 for(const symbol of ['KORUUSDT','CLUSDT','NVDAUSDT','BTCUSDT']){
   await evaluate(`(()=>{const select=document.querySelector('select[aria-label="选择标的"]');select.value='binance_usdm:${symbol}';select.dispatchEvent(new Event('change',{bubbles:true}));})()`);
   await waitFor(`document.querySelector('.chart-meta strong')?.textContent===${JSON.stringify(symbol)}&&Number(document.querySelector('[data-testid=price-chart]')?.dataset.candles)>0`);
   for(const interval of ['15m','1h','4h','1d','1w','1M']){
     await click(interval);
     await waitFor(`document.querySelector('.chart-footer')?.dataset.loadedInterval===${JSON.stringify(interval)}&&document.querySelector('.chart-footer')?.dataset.loadedAsset==='binance_usdm:${symbol}'&&Number(document.querySelector('[data-testid=price-chart]')?.dataset.candles)>0`);
     counts.push({symbol,interval,candles:await evaluate(`Number(document.querySelector('[data-testid=price-chart]').dataset.candles)`)});
   }
 }
 await evaluate(`document.querySelector('nav button:nth-child(2)').click()`);await waitFor(`document.querySelectorAll('tbody tr').length>=40`);await screenshot('asset-registry.png');
 await evaluate(`document.querySelector('nav button:nth-child(4)').click()`);await waitFor(`document.querySelector('h1')?.textContent==='后台任务'`);await screenshot('tasks.png');
 await evaluate(`document.querySelector('nav button:nth-child(5)').click()`);await waitFor(`document.querySelectorAll('.capability-grid .panel').length===8`);await screenshot('capabilities.png');
 await evaluate(`document.querySelector('nav button:nth-child(6)').click()`);await waitFor(`document.querySelector('h1')?.textContent==='系统设置'`);await screenshot('settings.png');
 await cdp('Emulation.setDeviceMetricsOverride',{width:390,height:844,deviceScaleFactor:1,mobile:true});await screenshot('mobile-settings.png');
 if(errors.length)throw Error('Uncaught browser errors: '+errors.join(';'));
 const report={status:'PASS',checked_at:new Date().toISOString(),authenticated:true,priority_assets:summary.priority_assets.map(a=>a.symbol),assets:summary.asset_count,candles:summary.candle_count,charts:counts,uncaught_browser_errors:errors,execution_mode:summary.execution_mode};
 fs.writeFileSync(path.join(output,'browser-check.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report));
 await evaluate(`(async()=>{const user=await fetch('/api/auth/me').then(r=>r.json());await fetch('/api/auth/logout',{method:'POST',headers:{'X-CSRF-Token':user.csrf_token}});})()`);
}finally{ws.close();}

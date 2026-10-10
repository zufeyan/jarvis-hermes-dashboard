// Real isolated headless Chrome checks: no microphone, playback or user profile.
const fs=require('node:fs'),path=require('node:path'),{spawn}=require('node:child_process');
const assert=require('node:assert/strict');
const out='/Volumes/Data_Sufayan/JARVIS_Thai_Voice/verification/dashboard';
const profile=fs.mkdtempSync('/Users/sufeyanwaedueraman/.hermes/cache/scratch/jarvis-cdp-');
const chrome=spawn('/Applications/Google Chrome.app/Contents/MacOS/Google Chrome',[
 '--headless','--disable-gpu','--no-first-run','--no-default-browser-check',
 '--remote-debugging-port=0','--user-data-dir='+profile,'about:blank'
],{stdio:'ignore',detached:true});
let socket;
const delay=ms=>new Promise(r=>setTimeout(r,ms));
(async()=>{
 try{
  let port;
  for(let i=0;i<100;i++){try{port=Number(fs.readFileSync(path.join(profile,'DevToolsActivePort'),'utf8').split('\n')[0]);break;}catch{await delay(100);}}
  assert(port,'Chrome CDP unavailable');
  const pages=await (await fetch(`http://127.0.0.1:${port}/json/list`)).json();
  socket=new WebSocket(pages.find(p=>p.type==='page').webSocketDebuggerUrl);
  await new Promise((resolve,reject)=>{socket.onopen=resolve;socket.onerror=reject;});
  let serial=0;const pending=new Map(),errors=[];
  socket.onmessage=e=>{const m=JSON.parse(e.data);if(m.id){const job=pending.get(m.id);if(job){pending.delete(m.id);m.error?job.reject(m.error):job.resolve(m.result);}}else if(m.method==='Runtime.exceptionThrown')errors.push(m.params.exceptionDetails.text);};
  const rpc=(method,params={})=>new Promise((resolve,reject)=>{const id=++serial;pending.set(id,{resolve,reject});socket.send(JSON.stringify({id,method,params}));});
  await rpc('Runtime.enable');await rpc('Page.enable');
  const rows=[];
  for(const [name,width,height] of [['desktop',1280,900],['mobile',390,844]]){
   await rpc('Emulation.setDeviceMetricsOverride',{width,height,deviceScaleFactor:1,mobile:name==='mobile'});
   await rpc('Page.navigate',{url:'http://127.0.0.1:8730'});
   let info;
   for(let i=0;i<80;i++){
    const r=await rpc('Runtime.evaluate',{expression:`JSON.stringify({ready:document.querySelector('#voice-status')?.dataset.kind,core:!!window.JarvisVoiceCore,status:document.querySelector('#voice-status')?.textContent,overflow:document.documentElement.scrollWidth>innerWidth,agentDisabled:document.querySelector('#run')?.disabled,chatReady:document.querySelector('#chat-status')?.dataset.kind==='ready',confirmDisabled:document.querySelector('#confirm-send')?.disabled,privacy:document.querySelector('#privacy')?.textContent,buttons:Array.from(document.querySelectorAll('button')).map(x=>({id:x.id,text:x.textContent,disabled:x.disabled}))})`,returnByValue:true});
    try{info=JSON.parse(r.result.value);}catch{}
    if(info?.ready==='ready')break;await delay(100);
   }
   assert.equal(info?.ready,'ready');assert.equal(info.core,true);assert.equal(info.agentDisabled,true);assert.equal(info.overflow,false);
   assert.equal(info.chatReady,true);assert.equal(info.confirmDisabled,true);
   if(name==='desktop' && process.env.JARVIS_CHAT_E2E==='1'){
    await rpc('Runtime.evaluate',{expression:`document.querySelector('#chat-input').value='อธิบาย IP Address เป็นภาษาไทยหนึ่งประโยคและยกตัวอย่าง 192.168.1.10';document.querySelector('#chat-input').dispatchEvent(new Event('input'));document.querySelector('#confirm-send').click();`});
    let reply;
    for(let i=0;i<300;i++){
     const r=await rpc('Runtime.evaluate',{expression:`JSON.stringify({kind:document.querySelector('#chat-status').dataset.kind,text:document.querySelector('#reply').textContent,readEnabled:!document.querySelector('#read-reply').disabled})`,returnByValue:true});
     reply=JSON.parse(r.result.value);if(reply.kind==='done'||reply.kind==='error')break;await delay(100);
    }
    assert.equal(reply.kind,'done');assert.match(reply.text,/192\.168\.1\.10/);assert.equal(reply.readEnabled,true);
    info.actualChatReply=reply.text;info.explicitReadReady=true;info.playbackTested=false;
   }
   rows.push({viewport:name,width,height,...info});
   const shot=await rpc('Page.captureScreenshot',{format:'png',captureBeyondViewport:false});
   fs.writeFileSync(path.join(out,'dashboard-'+name+'.png'),Buffer.from(shot.data,'base64'));
  }
  assert.deepEqual(errors,[]);
  const report={passed:true,source:'real isolated Chrome CDP',microphoneTested:false,playbackTested:false,errors,views:rows};
  fs.writeFileSync(path.join(out,'browser-report.json'),JSON.stringify(report,null,2));console.log(JSON.stringify(report,null,2));
 }finally{socket?.close();try{process.kill(-chrome.pid,'SIGTERM');}catch{}}
})().catch(e=>{console.error(e);process.exitCode=1;});
setTimeout(()=>{try{process.kill(-chrome.pid,'SIGKILL');}catch{};process.exit(2);},45000).unref();

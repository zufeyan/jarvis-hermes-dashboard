'use strict';
const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const {markdownToSafeHtml}=require('../../ui/voice-core.js');
const READY={stt:'local',tts:'edge',tools_enabled:false,agent_enabled:false,server_stt:true,server_tts:true};

function mount(statusPayload=READY, overrides={}) {
  const ids=['voice-status','record','stop-audio','cancel-listen','speak','transcript','privacy','tts-state','agent-notice','run'];
  const elements=Object.fromEntries(ids.map(id=>[id,{id,textContent:'',value:'',disabled:false,dataset:{},attrs:{},listeners:{},addEventListener(type,fn){this.listeners[type]=fn;},setAttribute(k,v){this.attrs[k]=v;},removeAttribute(){},load(){},pause(){}}]));
  const calls=[];
  const document={querySelector(s){if(s==='meta[name="jarvis-token"]')return {content:'test-token'};return elements[s.slice(1)]||null;}};
  class AudioMock {constructor(url){this.url=url;this.listeners={};} addEventListener(type,fn){this.listeners[type]=fn;} async play(){} pause(){} removeAttribute(){} load(){}}
  const context={document,window:{addEventListener(){}},navigator:{mediaDevices:{getUserMedia:async()=>({getTracks:()=>[]})}},URL:{createObjectURL:()=> 'blob:test',revokeObjectURL(){}},Audio:AudioMock,AbortController,Blob,fetch:async(url,opts={})=>{calls.push({url,opts});return url==='/api/status'?{ok:true,json:async()=>statusPayload}:{ok:true,blob:async()=>new Blob(['audio'],{type:'audio/mpeg'})};},console,setTimeout,clearTimeout,...overrides};
  vm.createContext(context);
  for(const file of ['voice-core.js','app.js']) vm.runInContext(fs.readFileSync(path.join(__dirname,'../../ui/'+file),'utf8'),context,{filename:'ui/'+file});
  return {elements,calls,context};
}
const tick=()=>new Promise(resolve=>setImmediate(resolve));

test('voice UI boots actual browser scripts and never enables agent send',async()=>{
  const {elements}=mount(); await tick();
  assert.equal(elements.run.disabled,true);
  assert.match(elements['agent-notice'].textContent,/ยังไม่เปิดการสั่งงาน/);
  assert.equal(elements.record.disabled,false);
  assert.equal(elements.speak.disabled,true);
  assert.match(elements.privacy.textContent,/local STT/);
});

test('selected text uses TTS endpoint only after explicit click',async()=>{
  const {elements,calls}=mount(); await tick();
  assert.equal(calls.some(c=>c.url==='/api/speak'),false);
  elements.transcript.value='Say VLAN 42'; elements.transcript.listeners.input();
  await elements.speak.listeners.click();
  assert.ok(calls.some(c=>c.url==='/api/speak'&&JSON.parse(c.opts.body).text==='Say VLAN 42'));
  assert.equal(calls.some(c=>c.url==='/api/run'),false);
  elements['stop-audio'].listeners.click();
});

test('response renderer preserves identifiers',()=>{
  const output=markdownToSafeHtml('Use TOKEN_42 and 192.168.1.10\n\n- **safe**');
  assert.match(output,/TOKEN_42/); assert.match(output,/192\.168\.1\.10/); assert.match(output,/<strong>safe<\/strong>/);
});

test('pending microphone cannot start twice and cancel stops late stream',async()=>{
  let resolveStream, requests=0, stopped=0;
  const permission=new Promise(r=>resolveStream=r);
  const {elements}=mount(READY,{navigator:{mediaDevices:{getUserMedia(){requests++;return permission;}}}});
  await tick();
  const first=elements.record.listeners.click();
  assert.equal(elements.record.disabled,true);
  await elements.record.listeners.click();
  assert.equal(requests,1);
  await elements['cancel-listen'].listeners.click();
  resolveStream({getTracks:()=>[{stop(){stopped++;}}]});
  await first;
  assert.equal(stopped,1);
});

test('reading disabled while recording',async()=>{
  class Recorder {constructor(){this.state='inactive';this.mimeType='audio/webm';this.events={};} addEventListener(n,f){this.events[n]=f;} start(){this.state='recording';} stop(){this.state='inactive';this.events.stop?.();}}
  const {elements}=mount(READY,{MediaRecorder:Recorder}); await tick();
  await elements.record.listeners.click();
  elements.transcript.value='สวัสดี'; elements.transcript.listeners.input();
  try {assert.equal(elements.speak.disabled,true);} finally {await elements['cancel-listen'].listeners.click();}
});

test('network TTS failure displays error instead of silently returning busy',async()=>{
  const {elements}=mount(READY,{fetch:async url=>{if(url==='/api/status')return {ok:true,json:async()=>READY};throw new Error('offline');}});
  await tick(); elements.transcript.value='สวัสดี';
  await elements.speak.listeners.click();
  assert.equal(elements['voice-status'].dataset.kind,'error');
});

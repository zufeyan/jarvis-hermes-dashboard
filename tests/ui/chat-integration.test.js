'use strict';
const {test}=require('node:test'); const assert=require('node:assert/strict'); const fs=require('node:fs'); const vm=require('node:vm');
function mount(payload={chat_enabled:true,tools_enabled:false,agent_enabled:true,scope:'chat-only'},fetcher){
 const ids=['voice-status','chat-status','record','stop-audio','cancel-listen','speak','transcript','privacy','tts-state','agent-notice','run','confirm-send','cancel-run','new-chat','reply','read-reply','chat-input','chat-send','transcript-digits','chat-digits','approval-panel','approval-detail','approve-yes','approve-no'];
 const els=Object.fromEntries(ids.map(id=>[id,{id,textContent:'',innerHTML:'',value:'',disabled:false,dataset:{},attrs:{},listeners:{},addEventListener(t,f){this.listeners[t]=f},setAttribute(k,v){this.attrs[k]=v},removeAttribute(){},load(){},pause(){}}]));
 const calls=[]; const document={querySelector(s){if(s==='meta[name="jarvis-token"]')return{content:'tk'};return els[s.slice(1)]||null;}};
 const window={addEventListener(){}};
 const context={document,window,navigator:{},URL:{createObjectURL:()=>'blob:mock',revokeObjectURL(){}},AbortController,Blob,console,setTimeout,clearTimeout,crypto:{randomUUID:(()=>{let n=0;return()=>`uuid-${++n}`})()},fetch:async(url,opts={})=>{calls.push({url,opts});return fetcher?fetcher(url,opts,calls):url==='/api/status'?{ok:true,json:async()=>payload}:{ok:true,json:async()=>({reply:'**Hi** TOKEN_1 10.0.0.1',session_id:'s',request_id:JSON.parse(opts.body).request_id}),blob:async()=>new Blob(['voice'])};}};
 context.Audio=class AudioMock{constructor(url){this.url=String(url);this.listeners={};}addEventListener(t,f){this.listeners[t]=f;}
  play(){return context.__audioBehavior==='block'?Promise.reject(Object.assign(new Error('blocked'),{name:'NotAllowedError'})):Promise.resolve();}
  pause(){}removeAttribute(){}load(){}};
 vm.createContext(context); vm.runInContext(fs.readFileSync('ui/voice-core.js','utf8'),context,{filename:'voice-core.js'}); vm.runInContext(fs.readFileSync('ui/app.js','utf8'),context,{filename:'app.js'}); return{els,calls,context};
}
const tick=()=>new Promise(r=>setImmediate(r));
test('chat requires exact explicit confirmation and posts owned request IDs',async()=>{const{els,calls}=mount();await tick();els['chat-input'].value='  exact edited text  ';els['chat-send'].listeners.click();await tick();assert.equal(calls.filter(c=>c.url==='/api/run').length,0);els['confirm-send'].listeners.click();await tick();const req=calls.find(c=>c.url==='/api/run');assert.equal(JSON.parse(req.opts.body).message,'  exact edited text  ');assert.equal(JSON.parse(req.opts.body).confirmed,true);assert.match(JSON.parse(req.opts.body).client_id,/^uuid-/);assert.match(JSON.parse(req.opts.body).request_id,/^uuid-/);});
test('no auto-send, Markdown safely renders and explicit read uses speak',async()=>{let speakBody;const{els,calls}=mount(undefined,async(url,opts)=>{if(url==='/api/status')return{ok:true,json:async()=>({chat_enabled:true,tools_enabled:false,agent_enabled:true,scope:'chat-only',tts:'edge',server_tts:true})};if(url==='/api/run')return{ok:true,json:async()=>({reply:'**hello** <img src=x onerror=alert(1)> 192.168.2.4',request_id:JSON.parse(opts.body).request_id})};if(url==='/api/speak'){speakBody=JSON.parse(opts.body);return{ok:true,blob:async()=>new Blob(['audio'])}};return{ok:true,json:async()=>({})}});await tick();await tick();await tick();assert.equal(calls.some(c=>c.url==='/api/run'),false);els['chat-input'].value='question';els['confirm-send'].listeners.click();await tick();await tick();assert.match(els.reply.innerHTML,/<strong>hello<\/strong>/);assert.doesNotMatch(els.reply.innerHTML,/<img/);assert.match(els.reply.innerHTML,/192\.168\.2\.4/);assert.equal(speakBody,undefined);await els['read-reply'].listeners.click();assert.ok(speakBody.text.includes('192.168.2.4'));});
test('chat disabled without explicit status proof',async()=>{const{els}=mount({agent_enabled:true,tools_enabled:false});await tick();assert.equal(els['confirm-send'].disabled,true);});
test('new/cancel invalidates late reply and cancels own run',async()=>{let finish;const{els,calls}=mount(undefined,(url,opts)=>{if(url==='/api/status')return Promise.resolve({ok:true,json:async()=>({chat_enabled:true,tools_enabled:false,agent_enabled:true,scope:'chat-only'})});if(url==='/api/run')return new Promise(r=>finish=()=>r({ok:true,json:async()=>({reply:'STALE',request_id:JSON.parse(opts.body).request_id})}));return Promise.resolve({ok:true,json:async()=>({})})});await tick();els['chat-input'].value='q';els['confirm-send'].listeners.click();await tick();els['new-chat'].listeners.click();await tick();assert.ok(calls.some(c=>c.url==='/api/cancel'));finish?.();await tick();assert.doesNotMatch(els.reply.innerHTML,/STALE/);});
test('provider failure body replaces the opaque HTTP 503 label',async()=>{
 const{els}=mount(undefined,async(url)=>{
  if(url==='/api/status')return{ok:true,json:async()=>({chat_enabled:true,tools_enabled:false,agent_enabled:true,scope:'chat-only'})};
  if(url==='/api/run')return{ok:false,status:503,json:async()=>({error:'โหมดโมเดลติดโควตา (HTTP 429) — ยังสนทนาต่อไม่ได้ชั่วคราว'})};
  return{ok:true,json:async()=>({})};
 });
 await tick();
 els['chat-input'].value='q';
 els['confirm-send'].listeners.click();
 await tick();await tick();
 assert.match(els['chat-status'].textContent,/สนทนาไม่สำเร็จ: โหมดโมเดลติดโควตา/);
 assert.equal(els['chat-status'].dataset.kind,'error');
 assert.doesNotMatch(els['chat-status'].textContent,/HTTP 503/);
});
test('blocked autoplay explains itself and the next press replays cached audio without refetching',async()=>{
 let speaks=0;
 const{els,context}=mount(undefined,async(url,opts)=>{
  if(url==='/api/status')return{ok:true,json:async()=>({chat_enabled:true,tools_enabled:false,agent_enabled:true,scope:'chat-only',tts:'edge',server_tts:true})};
  if(url==='/api/run')return{ok:true,json:async()=>({reply:'ตอบ 192.168.1.10',request_id:JSON.parse(opts.body).request_id})};
  if(url==='/api/speak'){speaks++;return{ok:true,blob:async()=>new Blob(['audio'])};}
  return{ok:true,json:async()=>({})};
 });
 await tick();
 assert.equal(els['read-reply'].disabled,true);
 els['chat-input'].value='q';
 els['confirm-send'].listeners.click();
 await tick();await tick();
 assert.equal(els['read-reply'].disabled,false);
 context.__audioBehavior='block';
 await els['read-reply'].listeners.click();await tick();await tick();
 assert.equal(speaks,1);
 assert.match(els['voice-status'].textContent,/บล็อกการเล่น/);
 context.__audioBehavior='ok';
 await els['read-reply'].listeners.click();await tick();await tick();
 assert.equal(speaks,1);
 assert.match(els['voice-status'].textContent,/กำลังอ่านออกเสียง/);
});
test('findDigitTokens lists only digit-bearing tokens (IP/CIDR/account/counts)',()=>{
 const{findDigitTokens}=require('../../ui/voice-core.js');
 assert.deepEqual(findDigitTokens('ping 192.168.10.5 ใช้เวลา 24 ms'),['192.168.10.5','24']);
 assert.deepEqual(findDigitTokens('CIDR /26 และบัญชี admin01.'),['/26','admin01']);
 assert.deepEqual(findDigitTokens('192.168.1.10 ซ้ำ 192.168.1.10'),['192.168.1.10']);
 assert.deepEqual(findDigitTokens('ไม่มีตัวเลขในประโยคนี้เลย'),[]);
});
test('digit/IP tokens are surfaced for review and the confirm button counts them',async()=>{
 const{els}=mount(); await tick();
 assert.equal(els['chat-digits'].hidden,true);
 els['chat-input'].value='ping 192.168.10.5 ใช้ 24 มิลลิวินาที และ /26 ของ admin01';
 els['chat-input'].listeners.input(); await tick();
 assert.equal(els['chat-digits'].hidden,false);
 for(const token of ['192.168.10.5','24','/26','admin01']) assert.ok(els['chat-digits'].innerHTML.includes(`<b>${token}</b>`),token);
 assert.match(els['confirm-send'].textContent,/ตรวจตัวเลข 4 รายการ/);
 els['transcript'].value='รหัสผ่าน 12 ตัว'; els['transcript'].listeners.input(); await tick();
 assert.equal(els['transcript-digits'].hidden,false);
 assert.ok(els['transcript-digits'].innerHTML.includes('<b>12</b>'));
 els['chat-input'].value='สวัสดีครับ'; els['chat-input'].listeners.input(); await tick();
 assert.equal(els['chat-digits'].hidden,true);
 assert.equal(els['confirm-send'].textContent,'ยืนยันส่งข้อความให้ Hermes');
});
test('digit warning escapes HTML inside tokens before rendering',async()=>{
 const{els}=mount(); await tick();
 els['chat-input'].value='a&b1 และ 10.0.0.1';
 els['chat-input'].listeners.input(); await tick();
 assert.ok(els['chat-digits'].innerHTML.includes('a&amp;b1'));
 assert.doesNotMatch(els['chat-digits'].innerHTML,/a&b1/);
 assert.ok(els['chat-digits'].innerHTML.includes('<b>10.0.0.1</b>'));
});
const READ_ONLY_STATUS={chat_enabled:true,tools_enabled:false,agent_enabled:true,scope:'chat-only',tts:'edge',server_tts:true,tools_read_only:true};
const APPROVAL={action:'read_text_file',args:{path:'/tmp/allowed/x.md'},request_id:'r1',digest:'d'.repeat(64),expires_at:Math.floor(Date.now()/1000)+90};
test('approval request is shown, approved once with its digest, then the reply lands',async()=>{
 let approveBody;
 const{els,calls}=mount(undefined,async(url,opts)=>{
  if(url==='/api/status')return{ok:true,json:async()=>READ_ONLY_STATUS};
  if(url==='/api/run')return{ok:true,json:async()=>({needs_approval:true,approval:APPROVAL,request_id:JSON.parse(opts.body).request_id})};
  if(url==='/api/approve'){approveBody=JSON.parse(opts.body);return{ok:true,json:async()=>({reply:'อ่านแล้ว — พอร์ต 8730',request_id:'r1',action_executed:'read_text_file'})};}
  return{ok:true,json:async()=>({})};
 });
 await tick();
 assert.equal(els['approval-panel'].hidden,true);
 els['chat-input'].value='อ่านไฟล์ x.md ให้หน่อย'; els['confirm-send'].listeners.click();
 await tick(); await tick();
 assert.equal(els['approval-panel'].hidden,false);
 assert.match(els['approval-detail'].textContent,/read_text_file/);
 assert.match(els['approval-detail'].textContent,/x\.md/);
 assert.equal(els['approve-yes'].disabled,false);
 assert.equal(els['reply'].innerHTML,'');
 await els['approve-yes'].listeners.click(); await tick(); await tick();
 assert.equal(approveBody.digest,'d'.repeat(64));
 assert.equal(approveBody.request_id,'r1');
 assert.match(els.reply.innerHTML,/8730/);
 assert.equal(els['approval-panel'].hidden,true);
 assert.equal(calls.filter(c=>c.url==='/api/approve').length,1);
});
test('deny hides the panel and never calls approve',async()=>{
 const{els,calls}=mount(undefined,async(url)=>{
  if(url==='/api/status')return{ok:true,json:async()=>READ_ONLY_STATUS};
  if(url==='/api/run')return{ok:true,json:async()=>({needs_approval:true,approval:APPROVAL,request_id:'r1'})};
  if(url==='/api/deny')return{ok:true,json:async()=>({ok:true,denied:true,executed:false})};
  return{ok:true,json:async()=>({})};
 });
 await tick();
 els['chat-input'].value='อ่านไฟล์'; els['confirm-send'].listeners.click();
 await tick(); await tick();
 assert.equal(els['approval-panel'].hidden,false);
 await els['approve-no'].listeners.click(); await tick();
 assert.equal(calls.filter(c=>c.url==='/api/deny').length,1);
 assert.equal(calls.filter(c=>c.url==='/api/approve').length,0);
 assert.equal(els['approval-panel'].hidden,true);
 assert.match(els['chat-status'].textContent,/ปฏิเสธการอ่านไฟล์แล้ว/);
});

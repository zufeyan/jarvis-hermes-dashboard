'use strict';
const {createCancelableJob} = window.JarvisVoiceCore;
const $ = selector => document.querySelector(selector);
const API_TOKEN = $('meta[name="jarvis-token"]')?.content || '';
const headers = extra => ({'x-jarvis-token': API_TOKEN, ...(extra || {})});
const makeId = () => (globalThis.crypto && globalThis.crypto.randomUUID ? globalThis.crypto.randomUUID() : `client_${Date.now().toString(36)}_${Math.random().toString(36).slice(2)}`);
const state = {stt:false,tts:false,requestingMic:false,micEpoch:0,recorder:null,stream:null,listenJob:null,speakJob:null,audio:null,audioUrl:null,pendingSpeech:null,recordingTimer:null,recording:false,chatEnabled:false,runJob:null,runId:null,runEpoch:0,reply:'',clientId:makeId(),approval:null};

function status(message,kind='info') { const node=$('#voice-status');node.textContent=message;node.dataset.kind=kind; }
function chatStatus(message,kind='info') { const node=$('#chat-status');if(node){node.textContent=message;node.dataset.kind=kind;} }
function escapeToken(value){return String(value).replace(/[&<>"']/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));}
function updateDigitWarnings(){
  [['#transcript','#transcript-digits'],['#chat-input','#chat-digits']].forEach(([source,target])=>{
    const input=$(source),box=$(target);if(!input||!box)return;
    const tokens=window.JarvisVoiceCore.findDigitTokens(input.value);
    if(!tokens.length){box.hidden=true;box.innerHTML='';}
    // Tokens come from user text but are HTML-escaped above before insertion.
    else{box.hidden=false;box.innerHTML='<span class="digit-warn-label">ตรวจตัวเลข/IP/CIDR ก่อนส่ง:</span> '+tokens.map(t=>`<b>${escapeToken(t)}</b>`).join(' · ');}
  });
}
function setChatControls(){ if(!$('#confirm-send'))return;const tokens=window.JarvisVoiceCore.findDigitTokens($('#chat-input').value);$('#confirm-send').textContent=tokens.length?`ยืนยันส่งข้อความให้ Hermes · ตรวจตัวเลข ${tokens.length} รายการ`:'ยืนยันส่งข้อความให้ Hermes';$('#confirm-send').disabled=!state.chatEnabled||!!state.runJob||!$('#chat-input').value.trim();$('#cancel-run').disabled=!state.runJob;$('#read-reply').disabled=!state.reply||!!state.runJob;if($('#approve-yes')){$('#approve-yes').disabled=!state.approval||!!state.runJob;$('#approve-no').disabled=!state.approval||!!state.runJob;}updateDigitWarnings(); }
function showApproval(approval){state.approval=approval;const panel=$('#approval-panel'),detail=$('#approval-detail');if(panel&&detail){panel.hidden=false;detail.textContent=`การกระทำ: ${approval.action}\narguments: ${JSON.stringify(approval.args,null,2)}\nrequest_id: ${approval.request_id}\nหมดอายุ: ${new Date(approval.expires_at*1000).toLocaleTimeString()}`;}chatStatus('มีคำขออ่านไฟล์รอการอนุมัติ — ตรวจรายการด้านบนก่อนกดยืนยัน','busy');setChatControls();}
function hideApproval(){state.approval=null;const panel=$('#approval-panel');if(panel){panel.hidden=true;const d=$('#approval-detail');if(d)d.textContent='';}setChatControls();}
function cancelRun(){const job=state.runJob,id=state.runId;if(!job)return;state.runEpoch++;state.runJob=null;job.cancel();chatStatus('ยกเลิกคำขอในเบราว์เซอร์แล้ว — หยุดงานฝั่งเซิร์ฟเวอร์แล้วหรือไม่ยังยืนยันไม่ได้');fetch('/api/cancel',{method:'POST',headers:headers({'content-type':'application/json'}),body:JSON.stringify({client_id:state.clientId,request_id:id})}).then(r=>{if(!r.ok)chatStatus('ส่งคำขอยกเลิกไม่สำเร็จ — สถานะงานฝั่งเซิร์ฟเวอร์ยังไม่ทราบ','error');}).catch(()=>chatStatus('ส่งคำขอยกเลิกไม่สำเร็จ — สถานะงานฝั่งเซิร์ฟเวอร์ยังไม่ทราบ','error'));setChatControls();}
async function submitChat(){if(!state.chatEnabled||state.runJob)return;const message=$('#chat-input').value;if(!message.trim())return;const requestId=makeId(),epoch=++state.runEpoch;const job=createCancelableJob(signal=>fetch('/api/run',{method:'POST',headers:headers({'content-type':'application/json'}),body:JSON.stringify({message,confirmed:true,client_id:state.clientId,request_id:requestId}),signal}));state.runJob=job;state.runId=requestId;chatStatus('กำลังสนทนากับ Hermes…','busy');setChatControls();try{const response=await job.promise;if(!job.isCurrent()||epoch!==state.runEpoch||state.runId!==requestId)return;if(!response)throw job.error()||new Error('ไม่ได้รับคำตอบ');if(!response.ok){let body=null;try{body=await response.json();}catch{}const detail=body&&typeof body.error==='string'?body.error.slice(0,400):'';throw new Error(detail||`HTTP ${response.status}`);}const result=await response.json();if(!job.isCurrent()||epoch!==state.runEpoch)return;if(result.needs_approval===true&&result.approval){showApproval(result.approval);return;}if(result.request_id!==requestId)throw new Error('รหัสคำขอไม่ตรงกัน — ไม่แสดงคำตอบที่ไม่ใช่ของคำขอนี้');state.reply=String(result.reply||'');$('#reply').innerHTML=window.JarvisVoiceCore.markdownToSafeHtml(state.reply);chatStatus('คำตอบพร้อม — ไม่มีการอ่านออกเสียงอัตโนมัติ','done');}catch(error){if(job.isCurrent())chatStatus(`สนทนาไม่สำเร็จ: ${error.message}`,'error');}finally{if(state.runJob===job)state.runJob=null;setChatControls();}}
async function approveAction(){const pending=state.approval;if(!pending)return;const job=createCancelableJob(signal=>fetch('/api/approve',{method:'POST',headers:headers({'content-type':'application/json'}),body:JSON.stringify({client_id:state.clientId,request_id:pending.request_id,digest:pending.digest}),signal}));state.runJob=job;chatStatus('กำลังอ่านไฟล์ตามที่อนุมัติ…','busy');setChatControls();try{const response=await job.promise;if(!job.isCurrent())return;if(!response)throw job.error()||new Error('ไม่ได้รับคำตอบ');if(!response.ok){let body=null;try{body=await response.json();}catch{}const detail=body&&typeof body.error==='string'?body.error.slice(0,400):'';throw new Error(detail||`HTTP ${response.status}`);}const result=await response.json();if(!job.isCurrent())return;if(result.needs_approval===true&&result.approval){showApproval(result.approval);return;}state.reply=String(result.reply||'');$('#reply').innerHTML=window.JarvisVoiceCore.markdownToSafeHtml(state.reply);hideApproval();chatStatus('อ่านไฟล์ตามที่อนุมัติแล้วและตอบกลับมา','done');}catch(error){if(job.isCurrent())chatStatus(`อนุมัติไม่สำเร็จ: ${error.message}`,'error');}finally{if(state.runJob===job)state.runJob=null;setChatControls();}}
async function denyAction(){const pending=state.approval;if(!pending)return;try{const r=await fetch('/api/deny',{method:'POST',headers:headers({'content-type':'application/json'}),body:JSON.stringify({client_id:state.clientId,request_id:pending.request_id})});if(!r.ok)throw new Error(`HTTP ${r.status}`);hideApproval();chatStatus('ปฏิเสธการอ่านไฟล์แล้ว — ไม่มีการอ่านไฟล์ใด ๆ','done');}catch(error){chatStatus(`ปฏิเสธไม่สำเร็จ: ${error.message}`,'error');}}
async function newChat(){const job=state.runJob;if(job){const id=state.runId;state.runEpoch++;state.runJob=null;job.cancel();fetch('/api/cancel',{method:'POST',headers:headers({'content-type':'application/json'}),body:JSON.stringify({client_id:state.clientId,request_id:id})}).catch(()=>{});}const epoch=++state.runEpoch;cancelSpeak(false);hideApproval();if(state.pendingSpeech){if(state.pendingSpeech.url)URL.revokeObjectURL(state.pendingSpeech.url);state.pendingSpeech=null;}state.reply='';$('#reply').innerHTML='';$('#chat-input').value='';chatStatus('กำลังเริ่มบทสนทนาใหม่…','busy');try{const r=await fetch('/api/new',{method:'POST',headers:headers({'content-type':'application/json'}),body:JSON.stringify({client_id:state.clientId})});if(!r.ok)throw new Error(`HTTP ${r.status}`);if(epoch===state.runEpoch)chatStatus('บทสนทนาใหม่พร้อม','done');}catch(error){if(epoch===state.runEpoch)chatStatus(`เริ่มบทสนทนาใหม่ไม่สำเร็จ: ${error.message}`,'error');}setChatControls();}
async function readReply(){
  if(!state.reply){chatStatus('ยังไม่มีคำตอบให้อ่าน','error');return;}
  if(state.recording||state.requestingMic||state.listenJob){chatStatus('กำลังบันทึกหรือถอดเสียงอยู่ — รอให้เสร็จก่อนอ่านคำตอบ','error');return;}
  if(!state.tts){chatStatus('ระบบอ่านเสียงยังไม่พร้อม (Edge TTS) — ข้อความคำตอบยังแสดงบนหน้าจอ','error');return;}
  unlockAudio();  // synchronous inside the real click, so Safari/Chrome allow playback
  const text=window.JarvisVoiceCore.cleanForSpeech(state.reply);
  if(!text){chatStatus('ไม่มีข้อความที่อ่านได้ในคำตอบ','error');return;}
  if(text.length>2000){chatStatus('คำตอบยาวเกิน 2,000 ตัวอักษรสำหรับการอ่าน — ข้อความฉบับเต็มยังแสดงอยู่','error');return;}
  await speakText(text,true);
}
function setVoiceControls() {
  $('#record').textContent=state.recording?'■ หยุดบันทึก':state.requestingMic?'กำลังขอสิทธิ์ไมค์…':'● กดเพื่อพูด';
  $('#record').setAttribute('aria-label',state.recording?'หยุดบันทึกและถอดเสียง':'เริ่มบันทึกเสียง');
  $('#record').setAttribute('aria-pressed',String(state.recording));
  $('#record').disabled=!state.stt||state.requestingMic||!!state.listenJob||(!state.recording&&!navigator.mediaDevices?.getUserMedia);
  $('#stop-audio').disabled=!state.audio&&!state.speakJob;
  $('#speak').disabled=!state.tts||state.recording||state.requestingMic||!!state.listenJob||!$('#transcript').value.trim();
  updateDigitWarnings();
}
function cancelSpeak(announce=true) {
  const job=state.speakJob;state.speakJob=null;if(job)job.cancel();
  const cached=state.pendingSpeech;
  if(state.audio){
    const keep=Boolean(cached&&state.audio===cached.audio);
    state.audio.pause();
    if(!keep){state.audio.removeAttribute('src');state.audio.load();}
    state.audio=null;
  }
  if(state.audioUrl){
    if(!cached||state.audioUrl!==cached.url)URL.revokeObjectURL(state.audioUrl);
    state.audioUrl=null;
  }
  if(announce)status('หยุดเสียงอ่านแล้ว — การสร้างเสียงบนเซิร์ฟเวอร์อาจยังทำงานจนจบหรือหมดเวลา');
  setVoiceControls();
}
async function cancelListen() {
  state.micEpoch++;state.requestingMic=false;
  if(state.listenJob){state.listenJob.cancel();state.listenJob=null;}
  if(state.recording)await stopRecording({transcribe:false});
  status('หยุดไมค์และยกเลิกการรอผลแล้ว — งาน STT ฝั่งเซิร์ฟเวอร์อาจยังทำงานจนหมดเวลา');
  setVoiceControls();
}
async function stopRecording({transcribe=true}={}) {
  if(!state.recording)return;
  const epoch=state.micEpoch;
  state.recording=false;clearTimeout(state.recordingTimer);state.recordingTimer=null;
  const recorder=state.recorder,stream=state.stream;state.recorder=null;state.stream=null;
  const stopped=new Promise(resolve=>{if(!recorder||recorder.state==='inactive')resolve();else{recorder.addEventListener('stop',resolve,{once:true});recorder.stop();}});
  if(stream)stream.getTracks().forEach(track=>track.stop());
  await stopped;setVoiceControls();
  if(!transcribe||epoch!==state.micEpoch)return;
  if(!recorder?._chunks?.length){status('ไม่พบเสียงบันทึก');return;}
  const audio=new Blob(recorder._chunks,{type:recorder.mimeType||'audio/webm'});
  const job=createCancelableJob(signal=>fetch('/api/listen',{method:'POST',headers:headers({'content-type':audio.type||'audio/webm'}),body:audio,signal}));
  state.listenJob=job;status('กำลังถอดเสียงในเครื่อง…','busy');setVoiceControls();
  try {
    const response=await job.promise;
    if(!job.isCurrent()||epoch!==state.micEpoch)return;
    if(!response)throw job.error()||new Error('ไม่ได้รับคำตอบ');
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const result=await response.json();
    if(!job.isCurrent()||epoch!==state.micEpoch)return;
    $('#transcript').value=String(result.text||'');
    status(result.text?'ข้อความถอดเสียงพร้อมตรวจทาน — ไม่มีการส่งคำสั่งอัตโนมัติ':'ไม่พบคำพูด','done');
  }catch(error){if(job.isCurrent())status(`ถอดเสียงไม่สำเร็จ: ${error.message}`,'error');}
  finally{if(state.listenJob===job)state.listenJob=null;setVoiceControls();}
}
async function toggleRecording() {
  if(state.recording)return stopRecording();
  if(state.requestingMic||state.listenJob)return;
  if(!state.stt)return status('ระบบถอดเสียงยังไม่พร้อม','error');
  cancelSpeak(false);
  state.requestingMic=true;const epoch=++state.micEpoch;setVoiceControls();
  status('กำลังขอสิทธิ์ไมโครโฟน…','busy');let stream;
  try {
    stream=await navigator.mediaDevices.getUserMedia({audio:{echoCancellation:true,noiseSuppression:true}});
    if(epoch!==state.micEpoch||!state.requestingMic){stream.getTracks().forEach(track=>track.stop());return;}
    const recorder=new MediaRecorder(stream);recorder._chunks=[];
    recorder.addEventListener('dataavailable',event=>{if(event.data?.size)recorder._chunks.push(event.data);});
    recorder.addEventListener('error',async()=>{await cancelListen();status('บันทึกเสียงไม่สำเร็จ','error');});
    recorder.start();state.stream=stream;state.recorder=recorder;state.recording=true;
    $('#transcript').value='';status('กำลังบันทึก — สูงสุด 30 วินาที','busy');
    state.recordingTimer=setTimeout(()=>stopRecording(),29000);
  }catch(error){stream?.getTracks().forEach(track=>track.stop());if(epoch===state.micEpoch)status(`เปิดไมโครโฟนไม่ได้: ${error.message}`,'error');}
  finally{if(epoch===state.micEpoch)state.requestingMic=false;setVoiceControls();}
}
async function speakSelected() {
  const text=$('#transcript').value.trim();
  if(!text)return;
  if(state.recording||state.requestingMic||state.listenJob){status('กำลังบันทึกหรือถอดเสียงอยู่ — รอให้เสร็จก่อน','error');return;}
  if(!state.tts){status('ระบบอ่านเสียงยังไม่พร้อม (Edge TTS)','error');return;}
  unlockAudio();
  await speakText(text);
}
function unlockAudio() {
  // Must run synchronously inside a real click: some browsers only unlock
  // media playback from a user-gesture task, not from an awaited fetch later.
  // Build a tiny silent WAV in memory so there is nothing to fetch.
  try{
    const n=800,head=new Uint8Array(44),dv=new DataView(head.buffer);
    const put=(o,s)=>{for(let i=0;i<s.length;i++)head[o+i]=s.charCodeAt(i);};
    put(0,'RIFF');dv.setUint32(4,36+n*2,true);put(8,'WAVE');put(12,'fmt ');
    dv.setUint32(16,16,true);dv.setUint16(20,1,true);dv.setUint16(22,1,true);
    dv.setUint32(24,8000,true);dv.setUint32(28,16000,true);dv.setUint16(32,2,true);dv.setUint16(34,16,true);
    put(36,'data');dv.setUint32(40,n*2,true);
    const wav=new Uint8Array(44+n*2);wav.set(head);
    const url=URL.createObjectURL(new Blob([wav],{type:'audio/wav'}));
    const primer=new Audio(url);primer.muted=true;primer.volume=0;
    const p=primer.play();if(p&&p.catch)p.catch(()=>{});
    setTimeout(()=>URL.revokeObjectURL(url),5000);
  }catch{}
}
function bindAudioEvents(audio){
  audio.addEventListener('ended',()=>{if(state.audio===audio){state.audio=null;status('อ่านเสียงเสร็จแล้ว','done');setVoiceControls();}},{once:true});
  audio.addEventListener('error',()=>{if(state.audio===audio){state.audio=null;status('เล่นเสียงไม่สำเร็จ','error');setVoiceControls();}},{once:true});
}
async function speakText(text,reuse=false) {
  if(!text)return;
  if(!state.tts){status('ระบบอ่านเสียงยังไม่พร้อม (Edge TTS)','error');return;}
  if(state.recording||state.requestingMic||state.listenJob){status('กำลังบันทึกหรือถอดเสียงอยู่ — รอให้เสร็จก่อน','error');return;}
  cancelSpeak(false);
  const cached=state.pendingSpeech;
  if(reuse&&cached&&cached.text===text&&cached.audio){
    state.audio=cached.audio;
    try{
      const p=state.audio.play();if(p&&p.then)await p;
      status('กำลังอ่านออกเสียง','done');setVoiceControls();return;
    }catch(error){
      state.audio=null;
      status(`เบราว์เซอร์บล็อกการเล่นเสียง — กด “อ่านคำตอบ” อีกครั้งเพื่อเล่นเสียงที่เตรียมไว้แล้ว (${error&&error.name?error.name:'เล่นไม่ได้'})`,'error');
      setVoiceControls();return;
    }
  }
  const job=createCancelableJob(signal=>fetch('/api/speak',{method:'POST',headers:headers({'content-type':'application/json'}),body:JSON.stringify({text}),signal}));
  state.speakJob=job;status('กำลังสร้างเสียงอ่านผ่าน Edge ออนไลน์…','busy');setVoiceControls();
  try{
    const response=await job.promise;
    if(!job.isCurrent())return;
    if(!response)throw job.error()||new Error('ไม่ได้รับคำตอบ');
    if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const blob=await response.blob();if(!job.isCurrent())return;
    if(state.pendingSpeech&&state.pendingSpeech.url)URL.revokeObjectURL(state.pendingSpeech.url);
    state.audioUrl=URL.createObjectURL(blob);
    state.audio=new Audio(state.audioUrl);
    state.pendingSpeech={text,url:state.audioUrl,audio:state.audio};
    bindAudioEvents(state.audio);
    const p=state.audio.play();if(p&&p.then)await p;
    if(job.isCurrent())status('กำลังอ่านออกเสียง','done');
  }catch(error){
    if(job.isCurrent()){
      const blocked=Boolean(error&&error.name==='NotAllowedError');
      if(blocked){
        state.speakJob=null;
        status('เบราว์เซอร์บล็อกการเล่นอัตโนมัติ — กด “อ่านคำตอบ” อีกครั้งเพื่อเล่นเสียงที่เตรียมไว้แล้ว','error');
      }else{
        cancelSpeak(false);
        status(`เสียงอ่านไม่สำเร็จ: ${error&&error.message?error.message:error}`,'error');
      }
    }
  }
  finally{setVoiceControls();}
}
async function boot(){
  $('#record')?.addEventListener('click',toggleRecording);
  $('#stop-audio')?.addEventListener('click',()=>cancelSpeak());
  $('#cancel-listen')?.addEventListener('click',cancelListen);
  $('#speak')?.addEventListener('click',speakSelected);
  $('#confirm-send')?.addEventListener('click',submitChat);$('#chat-send')?.addEventListener('click',()=>{});$('#chat-input')?.addEventListener('input',setChatControls);$('#cancel-run')?.addEventListener('click',cancelRun);$('#new-chat')?.addEventListener('click',newChat);$('#read-reply')?.addEventListener('click',readReply);$('#approve-yes')?.addEventListener('click',approveAction);$('#approve-no')?.addEventListener('click',denyAction);
  $('#use-transcript')?.addEventListener('click',()=>{const transcript=$('#transcript').value.trim();if(!transcript)return;$('#chat-input').value=transcript;setChatControls();chatStatus('นำข้อความถอดเสียงมาใส่ในช่องแชตแล้ว — ตรวจทานและกดยืนยันส่งเอง','ready');});
  $('#transcript')?.addEventListener('input',setVoiceControls);
  if($('#run')){$('#run').disabled=true;$('#run').setAttribute('aria-describedby','agent-notice');}
  status('กำลังตรวจสอบสถานะระบบเสียง…','busy');chatStatus('กำลังตรวจสอบสถานะ…','busy');
  try{
    const response=await fetch('/api/status',{headers:headers()});if(!response.ok)throw new Error(`HTTP ${response.status}`);
    const server=await response.json();
    state.stt=server.stt==='local'&&server.server_stt===true;
    state.tts=server.tts==='edge'&&server.server_tts===true;
    state.chatEnabled=server.chat_enabled===true&&server.tools_enabled===false&&server.agent_enabled===true&&server.scope==='chat-only';
    if($('#privacy'))$('#privacy').textContent='เสียงไมค์ใช้ local STT ในเครื่อง · ข้อความที่ยืนยันส่งพร้อมประวัติแชตส่งไปผู้ให้บริการโมเดลที่ตั้งไว้ · ข้อความที่กดอ่านส่งไป Edge TTS ออนไลน์ ไม่ควรส่งข้อมูลลับ';
    if($('#tts-state'))$('#tts-state').textContent=state.tts?'Edge TTS · ต้องใช้อินเทอร์เน็ต':'Edge TTS · ยังไม่พร้อม';
    const route=[server.chat_provider,server.chat_model].filter(Boolean).join(' / ');
    const readyNotice=route?`สนทนากับ Hermes ได้ · โมเดล ${route} — ยังปิดเครื่องมือและการแก้ไฟล์/ระบบ`:'สนทนากับ Hermes ได้ — ยังปิดเครื่องมือและการแก้ไฟล์/ระบบ';
    const toolNote=server.tools_read_only===true?' · เครื่องมือ read-only สองตัวใช้ได้ผ่านการยืนยันรายครั้ง':'';
    if($('#agent-notice'))$('#agent-notice').textContent=state.chatEnabled?readyNotice+toolNote:'ยังไม่เปิดการสั่งงานผ่าน Dashboard — ใช้ Hermes Desktop สำหรับงานจริง';
    if($('#confirm-send'))$('#confirm-send').disabled=!state.chatEnabled;chatStatus(state.chatEnabled?readyNotice:'แชตยังไม่พร้อมหรือไม่ได้ยืนยันว่าเป็นโหมด chat-only',state.chatEnabled?'ready':'error');
    status(state.stt?'พร้อมบันทึกเสียง · ไม่มีการส่งคำสั่งอัตโนมัติ':'STT ยังไม่พร้อม','ready');
  }catch(error){state.stt=false;state.tts=false;status(`เชื่อมต่อสถานะไม่ได้: ${error.message}`,'error');}
  if ($('#record')) setVoiceControls();
  setChatControls();
  hideApproval();
}
window.addEventListener('beforeunload',()=>{cancelSpeak(false);cancelListen();cancelRun();});
boot();

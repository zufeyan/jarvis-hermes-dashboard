const {test}=require('node:test');
const assert=require('node:assert/strict');
const vm=require('node:vm');
const fs=require('node:fs');
const {createCancelableJob}=require('../../ui/voice-core.js');

test('core script publishes browser API without Node injection',()=>{
 const context={window:{},AbortController};
 vm.runInNewContext(fs.readFileSync('ui/voice-core.js','utf8'),context);
 assert.equal(typeof context.window.JarvisVoiceCore?.createCancelableJob,'function');
});

test('cancel invalidates ownership even after fetch headers resolved',async()=>{
 const job=createCancelableJob(()=>Promise.resolve('response headers'));
 assert.equal(await job.promise,'response headers');
 job.cancel();
 assert.equal(job.isCurrent(),false);
 assert.equal(job.signal.aborted,true);
});

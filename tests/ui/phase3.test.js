const {test}=require('node:test');
const assert=require('node:assert/strict');
const {cleanForSpeech,markdownToSafeHtml,createCancelableJob,makeConfirmedPayload}=require('../../ui/voice-core.js');

test('speech cleaner excludes code, tables, and log metadata while preserving addresses and identifiers',()=>{
  assert.equal(cleanForSpeech('Use host 192.168.1.10 and ID AB-123.\n```\ncurl evil\n```\n|col|data|\nLog `TOKEN_42` safely.\nsession_id: abc'),'Use host 192.168.1.10 and ID AB-123. Log TOKEN_42 safely.');
});
test('markdown is safely rendered with headings, emphasis, and fenced code',()=>{
  const html=markdownToSafeHtml('# Title\n**bold** <script>alert(1)</script>\n```js\nalert(1)\n```');
  assert.match(html,/<h1>Title<\/h1>/); assert.match(html,/<strong>bold<\/strong>/);
  assert.doesNotMatch(html,/<script>/); assert.match(html,/&lt;script&gt;/);
  assert.match(html,/<pre><code>alert\(1\)\n<\/code><\/pre>/);
});
test('only explicit confirmation creates a send payload',()=>{
  assert.equal(makeConfirmedPayload('hello',false),null);
  assert.deepEqual(makeConfirmedPayload(' hello ',true),{message:'hello',confirmed:true});
});
test('cancelable job aborts and settles once; stale completion is discarded',async()=>{
  let resolve; const job=createCancelableJob(()=>new Promise(r=>resolve=r));
  const pending=job.promise; job.cancel(); resolve('late');
  assert.equal(await pending,null); assert.equal(job.isCurrent(),false);
});

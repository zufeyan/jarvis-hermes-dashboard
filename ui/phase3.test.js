const {test}=require('node:test');
const assert=require('node:assert/strict');
const {cleanForSpeech,markdownToSafeHtml}=require('./voice-core.js');
test('speech cleaner excludes fenced code, inline code, and table rows while preserving IPs and identifiers',()=>{
  const out=cleanForSpeech('Use host 192.168.1.10 and ID AB-123.\n```\ncurl evil\n```\n|col|data|\nLog `TOKEN_42` safely.');
  assert.equal(out,'Use host 192.168.1.10 and ID AB-123. Log TOKEN_42 safely.');
});
test('markdown renderer escapes raw HTML and supports safe headings, emphasis and fenced code',()=>{
  const out=markdownToSafeHtml('# Title\n**bold** <script>alert(1)</script>\n```js\nalert(1)\n```');
  assert.match(out,/<h1>Title<\/h1>/); assert.match(out,/<strong>bold<\/strong>/);
  assert.doesNotMatch(out,/<script>/); assert.match(out,/&lt;script&gt;/);
  assert.match(out,/<pre><code>alert\(1\)\n<\/code><\/pre>/);
});
test('speech prep keeps network addresses and explicit identifiers untouched',()=>{
  assert.equal(cleanForSpeech('Connect to 10.0.0.8; interface eth0; ID X-09.'),'Connect to 10.0.0.8; interface eth0; ID X-09.');
});

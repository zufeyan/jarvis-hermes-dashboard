'use strict';
(function () {

function cleanForSpeech(text) {
  return String(text || '')
    .replace(/\x1b\[[0-9;?]*[A-Za-z]/g, '')
    .replace(/```[\s\S]*?```/g, ' ')
    .split(/\r?\n/)
    .map(line => line.trim())
    .filter(line => line && !/^\|.*\|$/.test(line) && !/^(session_id:|session:|duration:|messages:|query:|initializing agent|resume this session|hermes --resume|[-─=]{3,})/i.test(line))
    .join(' ')
    .replace(/`([^`]+)`/g, '$1')
    .replace(/\*{1,2}([^*]+)\*{1,2}/g, '$1')
    .replace(/^\s*#+\s*/gm, '')
    .replace(/\s{2,}/g, ' ')
    .trim();
}

function escapeHtml(value) {
  return String(value ?? '').replace(/[&<>"']/g, char => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[char]));
}

function inlineMarkdown(value) {
  let text = escapeHtml(value);
  text = text.replace(/`([^`]+)`/g, '<code>$1</code>');
  text = text.replace(/\*\*([^*]+)\*\*/g, '<strong>$1</strong>');
  text = text.replace(/(^|[^*])\*([^*]+)\*/g, '$1<em>$2</em>');
  text = text.replace(/\[([^\]]+)\]\((https?:\/\/[^\s)]+)\)/g, '<a href="$2" rel="noopener noreferrer" target="_blank">$1</a>');
  return text;
}

function markdownToSafeHtml(markdown) {
  const lines = String(markdown ?? '').replace(/\r/g, '').split('\n');
  const out = []; let paragraph = [], list = null, code = false, codeLines = [];
  const flushParagraph = () => { if (paragraph.length) { out.push(`<p>${paragraph.map(inlineMarkdown).join('<br>')}</p>`); paragraph=[]; } };
  const flushList = () => { if (list) { out.push(`<${list}>${listItems.map(item=>`<li>${inlineMarkdown(item)}</li>`).join('')}</${list}>`); list=null; listItems=[]; } };
  let listItems = [];
  for (const line of lines) {
    if (/^\s*```/.test(line)) {
      flushParagraph(); flushList();
      if (code) { out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}\n</code></pre>`); codeLines=[]; code=false; }
      else code=true;
      continue;
    }
    if (code) { codeLines.push(line); continue; }
    if (!line.trim()) { flushParagraph(); flushList(); continue; }
    const heading = line.match(/^\s{0,3}(#{1,6})\s+(.+)$/);
    if (heading) { flushParagraph(); flushList(); const level=heading[1].length; out.push(`<h${level}>${inlineMarkdown(heading[2])}</h${level}>`); continue; }
    const item = line.match(/^\s*[-*+]\s+(.+)$/);
    if (item) { flushParagraph(); if (list !== 'ul') { flushList(); list='ul'; } listItems.push(item[1]); continue; }
    const numbered = line.match(/^\s*\d+[.)]\s+(.+)$/);
    if (numbered) { flushParagraph(); if (list !== 'ol') { flushList(); list='ol'; } listItems.push(numbered[1]); continue; }
    flushList(); paragraph.push(line);
  }
  flushParagraph(); flushList();
  if (code) out.push(`<pre><code>${escapeHtml(codeLines.join('\n'))}\n</code></pre>`);
  return out.join('\n');
}

function createCancelableJob(work) {
  const controller = new AbortController(); let current = true, settled = false, resolveOuter, failure=null;
  const promise = new Promise(resolve => { resolveOuter=resolve; });
  try {
    Promise.resolve(work(controller.signal)).then(value=>{
      if (!settled) { settled=true; resolveOuter(current ? value : null); }
    },error=>{ failure=error; if (!settled) { settled=true; resolveOuter(null); } });
  } catch (error) { failure=error; settled=true; resolveOuter(null); }
  return {
    promise,
    signal:controller.signal,
    error() { return failure; },
    cancel() { current=false; controller.abort(); if (!settled) { settled=true; resolveOuter(null); } },
    isCurrent() { return current; }
  };
}

function makeConfirmedPayload(text, confirmed) {
  const message=String(text ?? '').trim();
  return confirmed && message ? {message, confirmed:true} : null;
}

function findDigitTokens(text) {
  // Anything carrying a digit that a human must verify before sending:
  // 192.168.10.5, 10.0.0.0/8, /26, 8080, admin01, 24, 255.255.255.0
  const seen = new Set(); const out = [];
  String(text ?? '').split(/\s+/).forEach(word => {
    const token = word.replace(/^[^\w/:%-]+|[^\w/:%-]+$/g, '').replace(/^\.+|\.+$/g, '');
    if (/\d/.test(token) && !seen.has(token)) { seen.add(token); out.push(token); }
  });
  return out;
}

if (typeof module !== 'undefined') module.exports={cleanForSpeech,markdownToSafeHtml,createCancelableJob,makeConfirmedPayload,findDigitTokens};
if (typeof window !== 'undefined') window.JarvisVoiceCore={cleanForSpeech,markdownToSafeHtml,createCancelableJob,makeConfirmedPayload,findDigitTokens};
})();

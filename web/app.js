const $ = s => document.querySelector(s);
const state = {documents: [], selected: new Set(), busy: false, historyToken: 0};
let session = localStorage.getItem('review-session');
if (!session) {session = crypto.randomUUID(); localStorage.setItem('review-session', session);}
async function api(path, options = {}) {
  const r = await fetch(path, options);
  const data = await r.json();
  if (!r.ok) throw new Error(typeof data.detail === 'string' ? data.detail : '입력 내용을 확인해 주세요.');
  return data;
}
let toastTimer;
function toast(msg) {$('#toast').textContent = msg; $('#toast').style.display = 'block'; clearTimeout(toastTimer); toastTimer = setTimeout(() => $('#toast').style.display = 'none', 6000);}
function setBusy(value, message = '') {state.busy = value; document.querySelectorAll('button, #file, #documents input').forEach(b => b.disabled = value); $('#activity').textContent = message;}
function locationLabel(c) {return c.page ? `PDF ${c.page}쪽` : `${c.start_line}~${c.end_line}행`;}
async function showSource(cid) {
  try {const c = await api(`/api/sources/${encodeURIComponent(cid)}`); $('#source-title').textContent = `${c.name} · ${locationLabel(c)}`; $('#source-content').textContent = c.text; $('#source-dialog').showModal();} catch(e) {toast(e.message);}
}
function addMessage(role, text, metadata = {}) {
  metadata = {...metadata, sources: (metadata.sources || []).filter(c => text.includes(`[${c.id}]`))};
  $('#welcome')?.remove();
  const box = document.createElement('article'); box.className = `message ${role}`;
  const who = document.createElement('div'); who.className = 'who'; who.textContent = role === 'user' ? '나의 질문' : '다시봄'; box.append(who);
  const body = document.createElement('div'); body.className = 'body'; let displayText = text; (metadata.sources || []).forEach((c, i) => {displayText = displayText.split(`[${c.id}]`).join(`[${i + 1}]`);}); body.textContent = displayText; box.append(body);
  if (metadata.sources?.length) {
    const list = document.createElement('div'); list.className = 'source-list';
    const extra = document.createElement('details'); extra.className = 'extra-sources'; const more = document.createElement('summary'); more.textContent = `나머지 출처 ${Math.max(0, metadata.sources.length - 4)}개 보기`; extra.append(more);
    for (const [index, c] of metadata.sources.entries()) {const b = document.createElement('button'); b.textContent = `[${index + 1}] ${c.name} · ${locationLabel(c)}`; b.title = c.id; b.onclick = () => showSource(c.id); (index < 4 ? list : extra).append(b);} if(metadata.sources.length > 4) list.append(extra); box.append(list);
  }
  if (metadata.coverage) {const meta = document.createElement('div'); meta.className = 'meta'; meta.textContent = `${metadata.backend === 'extractive' ? '원문 발췌 · ' : 'AI 답변 · '}${metadata.coverage}`; box.append(meta);}
  if (metadata.events?.length) {const trace = document.createElement('details'); trace.className = 'trace'; const title = document.createElement('summary'); title.textContent = `처리 과정 · ${metadata.elapsed_ms}ms`; trace.append(title); for (const e of metadata.events) {const row = document.createElement('div'); row.textContent = e.detail; trace.append(row);} box.append(trace);}
  $('#messages').append(box); $('#messages').scrollTop = $('#messages').scrollHeight;
}
function emptyMessages() {$('#messages').replaceChildren(); const welcome = document.createElement('div'); welcome.id = 'welcome'; welcome.className = 'welcome'; welcome.textContent = '선택한 자료에 관해 질문하거나 핵심 요약을 눌러보세요.'; $('#messages').append(welcome);}
async function loadHistory() {
  const token = ++state.historyToken;
  try {const rows = await api(`/api/history/${encodeURIComponent(session)}?document_ids=${encodeURIComponent([...state.selected].sort().join(','))}`); if (token !== state.historyToken) return; emptyMessages(); for (const row of rows) addMessage(row.role, row.content, row.metadata);} catch(e) {toast(e.message);}
}
function renderDocuments() {
  $('#documents').replaceChildren(); $('#doc-count').textContent = state.documents.length;
  $('#selected-count').textContent = state.selected.size ? `${state.selected.size}개 자료 선택됨` : '선택한 자료 없음';
  for (const d of state.documents) {
    const box = document.createElement('div'); box.className = 'doc'; const input = document.createElement('input'); input.type = 'checkbox'; input.id = `doc-${d.id}`; input.checked = state.selected.has(d.id); input.onchange = () => {input.checked ? state.selected.add(d.id) : state.selected.delete(d.id); renderDocuments(); loadHistory();};
    const label = document.createElement('label'); label.htmlFor = input.id; const name = document.createElement('div'); name.className = 'doc-name'; name.textContent = d.name; const sub = document.createElement('small'); sub.textContent = `${d.chunk_count}개 구간 · ${d.characters.toLocaleString()}자`; label.append(name, sub);
    const del = document.createElement('button'); del.className = 'delete'; del.textContent = '×'; del.title = '자료 삭제'; del.setAttribute('aria-label', `${d.name} 삭제`); del.onclick = async () => {if (!confirm(`“${d.name}” 자료를 삭제할까요?`)) return; try {await api(`/api/documents/${d.id}`, {method: 'DELETE'}); state.selected.delete(d.id); await refresh(); await loadHistory();} catch(e) {toast(e.message);}};
    box.append(input, label, del); $('#documents').append(box);
  }
}
async function refresh() {state.documents = await api('/api/documents'); const ids = new Set(state.documents.map(d => d.id)); state.selected = new Set([...state.selected].filter(id => ids.has(id))); renderDocuments();}
async function send(mode = 'auto', message = null) {
  if (state.busy) return;
  if (!state.selected.size) return toast('먼저 복습할 자료를 추가하고 선택해 주세요.');
  if (state.selected.size > 20) return toast('한 번에 최대 20개 자료를 선택해 주세요.');
  const q = (message ?? $('#question').value).trim(); if (!q) return;
  state.historyToken++; addMessage('user', q); $('#question').value = ''; setBusy(true, '자료를 찾아 답변을 준비하고 있어요…');
  const requestId = crypto.randomUUID();
  let polling = false;
  const progressTimer = setInterval(async () => {if(polling) return; polling = true; try {const p = await api(`/api/progress/${requestId}`); if(p.detail) $('#activity').textContent = p.detail;} catch {} finally {polling = false;}}, 1500);
  try {const result = await api('/api/chat', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({message: q, document_ids: [...state.selected], session_id: session, request_id: requestId, mode})}); addMessage('assistant', result.answer, result);} catch(e) {addMessage('assistant', e.message); $('#question').value = q;} finally {clearInterval(progressTimer); setBusy(false); $('#question').focus();}
}
$('#chat-form').onsubmit = e => {e.preventDefault(); send();};
$('#question').onkeydown = e => {if (e.key === 'Enter' && !e.shiftKey && !e.isComposing) {e.preventDefault(); send();}};
document.querySelectorAll('[data-mode]').forEach(b => b.onclick = () => send(b.dataset.mode, b.dataset.mode === 'summary' ? '선택한 수업 자료의 핵심을 요약해 줘' : '수업 내용을 복습할 퀴즈를 만들어 줘'));
document.querySelectorAll('[data-question]').forEach(b => b.onclick = () => send('ask', b.dataset.question));
$('#new-chat').onclick = () => {state.historyToken++; session = crypto.randomUUID(); localStorage.setItem('review-session', session); emptyMessages(); toast('새 대화를 시작했어요.');};
$('#file').onchange = async e => {
  const files = [...e.target.files]; if (!files.length || state.busy) return; setBusy(true, '수업 자료를 읽고 있어요…');
  try {for (const file of files) {if (file.size > 10*1024*1024) {toast(`${file.name}: 10MB 이하 파일을 선택해 주세요.`); continue;} const form = new FormData(); form.append('file', file); try {const doc = await api('/api/documents/upload', {method: 'POST', body: form}); state.selected.add(doc.id); toast(doc.duplicate ? '이미 등록한 자료를 선택했어요.' : `${doc.name} 등록 완료`);} catch(e) {toast(e.message);}} await refresh(); await loadHistory();} finally {setBusy(false); $('#file').value = '';}
};
$('#paste-open').onclick = () => $('#paste-dialog').showModal(); $('#paste-close').onclick = () => $('#paste-dialog').close(); $('#source-close').onclick = () => $('#source-dialog').close();
$('#paste-form').onsubmit = async e => {e.preventDefault(); if (state.busy) return; setBusy(true, '자료를 등록하고 있어요…'); try {const d = await api('/api/documents/text', {method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify({title: $('#paste-title').value, text: $('#paste-text').value})}); state.selected.add(d.id); $('#paste-dialog').close(); $('#paste-form').reset(); await refresh(); await loadHistory(); toast('자료를 등록했어요.');} catch(e) {toast(e.message);} finally {setBusy(false);}};
(async () => {try {const health = await api('/api/health'); $('#backend').textContent = health.label; await refresh(); if(state.documents.length) {state.selected.add(state.documents[0].id); renderDocuments(); await loadHistory();}} catch(e) {$('#backend').textContent = '서버 연결 실패'; toast(e.message);}})();

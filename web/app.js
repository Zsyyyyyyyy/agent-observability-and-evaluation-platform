const api = async (url) => { const r = await fetch(url); if (!r.ok) throw new Error(r.status); return r.json(); };
const fmt = (ms) => ms ? `${(ms / 1000).toFixed(1)}s` : "—";
const signed = (n, suffix = "") => `${n > 0 ? "+" : ""}${n}${suffix}`;
const median = (xs) => { const s = xs.filter(Number.isFinite).sort((a,b) => a-b); if (!s.length) return 0; const i = Math.floor(s.length / 2); return s.length % 2 ? s[i] : (s[i-1] + s[i]) / 2; };
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
const METRICS = {
  duration_ms: { label: "Latency", unit: "ms", lowerIsBetter: true, format: v => fmt(v) },
  model_tokens: { label: "Model tokens", unit: "tokens", lowerIsBetter: true, format: v => `${Math.round(v).toLocaleString()} tok` },
  tool_calls: { label: "Tool calls", unit: "calls", lowerIsBetter: true, format: v => `${Math.round(v)} calls` },
};
let state = { trials: [], experiment: {}, gate: {}, versions: [], selectedCase: null, metric: "duration_ms" };

function versionLabels(experiment, trials) {
  const agents = experiment?.agents || [];
  const fromReport = agents.map(a => a.version).filter(Boolean);
  return [...new Set([...fromReport, ...trials.map(t => t.agent_version).filter(Boolean)])].slice(0, 2);
}
function summarize(rows) { const good = rows.filter(r => r.passed && r.trace_valid); return { rows, pass: good.length, count: rows.length, duration: median(good.map(r => r.duration_ms)), tools: median(good.map(r => r.tool_calls)), tokens: median(good.map(r => r.model_tokens)), failures: rows.filter(r => !r.passed).map(r => r.status) }; }
function deltaClass(n, lowerIsBetter = true) { return n === 0 ? "flat" : (lowerIsBetter ? n < 0 : n > 0) ? "positive" : "negative"; }

async function load() {
  let dashboard, trials, experiment, gate;
  try { [dashboard, trials, experiment, gate] = await Promise.all([api('/api/dashboard'), api('/api/trials'), api('/api/experiments/latest'), api('/api/gate/latest')]); }
  catch { dashboard={runtime_label:"Demo fallback · start serve_dashboard.py for artifacts",trial_count:0}; trials=[]; experiment={}; gate={}; document.querySelector('#connection').textContent='● API UNAVAILABLE'; }
  state = { ...state, trials, experiment, gate, versions: versionLabels(experiment, trials) };
  document.querySelector('#runtime-root').textContent = dashboard.runtime_label || 'Local experiment artifacts';
  renderDecision(dashboard); renderMatrix(); renderTriage();
  const firstCase = caseIds()[0];
  if (state.selectedCase && caseIds().includes(state.selectedCase)) renderCaseDetail(state.selectedCase, false);
  else if (firstCase) renderCaseDetail(firstCase, false);
}
function renderDecision(dashboard) {
  const c = state.experiment?.comparison, b = c?.baseline, v = c?.candidate, d = c?.delta || {};
  const passed = state.gate?.passed;
  document.querySelector('#gate-card').innerHTML = `<span class="gate-kicker">PROMOTION GATE</span><strong class="${passed ? 'gate-pass' : 'gate-hold'}">${passed === true ? 'PASS' : passed === false ? 'HOLD' : 'PENDING'}</strong><span>${passed === true ? 'Candidate meets the current release policy.' : 'Load a gate report to decide.'}</span>`;
  document.querySelector('#trial-count').textContent = dashboard.trial_count || '—';
  document.querySelector('#pass-delta').textContent = c ? signed((d.evaluation_pass_rate * 100).toFixed(1), 'pp') : '—';
  document.querySelector('#duration-delta').textContent = c ? signed(Math.round(d.avg_duration_ms), 'ms') : '—';
  document.querySelector('#token-delta').textContent = c ? signed(Math.round(d.avg_model_tokens || 0)) : '—';
  document.querySelector('#reliability').textContent = b && v ? `${Math.round(b.model_failed_rate*100)}% / ${Math.round(v.model_failed_rate*100)}%` : '—';
  const rules = state.gate?.rules || [];
  const fail = state.trials.filter(t => !t.passed); const model = state.trials.filter(t => t.status === 'model_failed');
  document.querySelector('#reliability-content').innerHTML = `<div class="reliability-number"><b>${model.length}</b><span>model failures</span></div><div class="reliability-number"><b>${state.trials.filter(t=>!t.trace_valid).length}</b><span>invalid traces</span></div><div class="rule-list">${rules.length ? rules.map(r=>`<div><span>${esc(r.name.replaceAll('_',' '))}</span><b class="${r.passed?'ok-text':'bad-text'}">${r.passed?'PASS':'BLOCK'}</b></div>`).join('') : `<div><span>${fail.length} trials need review</span></div>`}</div>`;
}
function renderMatrix() {
  const [base, candidate] = state.versions; const grouped = new Map();
  state.trials.forEach(t => { const k=t.case_id || t.trial_id; if (!grouped.has(k)) grouped.set(k, []); grouped.get(k).push(t); });
  const rows = [...grouped.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([caseId, items]) => {
    const left=summarize(items.filter(x=>x.agent_version===base)), right=summarize(items.filter(x=>x.agent_version===candidate));
    const delta=right.duration-left.duration; const passDelta=right.pass-left.pass;
    const status = right.pass > left.pass ? 'improved' : right.pass < left.pass ? 'regressed' : 'stable';
    return `<button class="comparison-row ${status} ${state.selectedCase===caseId?'selected':''}" data-case="${esc(caseId)}" aria-pressed="${state.selectedCase===caseId}" type="button"><span class="case-name">${esc(caseId.replaceAll('_',' '))}<small>${left.count || 0} × ${candidate ? 2 : 1} versions</small></span>${summaryCell(left, base)}<span class="delta-cell ${deltaClass(delta)}"><b>${signed(passDelta)}</b><small>pass</small><em>${signed(Math.round(delta),'ms')}</em></span>${summaryCell(right, candidate)}</button>`;
  });
  document.querySelector('#comparison-rows').innerHTML = rows.join('') || '<p class="empty big">No paired Trial artifacts available.</p>';
  document.querySelectorAll('.comparison-row').forEach(row => row.addEventListener('click', () => renderCaseDetail(row.dataset.case, true)));
}
function summaryCell(s, version) { const note=s.failures[0] ? s.failures[0].replaceAll('_',' ') : 'trace valid'; return `<span class="version-cell"><b>${s.pass}/${s.count || 0}</b><small>${esc(version || 'unknown')}</small><em>${fmt(s.duration)} · ${Math.round(s.tokens).toLocaleString()} tok · ${s.tools} tools</em><i class="${s.failures.length?'bad-text':'ok-text'}">${esc(note)}</i></span>`; }
function caseIds() { return [...new Set(state.trials.map(t => t.case_id).filter(Boolean))].sort((a,b) => a.localeCompare(b)); }
function trialNumber(row) { return String(row.trial_id || row.id || "").split("_trial_").pop() || "?"; }
function renderCaseDetail(caseId, shouldScroll = false) {
  state.selectedCase=caseId; const panel=document.querySelector('#case-detail-panel'); panel.hidden=false; document.querySelector('#case-detail-title').textContent=caseId.replaceAll('_',' ');
  const picker=document.querySelector('#case-select'); picker.innerHTML=caseIds().map(id=>`<option value="${esc(id)}">${esc(id.replaceAll('_',' '))}</option>`).join(''); picker.value=caseId;
  document.querySelectorAll('.metric-tabs [data-metric]').forEach(tab=>{const selected=tab.dataset.metric===state.metric;tab.setAttribute('aria-selected',String(selected));tab.classList.toggle('active',selected);});
  renderCaseChart(caseId); renderCaseSummary(caseId);
  const [base, candidate]=state.versions; const columns=[base,candidate].filter(Boolean).map(version=>{const rows=state.trials.filter(t=>t.case_id===caseId&&t.agent_version===version);const s=summarize(rows);return `<section class="run-column"><p class="eyebrow">${esc(version || 'UNKNOWN')}</p><h3>${s.pass}/${s.count} valid passes</h3><div class="run-metrics"><span>Median <b>${fmt(s.duration)}</b></span><span>Tokens <b>${Math.round(s.tokens).toLocaleString()}</b></span><span>Tools <b>${s.tools}</b></span></div><div class="run-buttons">${rows.map(r=>`<button type="button" data-trial="${encodeURIComponent(r.id)}" class="trial-pill ${r.passed?'pass':'fail'}">T${esc(trialNumber(r))} · ${r.passed?'PASS':esc(r.status)}</button>`).join('')}</div></section>`;}).join('');
  document.querySelector('#case-detail-content').innerHTML=columns || '<p class="empty big">No Trial artifacts for this Case.</p>';
  document.querySelectorAll('[data-trial]').forEach(b=>b.addEventListener('click',()=>showTrial(decodeURIComponent(b.dataset.trial))));
  renderMatrix();
  if (shouldScroll) panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}
function renderCaseChart(caseId) {
  const metric=METRICS[state.metric], [base,candidate]=state.versions, rows=state.trials.filter(t=>t.case_id===caseId);
  const groups=new Map(); rows.forEach(row=>{const key=trialNumber(row);if(!groups.has(key))groups.set(key,{});groups.get(key)[row.agent_version]=row;});
  const ordered=[...groups.keys()].sort((a,b)=>Number(a)-Number(b)||a.localeCompare(b));
  const values=rows.map(row=>Number(row[state.metric])).filter(Number.isFinite).filter(v=>v>0), max=Math.max(...values,1);
  const bar=(row, version)=>{const value=Number(row?.[state.metric]);const available=Number.isFinite(value)&& (value>0 || (state.metric==='tool_calls' && row?.passed));const height=available?Math.max(5,Math.round(value/max*100)):0;const failed=row&&!row.passed;return `<div class="chart-bar-wrap"><span class="chart-value">${available?esc(metric.format(value)):'N/A'}</span><div class="chart-bar ${version===base?'baseline-bar':'candidate-bar'} ${failed?'failed-bar':''} ${available?'':'missing-bar'}" style="--bar-height:${height}%"><span>${esc(version||'—')}</span></div></div>`;};
  const legend=`<div class="chart-legend"><span><i class="legend-swatch baseline-swatch"></i>${esc(base||'v1')}</span><span><i class="legend-swatch candidate-swatch"></i>${esc(candidate||'v2')}</span><small>柱高按当前 Case 的最大值归一化；失败或缺失显示 N/A</small></div>`;
  document.querySelector('#case-chart').innerHTML=`<div class="chart-head"><div><p class="eyebrow">${esc(metric.label.toUpperCase())} BY TRIAL</p><p class="chart-caption">同一 Trial 内并列展示 baseline 与 candidate，避免平均值掩盖单次异常。</p></div><span class="chart-unit">${esc(metric.unit)}</span></div><div class="bar-chart" style="--groups:${Math.max(ordered.length,1)}">${ordered.map(key=>`<div class="chart-group"><div class="chart-bars">${bar(groups.get(key)?.[base],base)}${bar(groups.get(key)?.[candidate],candidate)}</div><span class="trial-label">Trial ${esc(key)}</span></div>`).join('')||'<p class="empty">No metric data.</p>'}</div>${legend}`;
}
function renderCaseSummary(caseId) {
  const metric=METRICS[state.metric], [base,candidate]=state.versions, summaries={};
  [base,candidate].filter(Boolean).forEach(version=>{summaries[version]=summarize(state.trials.filter(t=>t.case_id===caseId&&t.agent_version===version));});
  const left=summaries[base], right=summaries[candidate], key=state.metric==='duration_ms'?'duration':state.metric==='model_tokens'?'tokens':'tools';
  const lv=left?.pass?left[key]:null, rv=right?.pass?right[key]:null, delta=(lv!==null&&rv!==null)?rv-lv:null, pct=(lv!==null&&lv!==0&&delta!==null)?delta/lv*100:null;
  const deltaText=delta===null?'N/A':pct===null?signed(Math.round(delta),` ${metric.unit}`):signed(Number(pct.toFixed(1)),'%'), cls=delta===null?'flat':deltaClass(delta,metric.lowerIsBetter);
  document.querySelector('#case-summary').innerHTML=`<div class="summary-version baseline-summary"><p class="eyebrow">BASELINE · ${esc(base||'—')}</p><strong>${lv!==null?esc(metric.format(lv)):'N/A'}</strong><span>${left?.pass||0}/${left?.count||0} valid passes</span></div><div class="summary-version candidate-summary"><p class="eyebrow">CANDIDATE · ${esc(candidate||'—')}</p><strong>${rv!==null?esc(metric.format(rv)):'N/A'}</strong><span>${right?.pass||0}/${right?.count||0} valid passes</span></div><div class="summary-delta ${cls}"><p class="eyebrow">CANDIDATE DELTA</p><strong>${esc(deltaText)}</strong><span>${delta===null?'not enough valid data':`${delta<0?'lower':'higher'} ${metric.label.toLowerCase()}`}</span></div>`;
}
function renderTriage() { const vf=document.querySelector('#version-filter'); const known=[...new Set(state.trials.map(t=>t.agent_version).filter(Boolean))]; vf.innerHTML='<option value="all">All</option>'+known.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join(''); applyFilters(); }
function applyFilters() { const version=document.querySelector('#version-filter').value, outcome=document.querySelector('#outcome-filter').value; const rows=state.trials.filter(t=>(version==='all'||t.agent_version===version)&&(outcome==='all'||(outcome==='pass'?t.passed:!t.passed))); document.querySelector('#trial-rows').innerHTML=rows.map(t=>`<tr class="trial-row" tabindex="0" data-id="${encodeURIComponent(t.id)}"><td><b>${esc(t.case_id)}</b><small>${esc(t.trial_id)}</small></td><td>${esc(t.agent_version)}</td><td><span class="status ${t.passed?'ok':'bad'}">${t.passed?'PASS':esc(t.status)}</span></td><td>${t.tool_calls}</td><td>${fmt(t.duration_ms)}</td><td>${Math.round(t.model_tokens||0).toLocaleString()}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No matching Trial.</td></tr>'; document.querySelectorAll('.trial-row').forEach(r=>{r.onclick=()=>showTrial(decodeURIComponent(r.dataset.id));r.onkeydown=e=>{if(e.key==='Enter')r.click();};}); }
function traceRows(events) {
  const ends = new Map(events.filter(e => e.kind === 'span_end' && e.span_id).map(e => [e.span_id, e]));
  return events.filter(e => !(e.kind === 'span_end' && e.span_id && events.some(start => start.kind === 'span_start' && start.span_id === e.span_id))).map(e => {
    const attrs = e.attributes || {}, end = ends.get(e.span_id), endAttrs = end?.attributes || {};
    const isTool = e.name === 'tool.call' || Boolean(attrs.tool_name);
    const label = isTool ? (attrs.tool_name || 'unnamed tool') : (e.name || e.span_id || 'event');
    const kind = isTool ? 'TOOL' : String(e.kind || 'EVENT').replace('span_start', 'SPAN');
    const meta = [];
    const duration = endAttrs.duration_ms ?? attrs.duration_ms;
    if (Number.isFinite(Number(duration))) meta.push(`${Number(duration).toFixed(1)}ms`);
    if (end?.status || e.status) meta.push(end?.status || e.status);
    if (attrs.model) meta.push(String(attrs.model));
    const preview = endAttrs.output_preview || attrs.output_preview;
    return `<li class="trace-event ${isTool?'tool-event':''}"><span class="trace-kind">${esc(kind)}</span><span class="trace-body"><strong class="trace-name">${esc(label)}</strong>${meta.length?`<span class="trace-meta">${esc(meta.join(' · '))}</span>`:''}${preview?`<span class="trace-preview">${esc(String(preview).replace(/\s+/g,' ').slice(0,180))}</span>`:''}</span></li>`;
  });
}
function diffModel(diff, changedFiles) {
  const lines = String(diff || '').split('\n');
  const fromHeaders = lines.map(line => line.match(/^diff --git a\/(.+) b\/(.+)$/)?.[2]).filter(Boolean);
  const files = [...new Set([...(changedFiles || []), ...fromHeaders])];
  return { files, added: lines.filter(line => line.startsWith('+') && !line.startsWith('+++')).length, removed: lines.filter(line => line.startsWith('-') && !line.startsWith('---')).length };
}
function renderDiff(diff, changedFiles) {
  const patch = String(diff || ''), info = diffModel(patch, changedFiles);
  document.querySelector('#diff-summary').innerHTML = `<div class="diff-summary-metrics"><span><b>${info.files.length}</b> file${info.files.length===1?'':'s'} changed</span><span class="diff-add"><b>+${info.added}</b> added</span><span class="diff-remove"><b>-${info.removed}</b> removed</span></div>${info.files.length?`<div class="diff-files">${info.files.map(file=>`<span>${esc(file)}</span>`).join('')}</div>`:''}`;
  document.querySelector('#diff-status').textContent = patch ? 'raw patch · color coded' : 'no patch';
  document.querySelector('#diff').innerHTML = patch ? patch.split('\n').map(line => { const cls=line.startsWith('+')&&!line.startsWith('+++')?'diff-add':line.startsWith('-')&&!line.startsWith('---')?'diff-remove':line.startsWith('@@')?'diff-hunk':line.startsWith('diff --git')?'diff-file':''; return `<span class="diff-line ${cls}">${esc(line)||' '}</span>`; }).join('') : '<span class="empty">No diff artifact.</span>';
}
async function showTrial(id) { let d; try { d=await api(`/api/trials/${encodeURIComponent(id)}`); } catch { return; } const r=d.result; document.querySelector('#detail-empty').hidden=true; document.querySelector('#detail').hidden=false; document.querySelector('#detail-title').textContent=r.trial_id; const s=document.querySelector('#detail-status'); s.textContent=r.status; s.className=`status ${r.evaluation?.passed?'ok':'bad'}`; document.querySelector('#detail-stats').innerHTML=[`agent ${r.agent_version}`,`profile ${r.agent_profile||'default'}`,`${r.model_usage?.total_tokens||0} tokens`,`${r.changed_files?.length||0} files`].map(x=>`<span class="chip">${esc(x)}</span>`).join(''); const trace=traceRows(d.trace || []); document.querySelector('#trace-list').innerHTML=trace.join('')||'<li class="empty">No trace artifact.</li>'; renderDiff(r.git_diff, r.changed_files); document.querySelector('.detail-panel').scrollIntoView({behavior:'smooth',block:'nearest'}); }
document.querySelector('#refresh').onclick=load; document.querySelector('#version-filter').onchange=applyFilters; document.querySelector('#outcome-filter').onchange=applyFilters; document.querySelector('#case-select').onchange=e=>renderCaseDetail(e.target.value, false); document.querySelectorAll('.metric-tabs [data-metric]').forEach(tab=>tab.onclick=()=>{state.metric=tab.dataset.metric;if(state.selectedCase)renderCaseDetail(state.selectedCase,false);}); document.querySelector('#close-case').onclick=()=>{document.querySelector('#case-detail-panel').hidden=true;state.selectedCase=null;renderMatrix();}; load();

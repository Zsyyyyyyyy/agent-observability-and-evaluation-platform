const api = async (url) => { const r = await fetch(url); if (!r.ok) throw new Error(r.status); return r.json(); };
const hasNumber = value => value !== null && value !== undefined && Number.isFinite(Number(value));
const fmt = (ms) => {
  if (!hasNumber(ms)) return "—";
  const value = Number(ms);
  if (Math.abs(value) < 1000) return `${value.toFixed(value === 0 ? 0 : 1)}ms`;
  return `${(value / 1000).toFixed(1)}s`;
};
const signed = (n, suffix = "") => `${n > 0 ? "+" : ""}${n}${suffix}`;
const median = (xs) => { const s = xs.filter(hasNumber).map(Number).sort((a,b) => a-b); if (!s.length) return null; const i = Math.floor(s.length / 2); return s.length % 2 ? s[i] : (s[i-1] + s[i]) / 2; };
const esc = (v) => String(v ?? "").replace(/[&<>"]/g, c => ({"&":"&amp;","<":"&lt;",">":"&gt;","\"":"&quot;"}[c]));
const fmtTokens = value => hasNumber(value) ? `${Math.round(Number(value)).toLocaleString()} tok` : 'N/A';
const fmtTools = value => hasNumber(value) ? `${Math.round(Number(value))} calls` : 'N/A';
const METRICS = {
  duration_ms: { label: "Latency", unit: "ms", lowerIsBetter: true, format: v => fmt(v) },
  model_tokens: { label: "Model tokens", unit: "tokens", lowerIsBetter: true, format: fmtTokens },
  tool_calls: { label: "Tool calls", unit: "calls", lowerIsBetter: true, format: fmtTools },
};
let state = { context: {}, runtimeAvailable: true, trials: [], dashboard: {}, experiment: {}, gate: {}, evolution: {}, protocol: {}, policyStop: {}, versions: [], selectedCase: null, metric: "duration_ms", traceView: "tree", traceEvents: [] };

function versionLabels(context) { return [context?.baseline_version, context?.candidate_version]; }
function summarize(rows) { const good = rows.filter(r => r.passed && r.trace_valid); return { rows, pass: good.length, count: rows.length, duration: median(good.map(r => r.duration_ms)), tools: median(good.map(r => r.tool_calls)), tokens: median(good.map(r => r.model_tokens)), failures: rows.filter(r => !r.passed).map(r => r.status) }; }
function deltaClass(n, lowerIsBetter = true) { return n === 0 ? "flat" : (lowerIsBetter ? n < 0 : n > 0) ? "positive" : "negative"; }
function sameContext(left, right) { return ['project_id','agent_id','experiment_id','baseline_version','candidate_version'].every(field => (left?.[field] ?? null) === (right?.[field] ?? null)); }
function runtimePayload(value, fallback) { return value?.available === true ? value.data ?? fallback : fallback; }

async function load() {
  const responses = await Promise.allSettled([
    api('/api/dashboard'), api('/api/trials'), api('/api/experiments/latest'),
    api('/api/gate/latest'), api('/api/context'), api('/api/evolution'), api('/api/protocol'), api('/api/policy-stop'),
  ]);
  const value = (index, fallback) => responses[index]?.status === 'fulfilled' ? responses[index].value : fallback;
  const dashboardResponse = value(0, {}), trialsResponse = value(1, {}), experimentResponse = value(2, {}), gateResponse = value(3, {}), contextResponse = value(4, {}), evolution = value(5, {}), protocol = value(6, {}), policyStop = value(7, {});
  const context = contextResponse.context || {};
  const runtimeResponses = [dashboardResponse, trialsResponse, experimentResponse, gateResponse];
  const runtimeAvailable = contextResponse.available === true && runtimeResponses.every(response => response.available === true && sameContext(context, response.context));
  const dashboard = runtimeAvailable ? runtimePayload(dashboardResponse, {trial_count: 0}) : {trial_count: 0};
  const trials = runtimeAvailable ? runtimePayload(trialsResponse, []) : [];
  const experiment = runtimeAvailable ? runtimePayload(experimentResponse, {}) : {};
  const gate = runtimeAvailable ? runtimePayload(gateResponse, {}) : {};
  const failed = responses.filter(response => response.status === 'rejected');
  if (failed.length) document.querySelector('#connection').textContent = failed.length === responses.length ? '● API UNAVAILABLE' : '● PARTIAL API';
  state = { ...state, context, runtimeAvailable, dashboard, trials, experiment, gate, evolution, protocol, policyStop, versions: versionLabels(context) };
  renderConsoleContext(); renderRuntimeContextState(contextResponse, runtimeResponses);
  if (!runtimeAvailable) { renderEvolution(); return; }
  renderMatrixHeaders();
  renderDecision(dashboard); renderEvidenceDeck(dashboard); renderBehaviorRegressionSummary(); renderDecisionSpine(); renderEvolution(); renderProtocol(); renderMatrix(); renderTriage();
  const firstCase = primaryCaseId() || caseIds()[0];
  if (state.selectedCase && caseIds().includes(state.selectedCase)) renderCaseDetail(state.selectedCase, false);
  else if (firstCase) renderCaseDetail(firstCase, false);
}
function renderConsoleContext() {
  const context=state.context || {}, content=document.querySelector('#console-context');
  const experiment=(state.evolution?.experiments || []).find(item=>item.experiment_id===state.evolution?.current_experiment_id);
  const versions=[context.baseline_version,context.candidate_version].filter(Boolean).join(' → ');
  const experimentName=experiment?.name || (versions ? `${versions} Regression` : context.experiment_id || 'unlinked');
  const note=context.scope_status==='mismatch' ? 'Context mismatch: Runtime Evidence is hidden.' : context.legacy ? 'This artifact was created before Evaluation Context support. Project identity unavailable. Some evolution views may be limited.' : 'All Console evidence is scoped to this Experiment.';
  const field=(label,value)=>`<div><span>${esc(label)}</span><strong>${esc(value || 'unavailable')}</strong></div>`;
  content.innerHTML=`<header><span>EVALUATION CONTEXT</span><small>${esc(note)}</small></header><div class="console-context-fields">${field('PROJECT',context.project_id || 'Unknown Project')}${field('AGENT',context.agent_id || 'Unknown Agent')}${field('EXPERIMENT',experimentName)}${field('BASELINE',context.baseline_version)}${field('CANDIDATE',context.candidate_version)}</div>`;
  content.classList.toggle('context-warning', context.legacy === true || context.scope_status === 'mismatch');
}
function renderRuntimeContextState(contextResponse, runtimeResponses) {
  const valid = state.runtimeAvailable, error = document.querySelector('#runtime-context-error');
  document.querySelectorAll('[data-runtime-evidence]').forEach(element => { element.hidden = !valid; });
  if (valid) { error.hidden = true; return; }
  const reason = contextResponse.reason || runtimeResponses.find(item => item?.reason)?.reason || 'runtime_context_mismatch';
  const runtime = contextResponse.runtime || runtimeResponses.find(item => item?.runtime)?.runtime || {};
  const runtimeVersions = [runtime.baseline_version, runtime.candidate_version].filter(Boolean).join(' → ') || 'unavailable';
  const expectedVersions = [state.context.baseline_version, state.context.candidate_version].filter(Boolean).join(' → ') || 'unavailable';
  error.hidden = false;
  error.innerHTML = `<span>EVALUATION CONTEXT MISMATCH</span><strong>Runtime Evidence hidden</strong><div class="runtime-context-comparison"><div><span>RUNTIME</span><b>${esc(runtimeVersions)}</b></div><div><span>EXPECTED CATALOG</span><b>${esc(expectedVersions)}</b></div></div><small>${esc(reason.replaceAll('_',' '))}. Case, Trial, Latency, Gate, and Trace data are not shown until this Runtime matches its Experiment Context.</small>`;
  state.selectedCase = null;
  document.querySelector('#case-detail-panel').hidden = true;
  document.querySelector('#detail').hidden = true;
  document.querySelector('#detail-empty').hidden = false;
}
function renderMatrixHeaders() {
  const [baseline, candidate] = state.versions;
  document.querySelector('#matrix-baseline-label').textContent = `BASELINE · ${baseline || 'unavailable'}`;
  document.querySelector('#matrix-candidate-label').textContent = `CANDIDATE · ${candidate || 'unavailable'}`;
}
function renderProtocol() {
  const content=document.querySelector('#protocol-content'), hint=document.querySelector('#protocol-hint'), p=state.protocol || {};
  if (!p.available) { hint.textContent='legacy artifact'; content.innerHTML='<p class="empty big">This historical Experiment predates protocol freezing. Its results remain inspectable, but they cannot claim strict comparability.</p>'; return; }
  const comparability=p.comparability || {}, level=String(comparability.level || 'not_available');
  hint.textContent=`${level.toUpperCase()} · ${String(p.fingerprint || '').slice(0,18)}…`;
  const yesNo=value=>value===true?'Docker':'trusted host';
  content.innerHTML=`<div class="protocol-grid"><div><span>COMPARISON INTENT</span><strong>${esc(p.comparison_intent || 'unrecorded')}</strong></div><div><span>FIXED MODEL</span><strong>${esc(p.model || 'unconfigured')}</strong><small>${esc(p.provider || 'provider unrecorded')}</small></div><div><span>BENCHMARK</span><strong>${esc(String(p.case_count ?? 0))} Cases × ${esc(String(p.trials_per_case ?? '—'))} Trials</strong><small>paired schedule seed ${esc(String(p.schedule_seed ?? '—'))}</small></div><div><span>EXECUTION</span><strong>${esc(yesNo(p.docker))}</strong><small>${esc(p.image || 'no container image')}</small></div></div><div class="protocol-foot"><span>ALLOWED CHANGE</span><b>${esc((p.allowed_differences || []).join(', ') || 'none')}</b><span>PROTOCOL</span><b>${esc(String(p.fingerprint || 'unavailable'))}</b></div>`;
}
function renderEvolution() {
  const content=document.querySelector('#evolution-content'), hint=document.querySelector('#evolution-hint');
  const evolution=state.evolution || {}, versions=[...(evolution.versions || [])];
  const context=evolution.context || state.context || {}, legacy=context.legacy === true;
  const experiments=[...(evolution.experiments || [])].sort((a,b)=>String(a.completed_at || a.created_at).localeCompare(String(b.completed_at || b.created_at)));
  const experiment=experiments.find(item=>item.experiment_id===evolution.current_experiment_id) || experiments.at(-1);
  const ledgerExperiments=[...(evolution.ledger_experiments || (experiment ? [experiment] : []))];
  const gate=(evolution.gate_decisions || []).find(item=>item.experiment_id===experiment?.experiment_id);
  if (!evolution.available || !versions.length) {
    hint.textContent=String(evolution.reason || 'catalog unavailable').replaceAll('_',' ');
    const mismatch=evolution.reason==='runtime_catalog_context_mismatch';
    const lineageMissing=['catalog_missing','catalog_experiment_missing','catalog_lineage_missing'].includes(evolution.reason);
    content.innerHTML=mismatch
      ? '<p class="empty big">Catalog scope does not match the current Evaluation Context. Version history is hidden.</p>'
      : lineageMissing
        ? '<div class="evolution-empty"><span>NO EVOLUTION LINEAGE ATTACHED</span><strong>This experiment has runtime evidence, but no matching catalog history.</strong><small>Run or index the Experiment with its Evaluation Context to attach lineage history.</small></div>'
        : '<p class="empty big">No version history is attached to this Evaluation Context yet.</p>';
    return;
  }
  hint.textContent=legacy
    ? `LEGACY CATALOG · no project identity · ${evolution.catalog_path || 'local catalog'}`
    : `${context.project_id || 'Unknown Project'} · ${versions.length} versions · ${experiments.length} experiments`;
  const scope=`<div class="evolution-selectors"><span class="evolution-picker">PROJECT · ${esc(context.project_id || 'Unknown Project')}</span><span class="evolution-picker">AGENT · ${esc(context.agent_id || 'Unknown Agent')}</span><small>${legacy ? 'Legacy Catalog (no project identity)' : 'Current Evaluation Context only'}</small></div>`;
  const node=(version,index)=>{const isCandidate=version.version_id===experiment?.candidate_version_id; const isBaseline=version.version_id===experiment?.baseline_version_id; const role=isCandidate?'CANDIDATE':isBaseline?'BASELINE':'HISTORY'; const snapshot=version.snapshot || {}; return `<button type="button" class="evolution-node ${isCandidate?'candidate-node':''}" data-evolution-version="${esc(version.version_id)}" aria-expanded="${isCandidate?'true':'false'}"><span class="evolution-step">${String(index+1).padStart(2,'0')}</span><span class="evolution-marker"></span><span class="evolution-role">${role}</span><strong>${esc(version.version)}</strong><small>${esc(version.change_type)} · ${esc(version.status)}</small><span class="evolution-summary">${esc(version.change_summary)}</span><span class="evolution-meta">${esc(snapshot.adapter_id || 'adapter')} / ${esc(snapshot.prompt_profile || 'default profile')}</span></button>`;};
  const experimentGate=state.gate?.decision?.status || (state.gate?.passed === true ? 'promote' : state.gate?.passed === false ? 'hold' : 'unavailable');
  const gateView=gate ? `<aside class="evolution-gate ${gate.status==='promote'?'promote':'hold'}"><span>LINEAGE GATE RECORD</span><strong>${esc(gate.status.toUpperCase())}</strong><small>${esc(gate.policy_version)} · ${esc(String(gate.rules?.length || 0))} rules</small></aside>` : `<aside class="evolution-gate pending"><span>LINEAGE GATE RECORD</span><strong>PENDING</strong><small>No catalog-linked Gate record. Experiment Gate: ${esc(experimentGate.toUpperCase())}.</small></aside>`;
  const metric=(value, kind)=>{const number=Number(value);if(!Number.isFinite(number))return 'N/A';if(kind==='pass')return `${number>0?'+':''}${(number*100).toFixed(1)}pp`;if(kind==='latency')return `${number>0?'+':''}${Math.round(number)}ms`;return `${number>0?'+':''}${Math.round(number)}`;};
  const comparability=(item)=>{const info=item.comparability || {}, level=info.level || 'none', label={first:'FIRST RECORD',strict:'STRICTLY COMPARABLE',partial:'PARTIALLY COMPARABLE',none:'NOT COMPARABLE'}[level] || 'NOT COMPARABLE', delta=item.comparison_summary?.delta || {}; return `<article class="experiment-ledger-row ${esc(level)} ${item.experiment_id===experiment?.experiment_id?'current':''}"><div class="experiment-ledger-title"><span>${esc(label)}</span><strong>${esc(item.name || 'version comparison')}</strong><small>${esc(info.reason || 'No comparability note.')}</small></div><div class="experiment-ledger-metrics"><span>PASS <b>${esc(metric(delta.evaluation_pass_rate,'pass'))}</b></span><span>LATENCY <b>${esc(metric(delta.avg_duration_ms,'latency'))}</b></span><span>TOKENS <b>${esc(metric(delta.avg_model_tokens,'count'))}</b></span><span>TOOLS <b>${esc(metric(delta.avg_tool_calls,'count'))}</b></span></div></article>`;};
  const currentName=experiment?.name || context.experiment_id || 'current comparison';
  const baselineVersion=context.baseline_version || experiment?.baseline_version_id || 'unavailable';
  const candidateVersion=context.candidate_version || experiment?.candidate_version_id || 'unavailable';
  content.innerHTML=`${scope}<div class="evolution-rail">${versions.map(node).join('')}</div><div class="evolution-context"><div><span>ACTIVE EXPERIMENT</span><strong>${esc(currentName)}</strong><small>${esc(String(experiment?.case_ids?.length || 0))} Cases · context ${esc(String(experiment?.evaluation_context_hash || '').slice(0,18))}…</small></div>${gateView}</div><section class="experiment-ledger" aria-label="Current Experiment ledger"><div class="experiment-ledger-head"><div><span>EXPERIMENT LEDGER</span><strong>CURRENT EXPERIMENT</strong></div><small>Candidate minus baseline · comparable only when the benchmark basis supports a trend claim.</small></div><div class="experiment-ledger-current"><div><span>EXPERIMENT</span><strong>${esc(currentName)}</strong></div><div><span>BASELINE</span><strong>${esc(baselineVersion)}</strong></div><div><span>CANDIDATE</span><strong>${esc(candidateVersion)}</strong></div></div>${ledgerExperiments.map(comparability).join('')}</section>`;
  document.querySelectorAll('[data-evolution-version]').forEach(button=>button.addEventListener('click',()=>toggleEvolutionNode(button)));
}
function toggleEvolutionNode(button) {
  document.querySelectorAll('[data-evolution-version]').forEach(node=>node.setAttribute('aria-expanded',String(node===button && node.getAttribute('aria-expanded')!=='true')));
}
function renderDecision(dashboard) {
  const c = state.experiment?.comparison, b = c?.baseline, v = c?.candidate, d = c?.delta || {};
  const passed = state.gate?.passed;
  const decisionMessage = state.gate?.decision?.message;
  document.querySelector('#gate-card').innerHTML = `<span class="gate-kicker">EXPERIMENT GATE</span><strong class="${passed ? 'gate-pass' : 'gate-hold'}">${passed === true ? 'PASS' : passed === false ? 'HOLD' : 'PENDING'}</strong><span>${esc(decisionMessage || (passed === true ? 'Candidate meets the current release policy.' : 'Load a gate report to decide.'))}</span>`;
  document.querySelector('#trial-count').textContent = dashboard.trial_count || '—';
  document.querySelector('#pass-delta').textContent = c ? signed((d.evaluation_pass_rate * 100).toFixed(1), 'pp') : '—';
  const statistics = c?.statistics || state.experiment?.statistics || {};
  const pairedDuration = statistics.metrics?.duration_ms?.point_estimate_mean_delta;
  const pairedTokens = statistics.metrics?.model_tokens?.point_estimate_mean_delta;
  document.querySelector('#duration-delta').textContent = hasNumber(pairedDuration) ? signed(Math.round(pairedDuration), 'ms') : '—';
  document.querySelector('#token-delta').textContent = hasNumber(pairedTokens) ? signed(Math.round(pairedTokens)) : '—';
  const reliability = c?.reliability || state.experiment?.reliability || {};
  const allPassDelta = reliability.delta?.all_pass_at_k;
  document.querySelector('#reliability').textContent = hasNumber(allPassDelta) ? signed((Number(allPassDelta) * 100).toFixed(1), 'pp') : '—';
  const rules = state.gate?.rules || [];
  const fail = state.trials.filter(t => !t.passed); const model = state.trials.filter(t => t.status === 'model_failed');
  document.querySelector('#reliability-content').innerHTML = `<div class="reliability-number"><b>${model.length}</b><span>model failures</span></div><div class="reliability-number"><b>${state.trials.filter(t=>!t.trace_valid).length}</b><span>invalid traces</span></div><div class="rule-list">${rules.length ? rules.map(r=>`<div><span>${esc(r.name.replaceAll('_',' '))}</span><b class="${r.passed?'ok-text':'bad-text'}">${r.passed?'PASS':'BLOCK'}</b></div>`).join('') : `<div><span>${fail.length} trials need review</span></div>`}</div>`;
  renderAttribution();
}
function caseComparisons() { return state.experiment?.comparison?.case_comparisons || state.experiment?.case_comparisons || []; }
function consistency(item) { return item?.all_pass_at_k ?? item?.pass_at_k; }
function casePriority(item) {
  const before = consistency(item?.baseline), after = consistency(item?.candidate);
  const failedPairs = (item?.paired_trials || []).filter(pair => pair.baseline?.valid_pass !== pair.candidate?.valid_pass).length;
  const durationMagnitude = Math.max(0, ...(item?.paired_trials || []).map(pair => Math.abs(Number(pair.delta?.duration_ms) || 0)));
  return (before !== after ? 1000000 : 0) + failedPairs * 100000 + durationMagnitude;
}
function primaryCaseId() {
  return [...caseComparisons()].sort((a,b) => casePriority(b) - casePriority(a))[0]?.case_id || null;
}
function renderDecisionSpine() {
  const gateStatus = state.gate?.decision?.status || (state.gate?.passed === true ? 'promote' : state.gate?.passed === false ? 'blocked' : 'pending');
  const statistics = state.experiment?.comparison?.statistics || state.experiment?.statistics || {};
  const conclusion = statistics.conclusion || {};
  const evidenceStatus = conclusion.level || 'not_available';
  const eligible = Number(statistics.eligible_case_count || 0), target = 8;
  const primary = primaryCaseId();
  const primaryReport = caseComparisons().find(item => item.case_id === primary);
  const before = consistency(primaryReport?.baseline), after = consistency(primaryReport?.candidate);
  const evidenceLabel = {limited_coverage:'INCONCLUSIVE',inconclusive:'INCONCLUSIVE',observed_latency_improvement:'OBSERVED',not_available:'NOT AVAILABLE'}[evidenceStatus] || evidenceStatus.replaceAll('_',' ').toUpperCase();
  const gateTone = gateStatus === 'promote' ? 'positive' : gateStatus === 'pending' ? 'flat' : 'negative';
  const evidenceTone = ['limited_coverage','inconclusive'].includes(evidenceStatus) ? 'warning' : evidenceStatus === 'observed_latency_improvement' ? 'positive' : 'flat';
  document.querySelector('#decision-spine-content').innerHTML = `
    <article class="spine-stop ${gateTone}"><span class="spine-index">01</span><small>EXPERIMENT GATE</small><strong>${esc(gateStatus.toUpperCase())}</strong><p>${esc(state.gate?.decision?.message || 'No Experiment Gate report is attached.')}</p></article>
    <article class="spine-stop ${evidenceTone}"><span class="spine-index">02</span><small>STATISTICAL EVIDENCE</small><strong>${esc(evidenceLabel)}</strong><p>${esc(conclusion.reason || 'No paired bootstrap conclusion is available.')}</p></article>
    <article class="spine-stop ${eligible >= target ? 'positive' : 'warning'}"><span class="spine-index">03</span><small>CLAIM COVERAGE</small><strong>${esc(String(eligible))} CASES · required ≥ ${target}</strong><p>${eligible >= target ? 'Broad performance claim coverage reached.' : `${target - eligible} more eligible Cases are required for a broad claim.`}</p></article>
    <button class="spine-stop primary-stop ${before === after ? 'flat' : after ? 'positive' : 'negative'}" type="button" ${primary ? `data-primary-case="${esc(primary)}"` : 'disabled'}><span class="spine-index">04</span><small>PRIMARY INVESTIGATION</small><strong>${esc(primary ? primary.replaceAll('_',' ') : 'NO CASE')}</strong><p>${primary ? `${before ? 'all-pass' : 'not all-pass'} → ${after ? 'all-pass' : 'not all-pass'} · open paired evidence` : 'No Case comparison is available.'}</p></button>`;
  document.querySelector('[data-primary-case]')?.addEventListener('click', event => renderCaseDetail(event.currentTarget.dataset.primaryCase, true));
}
function renderAttribution() {
  const attribution = state.experiment?.comparison?.failure_attribution || state.experiment?.failure_attribution || {};
  const baseline = attribution.baseline || {}, candidate = attribution.candidate || {};
  const [base, candidateVersion] = state.versions;
  const view = (label, key, hint) => {
    const before = baseline[key]?.valid_pass_rate, after = candidate[key]?.valid_pass_rate;
    return `<div class="reliability-view"><span>${esc(label)}</span><div><b class="baseline-value">${esc(pct(before))}</b><i>→</i><b class="candidate-value">${esc(pct(after))}</b></div><small>${esc(hint)}</small></div>`;
  };
  if (!attribution.baseline) {
    document.querySelector('#attribution-content').innerHTML = '<p class="attribution-empty">Failure attribution needs an experiment report.</p>';
    return;
  }
  const counts = candidate.counts || {};
  document.querySelector('#attribution-content').innerHTML = `<div class="attribution-head"><span>RAW / DIAGNOSTIC</span><small>${esc(base || 'baseline')} → ${esc(candidateVersion || 'candidate')}</small></div>${view('Raw reliability', 'raw_reliability', 'Release Gate uses every failed Trial.')}${view('Agent quality', 'agent_quality', `Excludes ${candidate.agent_quality?.excluded_external_failure_count ?? 0} model / infra failures for diagnosis only.`)}<div class="failure-ledger">${['agent','model','infrastructure','evidence','policy'].map(kind=>`<span><b>${esc(String(counts[kind] ?? 0))}</b>${esc(kind)}</span>`).join('')}</div>`;
}
function pct(value) { return hasNumber(value) ? `${(Number(value) * 100).toFixed(1)}%` : 'N/A'; }
function evidencePair(label, before, after, formatter, lowerIsBetter) {
  const delta = hasNumber(before) && hasNumber(after) ? Number(after) - Number(before) : null;
  const verdict = delta === null ? 'flat' : deltaClass(delta, lowerIsBetter);
  const deltaText = delta === null ? 'N/A' : (label.includes('rate') || label.includes('Pass@')) ? `${delta > 0 ? '+' : ''}${(delta * 100).toFixed(1)}pp` : `${delta > 0 ? '+' : ''}${Math.round(delta)}`;
  return `<div class="evidence-row"><span>${esc(label)}</span><div class="evidence-values"><b class="baseline-value">${esc(formatter(before))}</b><i>→</i><b class="candidate-value">${esc(formatter(after))}</b></div><em class="${verdict}">${esc(deltaText)}</em></div>`;
}
function renderEvidenceDeck(dashboard = {}) {
  const comparison = state.experiment?.comparison || {};
  const reliability = comparison.reliability || state.experiment?.reliability || {};
  const efficiency = comparison.efficiency || state.experiment?.efficiency || {};
  const rb = reliability.baseline || {}, rc = reliability.candidate || {};
  const eb = efficiency.baseline || {}, ec = efficiency.candidate || {};
  const [base, candidate] = state.versions;
  const baseConsistency=rb.all_pass_at_k ?? rb.pass_at_k, candidateConsistency=rc.all_pass_at_k ?? rc.pass_at_k;
  document.querySelector('#stability-content').innerHTML = reliability.baseline ? `<div class="evidence-key"><span class="baseline-key">${esc(base || 'baseline')}</span><span class="candidate-key">${esc(candidate || 'candidate')}</span></div>${evidencePair('All-pass@3', baseConsistency, candidateConsistency, pct, false)}${evidencePair('Flaky case rate', rb.flaky_case_rate, rc.flaky_case_rate, pct, true)}<p class="evidence-note">All-pass@3 means all three repeats passed; it is a consistency metric, not standard Pass@3. ${esc(String(rc.eligible_case_count ?? 0))} Case groups had enough repeated Trials.</p>` : '<p class="empty big">Repeatability needs an experiment report with at least three Trials per Case.</p>';
  document.querySelector('#efficiency-content').innerHTML = efficiency.baseline ? `<div class="evidence-key"><span class="baseline-key">${esc(base || 'baseline')}</span><span class="candidate-key">${esc(candidate || 'candidate')}</span></div>${evidencePair('P50 latency', eb.p50_duration_ms, ec.p50_duration_ms, fmt, true)}${evidencePair('P95 latency', eb.p95_duration_ms, ec.p95_duration_ms, fmt, true)}${evidencePair('P50 model tokens', eb.p50_model_tokens, ec.p50_model_tokens, fmtTokens, true)}${evidencePair('P95 model tokens', eb.p95_model_tokens, ec.p95_model_tokens, fmtTokens, true)}<p class="evidence-note">Tail metrics expose slow or expensive Trials that average values can hide.</p>` : '<p class="empty big">Tail metrics are not available in this report.</p>';
  const cases = comparison.case_comparisons || state.experiment?.case_comparisons || [];
  const changed = cases.filter(item => consistency(item.baseline) !== consistency(item.candidate));
  const listed = (changed.length ? changed : cases).slice(0, 4);
  document.querySelector('#regression-content').innerHTML = cases.length ? `<div class="case-signal-list">${listed.map(item => { const before=consistency(item.baseline), after=consistency(item.candidate), stateClass=before===after?'flat':after?'positive':'negative'; return `<button type="button" class="case-signal ${stateClass}" data-evidence-case="${esc(item.case_id)}"><span>${esc(item.case_id.replaceAll('_',' '))}</span><b>${before === null || before === undefined ? 'N/A' : before ? '3/3' : 'not 3/3'} <i>→</i> ${after === null || after === undefined ? 'N/A' : after ? '3/3' : 'not 3/3'}</b><small>${esc(String(item.paired_trial_count || 0))} paired Trials</small></button>`; }).join('')}</div><p class="evidence-note">Select a Case to inspect every paired Trial, Trace, and Diff.</p>` : '<p class="empty big">No Case-level paired report is available.</p>';
  renderStatisticalEvidence(comparison.statistics || state.experiment?.statistics || {});
  renderBehaviorEvidence(comparison.behavior || state.experiment?.behavior || dashboard.behavior || {});
  renderPolicyStopEvidence();
  document.querySelectorAll('[data-evidence-case]').forEach(button => button.addEventListener('click', () => renderCaseDetail(button.dataset.evidenceCase, true)));
}
function renderPolicyStopEvidence() {
  const content = document.querySelector('#policy-stop-content'), evidence = state.policyStop || {};
  if (!evidence.available) {
    content.innerHTML = '<p class="empty big">No verification-stop policy Trace is available in this runtime.</p>';
    return;
  }
  const clean = Number(evidence.post_stop_model_or_tool_spans || 0) === 0 && Number(evidence.missing_policy_stop_count || 0) === 0;
  const status = clean ? 'INVARIANT HOLDS' : 'REVIEW REQUIRED';
  content.innerHTML = `<div class="policy-stop-verdict ${clean ? 'verified' : 'review'}"><span>${esc(status)}</span><b>${esc(String(evidence.policy_stop_trace_count || 0))} / ${esc(String(evidence.candidate_trial_count || 0))}</b><small>candidate traces stopped after verification</small></div><div class="policy-stop-grid"><div><span>verification passed</span><b>${esc(String(evidence.verification_passed_count || 0))}</b></div><div><span>post-stop model / tool spans</span><b>${esc(String(evidence.post_stop_model_or_tool_spans || 0))}</b></div><div><span>missing stop events</span><b>${esc(String(evidence.missing_policy_stop_count || 0))}</b></div></div><p class="evidence-note">The trace-level invariant is stronger than a lower average: after the exact verification command passes, V4.1 records a policy stop before it can request another model or tool action.</p>`;
}
function renderStatisticalEvidence(statistics) {
  const content = document.querySelector('#statistics-content');
  const metrics = statistics?.metrics || {};
  const latency = metrics.duration_ms;
  if (!latency?.available) {
    content.innerHTML = '<p class="empty big">Paired statistical evidence needs completed, valid Trials for both versions. It never substitutes for reliability or Gate evidence.</p>';
    return;
  }
  const interval = latency.ci95 || {};
  const cases = latency.case_outcomes || {};
  const fmtDelta = value => hasNumber(value) ? signed(Math.round(Number(value)), 'ms') : 'N/A';
  const conclusion = statistics.conclusion || {};
  const label = {observed_latency_improvement:'OBSERVED IMPROVEMENT',inconclusive:'INCONCLUSIVE',limited_coverage:'LIMITED COVERAGE',not_available:'NOT AVAILABLE'}[conclusion.level] || 'DIAGNOSTIC';
  content.innerHTML = `<div class="stat-verdict ${esc(conclusion.level || 'inconclusive')}"><span>${esc(label)}</span><b>${esc(fmtDelta(latency.point_estimate_median_delta))}</b><small>median paired latency delta</small></div><div class="stat-grid"><div><span>95% interval</span><b>${esc(fmtDelta(interval.low))} → ${esc(fmtDelta(interval.high))}</b></div><div><span>valid pairs</span><b>${esc(String(latency.paired_trial_count || 0))} / ${esc(String(statistics.eligible_case_count || 0))} Cases</b></div><div><span>Case outcomes</span><b>${esc(String(cases.candidate_lower || 0))} lower · ${esc(String(cases.candidate_higher || 0))} higher · ${esc(String(cases.tied || 0))} tie</b></div></div><p class="evidence-note">${esc(conclusion.reason || 'Clustered bootstrap resamples Cases, not individual repeats. Diagnostic only; it does not replace the Gate.')}</p>`;
}
function behaviorValue(value, availability, formatter = pct) { return availability && value !== null && value !== undefined ? formatter(value) : 'N/A'; }
function renderBehaviorEvidence(behavior) {
  const baseline = behavior.baseline, candidate = behavior.candidate;
  const pair = (label, key, availabilityKey, formatter = pct, lowerIsBetter = true) => {
    const before = baseline?.[key], after = candidate?.[key];
    const availableBefore = baseline?.availability?.[availabilityKey] ?? before !== null;
    const availableAfter = candidate?.availability?.[availabilityKey] ?? after !== null;
    if (!baseline || !candidate) return `<div class="behavior-row"><span>${esc(label)}</span><b>${esc(behaviorValue(behavior?.[key], behavior?.availability?.[availabilityKey], formatter))}</b></div>`;
    // 两侧没有同一类可用证据时，旧 Artifact 的遗留原始值不能构成可解释的 Delta。
    const delta = availableBefore && availableAfter && hasNumber(before) && hasNumber(after) ? Number(after)-Number(before) : null;
    return `<div class="behavior-row"><span>${esc(label)}</span><div><b class="baseline-value">${esc(behaviorValue(before, availableBefore, formatter))}</b><i>→</i><b class="candidate-value">${esc(behaviorValue(after, availableAfter, formatter))}</b></div><em class="${delta === null ? 'flat' : deltaClass(delta, lowerIsBetter)}">${delta === null ? 'N/A' : signed((delta*100).toFixed(1),'pp')}</em></div>`;
  };
  const content = document.querySelector('#behavior-content');
  if (!baseline && !candidate && !behavior?.instrumented_trial_count) {
    content.innerHTML = '<p class="empty big">No semantic Trace evidence in this runtime. Run an instrumented Trial to inspect tool discipline.</p>';
    return;
  }
  const count = baseline?.instrumented_trial_count ?? candidate?.instrumented_trial_count ?? behavior.instrumented_trial_count ?? 0;
  const availability = baseline?.availability || candidate?.availability || behavior.availability || {};
  const unavailableMetrics = Object.entries(baseline?.evidence_availability || candidate?.evidence_availability || {})
    .filter(([, state]) => state !== 'available').map(([field]) => field.replaceAll('_', ' '));
  const note = behavior.unavailable?.all_behavior_metrics || baseline?.unavailable?.all_behavior_metrics || candidate?.unavailable?.all_behavior_metrics
    || (unavailableMetrics.length ? `N/A: ${unavailableMetrics.join(', ')} evidence is unsupported or was not observed.` : null);
  content.innerHTML = `${baseline && candidate ? '<div class="evidence-key"><span class="baseline-key">baseline</span><span class="candidate-key">candidate</span></div>' : ''}${pair('Tool success rate','tool_success_rate','tool_outcomes',pct,false)}${pair('Repeated tool calls','repeated_tool_call_rate','repeated_tool_calls',pct,true)}${pair('Duplicate reads','duplicate_read_rate','duplicate_reads',pct,true)}${pair('Edit before read','edit_before_read_count','edit_before_read',v=>String(v),true)}<p class="evidence-note">${note ? esc(note) : `${esc(String(count))} instrumented Trial${count===1?'':'s'} · semantic fields are path/key/fingerprint only.`}</p>`;
}
function activeBehaviorDiff() { return state.experiment?.comparison?.behavior_diff || state.experiment?.behavior_diff || {}; }
function compact(value) { const number=Number(value); if (!Number.isFinite(number)) return 'N/A'; return Math.abs(number)>=1000 ? `${(number/1000).toFixed(1)}k` : String(Number.isInteger(number) ? number : Number(number.toFixed(2))); }
function summaryDirection(value) { return value === 'improved' ? 'positive' : value === 'regressed' ? 'negative' : 'flat'; }
function renderBehaviorRegressionSummary() {
  const content=document.querySelector('#behavior-regression-content'), diff=activeBehaviorDiff(), summary=diff.summary || {}, availability=diff.availability || {}, unavailable=diff.unavailable || {};
  const entries=[
    ['Model calls','metrics','model_calls'], ['Tool calls','metrics','tool_calls'], ['Tokens','metrics','total_tokens'], ['Latency','metrics','duration_ms'],
    ['Duplicate read','patterns','duplicate_read'], ['Repeated tool call','patterns','repeated_tool_call'],
  ];
  if (!availability.behavior_diff) { content.innerHTML=`<p class="empty big">${esc(unavailable.behavior_diff || 'No paired Trial Behavior Snapshots are available for this Experiment.')}</p>`; return; }
  const row=([label,group,key])=>{const item=summary[group]?.[key]; if (!item) return `<div class="behavior-summary-row unavailable"><span>${esc(label)}</span><b>N/A</b><small>${esc(unavailable[key] || 'paired evidence unavailable')}</small></div>`; const metric=item.median_delta; const detail=group==='metrics' ? `median Δ ${compact(metric)}` : 'pattern count across paired Trials'; return `<div class="behavior-summary-row ${summaryDirection(item.regressed_cases ? 'regressed' : item.improved_cases ? 'improved' : 'unchanged')}"><span>${esc(label)}</span><b>${esc(String(item.improved_cases))} improved <i>/</i> ${esc(String(item.unchanged_cases))} unchanged <i>/</i> ${esc(String(item.regressed_cases))} regressed</b><small>${esc(detail)} · ${esc(String(item.available_case_count))} Cases</small></div>`;};
  content.innerHTML=`<div class="behavior-summary-list">${entries.map(row).join('')}</div><p class="evidence-note">Behavior Diff is diagnostic evidence only. It never changes the promotion Gate.</p>`;
}
function renderMatrix() {
  const [base, candidate] = state.versions; const grouped = new Map();
  state.trials.forEach(t => { const k=t.case_id || t.trial_id; if (!grouped.has(k)) grouped.set(k, []); grouped.get(k).push(t); });
  const rows = [...grouped.entries()].sort(([a],[b])=>a.localeCompare(b)).map(([caseId, items]) => {
    const left=summarize(items.filter(x=>x.agent_version===base)), right=summarize(items.filter(x=>x.agent_version===candidate));
    const delta=hasNumber(left.duration)&&hasNumber(right.duration)?right.duration-left.duration:null; const passDelta=right.pass-left.pass;
    const status = right.pass > left.pass ? 'improved' : right.pass < left.pass ? 'regressed' : 'stable';
    const primary = caseId === primaryCaseId();
    return `<button class="comparison-row ${status} ${primary?'primary-case':''} ${state.selectedCase===caseId?'selected':''}" data-case="${esc(caseId)}" aria-pressed="${state.selectedCase===caseId}" type="button"><span class="case-name">${primary?'<i class="investigate-tag">INVESTIGATE</i>':''}${esc(caseId.replaceAll('_',' '))}<small>${left.count || 0} × ${candidate ? 2 : 1} versions</small></span>${summaryCell(left, base)}<span class="delta-cell ${delta===null?'flat':deltaClass(delta)}"><b>${signed(passDelta)}</b><small>pass</small><em>${delta===null?'N/A':signed(Math.round(delta),'ms')}</em></span>${summaryCell(right, candidate)}</button>`;
  });
  document.querySelector('#comparison-rows').innerHTML = rows.join('') || '<p class="empty big">No paired Trial artifacts available.</p>';
  document.querySelectorAll('.comparison-row').forEach(row => row.addEventListener('click', () => renderCaseDetail(row.dataset.case, true)));
}
function summaryCell(s, version) { const note=s.failures[0] ? s.failures[0].replaceAll('_',' ') : 'trace valid'; return `<span class="version-cell"><b>${s.pass}/${s.count || 0}</b><small>${esc(version || 'unknown')}</small><em>${fmt(s.duration)} · ${fmtTokens(s.tokens)} · ${fmtTools(s.tools)}</em><i class="${s.failures.length?'bad-text':'ok-text'}">${esc(note)}</i></span>`; }
function caseIds() { return [...new Set(state.trials.map(t => t.case_id).filter(Boolean))].sort((a,b) => a.localeCompare(b)); }
function trialNumber(row) { return String(row.trial_id || row.id || "").split("_trial_").pop() || "?"; }
function renderCaseDetail(caseId, shouldScroll = false) {
  state.selectedCase=caseId; const panel=document.querySelector('#case-detail-panel'); panel.hidden=false; document.querySelector('#case-detail-title').textContent=caseId.replaceAll('_',' ');
  const picker=document.querySelector('#case-select'); picker.innerHTML=caseIds().map(id=>`<option value="${esc(id)}">${esc(id.replaceAll('_',' '))}</option>`).join(''); picker.value=caseId;
  document.querySelectorAll('.metric-tabs [data-metric]').forEach(tab=>{const selected=tab.dataset.metric===state.metric;tab.setAttribute('aria-selected',String(selected));tab.classList.toggle('active',selected);});
  renderCaseDiagnosis(caseId); renderCaseChart(caseId); renderCaseSummary(caseId); renderCaseBehavior(caseId);
  const [base, candidate]=state.versions;
  const rows=state.trials.filter(t=>t.case_id===caseId), grouped=new Map();
  rows.forEach(row=>{const key=trialNumber(row);if(!grouped.has(key))grouped.set(key,{});grouped.get(key)[row.agent_version]=row;});
  const trialCard=(row, role)=>row ? `<button type="button" data-trial="${encodeURIComponent(row.id)}" class="paired-trial-card ${role} ${row.passed?'pass':'fail'}"><span>${esc(role.toUpperCase())}</span><strong>${row.passed?'PASS':esc(String(row.failure_reason || row.status || 'NEEDS REVIEW').replaceAll('_',' '))}</strong><small>${fmt(row.duration_ms)} · ${fmtTokens(row.model_tokens)} · ${fmtTools(row.tool_calls)}</small></button>` : `<div class="paired-trial-card missing"><span>${esc(role.toUpperCase())}</span><strong>MISSING</strong><small>No artifact for this side.</small></div>`;
  const pairs=[...grouped.entries()].sort(([a],[b])=>Number(a)-Number(b)||a.localeCompare(b)).map(([index,pair])=>{const left=pair[base],right=pair[candidate],different=left?.passed!==right?.passed, behaviorPair=(activeBehaviorDiff().deltas || []).find(item=>item.case_id===caseId&&String(item.trial_index)===String(Number(index))); const patternItems=[...(behaviorPair?.removed_patterns || []).map(item=>`<span class="positive">✓ ${esc(item.pattern)} ${esc(String(item.delta))}</span>`),...(behaviorPair?.added_patterns || []).map(item=>`<span class="negative">! ${esc(item.pattern)} +${esc(String(item.delta))}</span>`)]; const behaviorNote=behaviorPair?`<div class="pair-behavior-delta"><b>BEHAVIOR Δ</b>${patternItems.length?patternItems.join(''):'<span class="flat">No semantic pattern change</span>'}</div>`:''; const traceButton=left&&right?`<button type="button" data-trace-diff="${encodeURIComponent(left.id)}|${encodeURIComponent(right.id)}">Compare traces</button>`:'';return `<section class="paired-trial-row ${different?'behavior-difference':''}"><div class="pair-index"><span>TRIAL</span><strong>${esc(String(index).padStart(3,'0'))}</strong>${different?'<small>OUTCOME DIFFERENCE</small>':''}${traceButton}</div>${trialCard(left,'baseline')}<div class="pair-arrow" aria-hidden="true">→</div>${trialCard(right,'candidate')}${behaviorNote}</section>`;}).join('');
  document.querySelector('#case-detail-content').innerHTML=pairs || '<p class="empty big">No paired Trial artifacts for this Case.</p>';
  document.querySelectorAll('[data-trial]').forEach(b=>b.addEventListener('click',()=>showTrial(decodeURIComponent(b.dataset.trial))));
  document.querySelectorAll('[data-trace-diff]').forEach(button=>button.addEventListener('click',()=>{const [baseline,candidate]=button.dataset.traceDiff.split('|').map(decodeURIComponent); showTraceDiff(baseline,candidate);}));
  renderMatrix();
  if (shouldScroll) panel.scrollIntoView({behavior:'smooth',block:'nearest'});
}
async function showTraceDiff(baseline, candidate) {
  try {
    const response=await api(`/api/trace-diff?baseline=${encodeURIComponent(baseline)}&candidate=${encodeURIComponent(candidate)}`);
    if(response.available!==true) return;
    const diff=response.data||{}, first=diff.first_divergence, critical=diff.critical_path||{}, failure=diff.failure_alignment||{};
    const describe=span=>span ? `${span.span_type||'span'} · ${span.name||'unknown'} · ${fmt(span.duration_ms)} · ${fmtTokens(span.tokens)} · ${fmtTools(span.tool_calls)}` : '—';
    const delta=row=>row.delta ? `Δ ${fmt(row.delta.duration_ms)} · ${fmtTokens(row.delta.tokens)} · ${fmtTools(row.delta.tool_calls)}` : row.kind.toUpperCase();
    const rows=(diff.rows||[]).map(row=>`<div class="trace-diff-row ${esc(row.kind)}" style="--trace-depth:${Number(row.depth)||0}"><span>${esc(describe(row.baseline))}</span><b>${esc(delta(row))}</b><span>${esc(describe(row.candidate))}</span></div>`).join('');
    document.querySelector('#trace-diff-panel').hidden=false;
    document.querySelector('#trace-diff-summary').textContent=`${diff.matched_span_count||0} aligned · ${first?`first divergence: ${first.kind} ${first.baseline?.name||first.candidate?.name}`:'no structural divergence'} · critical path ${fmt(critical.baseline?.duration_ms)} → ${fmt(critical.candidate?.duration_ms)} · failure spans ${failure.aligned?'aligned':'not aligned'}`;
    document.querySelector('#trace-diff-rows').innerHTML=rows || '<p class="empty">No Trace spans available for alignment.</p>';
    document.querySelector('#trace-diff-panel').scrollIntoView({behavior:'smooth',block:'nearest'});
  } catch {}
}
function renderCaseDiagnosis(caseId) {
  const report=caseComparisons().find(item=>item.case_id===caseId), pairs=report?.paired_trials || [];
  const mismatch=pairs.find(pair=>pair.baseline?.valid_pass!==pair.candidate?.valid_pass);
  const failed=state.trials.find(row=>row.case_id===caseId&&!row.passed);
  const diagnosis=document.querySelector('#case-diagnosis');
  if (!mismatch && !failed) {
    diagnosis.innerHTML='<span class="diagnosis-status stable">STABLE OUTCOME</span><p>Both versions reached the same validity outcome. Use paired bars to inspect efficiency variance.</p>';
    return;
  }
  const side=mismatch?.baseline?.valid_pass===false?'Baseline':'Candidate';
  const reason=failed?.failure_reason || failed?.status || 'validity mismatch';
  diagnosis.innerHTML=`<span class="diagnosis-status investigate">INVESTIGATE TRIAL ${esc(String(mismatch?.trial_index || trialNumber(failed)).padStart(3,'0'))}</span><p><b>${esc(side)}</b> failed the valid-pass boundary: <strong>${esc(reason.replaceAll('_',' '))}</strong>. The paired counterpart passed; open both artifacts below to compare tool behavior.</p>`;
}
function renderCaseChart(caseId) {
  const metric=METRICS[state.metric], [base,candidate]=state.versions, rows=state.trials.filter(t=>t.case_id===caseId);
  const groups=new Map(); rows.forEach(row=>{const key=trialNumber(row);if(!groups.has(key))groups.set(key,{});groups.get(key)[row.agent_version]=row;});
  const ordered=[...groups.keys()].sort((a,b)=>Number(a)-Number(b)||a.localeCompare(b));
  const values=rows.map(row=>row[state.metric]).filter(hasNumber).map(Number).filter(v=>v>0), max=Math.max(...values,1);
  const bar=(row, version)=>{const raw=row?.[state.metric],value=Number(raw);const available=hasNumber(raw)&& (value>0 || (state.metric==='tool_calls' && row?.passed));const height=available?Math.max(5,Math.round(value/max*100)):0;const failed=row&&!row.passed;return `<div class="chart-bar-wrap"><span class="chart-value">${available?esc(metric.format(value)):'N/A'}</span><div class="chart-bar ${version===base?'baseline-bar':'candidate-bar'} ${failed?'failed-bar':''} ${available?'':'missing-bar'}" style="--bar-height:${height}%"><span>${esc(version||'—')}</span></div></div>`;};
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
function snapshotMedian(snapshots, key, pattern=false) { const values=snapshots.map(snapshot=>pattern ? snapshot?.patterns?.[key] : snapshot?.[key]).filter(hasNumber).map(Number); return values.length ? median(values) : null; }
function snapshotTotal(snapshots, key) { const values=snapshots.map(snapshot=>snapshot?.patterns?.[key]).filter(hasNumber).map(Number); return values.length ? values.reduce((total,value)=>total+value,0) : null; }
function caseSnapshots(caseId) {
  const baselineId=state.experiment?.baseline_id, candidateId=state.experiment?.candidate_id, summaries=state.experiment?.summaries || {};
  const snapshots=id=>(summaries[id]?.jobs || []).filter(job=>job.case_id===caseId).map(job=>job.behavior_snapshot).filter(snapshot=>snapshot&&typeof snapshot==='object');
  return { baseline:snapshots(baselineId), candidate:snapshots(candidateId) };
}
function behaviorMetricValue(value, kind) { if (value===null || value===undefined) return 'N/A'; if (kind==='tokens') return `${compact(value)} tok`; if (kind==='latency') return fmt(value); if (kind==='rate') return `${(Number(value)*100).toFixed(0)}%`; return compact(value); }
function renderCaseBehavior(caseId) {
  const diff=activeBehaviorDiff(), caseDiff=(diff.case_diffs || []).find(item=>item.case_id===caseId), snapshots=caseSnapshots(caseId), comparison=(state.experiment?.comparison?.case_comparisons || state.experiment?.case_comparisons || []).find(item=>item.case_id===caseId);
  const metrics=document.querySelector('#case-behavior-metrics'), changes=document.querySelector('#case-behavior-changes'), failure=document.querySelector('#case-failure-evidence');
  if (!caseDiff) { metrics.innerHTML='<p class="empty">No paired Behavior Snapshot for this Case.</p>'; changes.innerHTML=''; failure.innerHTML=''; return; }
  const rows=[['Pass',null,'pass'],['Model calls','model_calls','count'],['Tool calls','tool_calls','count'],['Tokens','total_tokens','tokens'],['Latency','duration_ms','latency'],['Duplicate reads (total)','duplicate_read','pattern'],['Repeated calls (total)','repeated_tool_call','pattern'],['Tool errors (total)','failed_tool_call','pattern']];
  const passValue=side=>{const item=comparison?.[side] || {}; return `${item.valid_pass_count ?? '—'}/${item.trial_count ?? '—'}`;};
  const metricRow=([label,key,kind])=>{if(kind==='pass') return `<div class="case-behavior-row"><span>${label}</span><b>${esc(passValue('baseline'))}</b><b>${esc(passValue('candidate'))}</b><em class="flat">—</em></div>`; const item=kind==='pattern'?caseDiff.patterns?.[key]:caseDiff.metrics?.[key]; const before=kind==='pattern'?snapshotTotal(snapshots.baseline,key):snapshotMedian(snapshots.baseline,key), after=kind==='pattern'?snapshotTotal(snapshots.candidate,key):snapshotMedian(snapshots.candidate,key); const delta=item?.delta ?? item?.median_delta; const cls=summaryDirection(item?.classification); return `<div class="case-behavior-row"><span>${esc(label)}</span><b>${esc(behaviorMetricValue(before,kind))}</b><b>${esc(behaviorMetricValue(after,kind))}</b><em class="${cls}">${delta===null||delta===undefined?'N/A':esc(`${Number(delta)>0?'+':''}${compact(delta)}`)}</em></div>`;};
  metrics.innerHTML=`<div class="case-behavior-table-head"><span>METRIC</span><span>BASELINE</span><span>CANDIDATE</span><span>PAIRED Δ</span></div>${rows.map(metricRow).join('')}<p class="case-behavior-note">Metrics use each version’s Trial median; paired Δ is the median of same-index Trial deltas. Pattern rows use totals across all paired Trials.</p>`;
  const patternEntries=Object.entries(caseDiff.patterns || {}), improved=patternEntries.filter(([,item])=>item.classification==='improved'), regressed=patternEntries.filter(([,item])=>item.classification==='regressed'), unavailable=patternEntries.filter(([,item])=>item.classification==='not_available');
  const patternList=(title,items,cls)=>`<div class="behavior-change-group ${cls}"><span>${title}</span>${items.length?items.map(([name,item])=>`<b>${cls==='positive'?'✓':cls==='negative'?'!':'–'} ${esc(name)} <i>${item.delta===null||item.delta===undefined?'N/A':`${item.delta>0?'+':''}${item.delta}`}</i></b>`).join(''):'<small>None</small>'}</div>`;
  changes.innerHTML=`${patternList('Improved',improved,'positive')}${patternList('Regressed',regressed,'negative')}${patternList('Unavailable',unavailable,'unavailable')}${unavailable.length?'<p class="evidence-note">N/A means the required Trace span was not emitted; it is never treated as zero.</p>':''}`;
  const failures=state.trials.filter(row=>row.case_id===caseId&&!row.passed&&row.failure_kind&&row.failure_kind!=='passed');
  failure.innerHTML=failures.length?failures.map(row=>{const span=row.failure_span || {}, evidence=row.failure_evidence || {}; return `<article class="failure-evidence"><span>${esc(String(row.agent_version || 'trial').toUpperCase())} · Trial ${esc(trialNumber(row))}</span><strong>${esc(row.failure_kind)} / ${esc(row.failure_reason)}</strong><small>${span.name?`Span ${esc(span.name)}${span.tool_name?` · ${esc(span.tool_name)}`:''}${span.span_id?` · ${esc(span.span_id)}`:''}`:'No deterministic Span match'}</small>${evidence.target_path?`<small>Path · ${esc(evidence.target_path)}</small>`:''}<button type="button" data-failure-trial="${encodeURIComponent(row.id)}">View Trace Evidence →</button></article>`;}).join(''):'<p class="empty">No failing Trial in this Case.</p>';
  document.querySelectorAll('[data-failure-trial]').forEach(button=>button.addEventListener('click',()=>showTrial(decodeURIComponent(button.dataset.failureTrial))));
}
function renderTriage() { const vf=document.querySelector('#version-filter'); const known=[...new Set(state.trials.map(t=>t.agent_version).filter(Boolean))]; vf.innerHTML='<option value="all">All</option>'+known.map(v=>`<option value="${esc(v)}">${esc(v)}</option>`).join(''); applyFilters(); }
function applyFilters() { const version=document.querySelector('#version-filter').value, outcome=document.querySelector('#outcome-filter').value; const rows=state.trials.filter(t=>(version==='all'||t.agent_version===version)&&(outcome==='all'||(outcome==='pass'?t.passed:!t.passed))); document.querySelector('#trial-rows').innerHTML=rows.map(t=>`<tr class="trial-row" tabindex="0" data-id="${encodeURIComponent(t.id)}"><td><b>${esc(t.case_id)}</b><small>${esc(t.trial_id)}</small></td><td>${esc(t.agent_version)}</td><td><span class="status ${t.passed?'ok':'bad'}">${t.passed?'PASS':esc(t.status)}</span></td><td>${fmtTools(t.tool_calls)}</td><td>${fmt(t.duration_ms)}</td><td>${fmtTokens(t.model_tokens)}</td></tr>`).join('')||'<tr><td colspan="6" class="empty">No matching Trial.</td></tr>'; document.querySelectorAll('.trial-row').forEach(r=>{r.onclick=()=>showTrial(decodeURIComponent(r.dataset.id));r.onkeydown=e=>{if(e.key==='Enter')r.click();};}); }
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
function traceTreeRows(events) {
  const visible = events.filter(e => !(e.kind === 'span_end' && e.span_id && events.some(start => start.kind === 'span_start' && start.span_id === e.span_id)));
  const starts = new Set(events.filter(e => e.kind === 'span_start' && e.span_id).map(e => e.span_id));
  const nodes = visible.map((event, index) => ({ event, key: `${event.event_seq ?? index}:${event.span_id || event.name || 'event'}`, parent: event.parent_span_id || event.parent_id || null, children: [] }));
  const spans = new Map(nodes.filter(node => node.event.kind === 'span_start' && node.event.span_id).map(node => [node.event.span_id, node]));
  const roots = [];
  const reaches = (node, parent) => { for (let current = parent; current; current = spans.get(current.parent)) if (current === node) return true; return false; };
  nodes.forEach(node => {
    const parent = node.parent && spans.get(node.parent);
    if (parent && parent !== node && !reaches(node, parent)) parent.children.push(node);
    else roots.push({ ...node, orphan: Boolean(node.parent && !starts.has(node.parent)) });
  });
  const render = (node, depth) => {
    const event = node.event, attrs = event.attributes || {}, isTool = event.name === 'tool.call' || Boolean(attrs.tool_name);
    const ends = events.find(item => item.kind === 'span_end' && item.span_id && item.span_id === event.span_id);
    const endAttrs = ends?.attributes || {}, meta = [], duration = endAttrs.duration_ms ?? attrs.duration_ms;
    if (Number.isFinite(Number(duration))) meta.push(`${Number(duration).toFixed(1)}ms`);
    if (ends?.status || event.status) meta.push(ends?.status || event.status);
    if (attrs.model) meta.push(String(attrs.model));
    const label = isTool ? (attrs.tool_name || 'unnamed tool') : (event.name || event.span_id || 'event');
    const preview = endAttrs.output_preview || attrs.output_preview;
    const expandable = node.children.length > 0;
    const toggle = expandable ? `<button type="button" class="trace-tree-toggle" data-trace-node="${esc(node.key)}" aria-expanded="true">⌄</button>` : '<span class="trace-tree-stem"></span>';
    const childRows = node.children.map(child => render(child, depth + 1)).join('');
    return `<li class="trace-event trace-tree-event ${isTool?'tool-event':''} ${node.orphan?'orphan-event':''}" data-trace-branch="${esc(node.key)}" style="--trace-depth:${depth}">${toggle}<span class="trace-kind">${esc(isTool?'TOOL':String(event.kind || 'EVENT').replace('span_start','SPAN'))}</span><span class="trace-body"><strong class="trace-name">${esc(label)}</strong>${meta.length?`<span class="trace-meta">${esc(meta.join(' · '))}</span>`:''}${preview?`<span class="trace-preview">${esc(String(preview).replace(/\s+/g,' ').slice(0,180))}</span>`:''}${node.orphan?'<span class="trace-orphan">missing parent span</span>':''}</span>${childRows ? `<ol class="trace-tree-children">${childRows}</ol>` : ''}</li>`;
  };
  return roots.map(node => render(node, 0));
}
function renderTrace() {
  const tabs = document.querySelectorAll('[data-trace-view]'), tree = state.traceView === 'tree';
  tabs.forEach(tab => tab.setAttribute('aria-selected', String(tab.dataset.traceView === state.traceView)));
  const rows = tree ? traceTreeRows(state.traceEvents) : traceRows(state.traceEvents);
  document.querySelector('#trace-list').classList.toggle('trace-tree', tree);
  document.querySelector('#trace-list').innerHTML = rows.join('') || '<li class="empty">No trace artifact.</li>';
  document.querySelectorAll('[data-trace-node]').forEach(button => button.addEventListener('click', () => {
    const branch = document.querySelector(`[data-trace-branch="${CSS.escape(button.dataset.traceNode)}"]`);
    const expanded = button.getAttribute('aria-expanded') !== 'true';
    button.setAttribute('aria-expanded', String(expanded));
    branch?.classList.toggle('collapsed', !expanded);
  }));
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
async function showTrial(id) { if (!state.runtimeAvailable) return; let response; try { response=await api(`/api/trials/${encodeURIComponent(id)}`); } catch { return; } if (response.available !== true || !sameContext(state.context, response.context)) return; const d=response.data || {}, r=d.result, behavior=r.behavior||{}, capabilities=behavior.adapter_capabilities||{}, unavailable=Object.entries(behavior.unavailable||{}), provenance=r.evidence_provenance||behavior.evidence_provenance||{}; const supported=Object.entries(capabilities).filter(([key,value])=>key!=='schema_version'&&value===true).map(([key])=>key.replaceAll('_',' ')); const capabilityChip=supported.length?`capability ${behavior.capability_source||'snapshot'}: ${supported.join(', ')}`:`capability ${behavior.capability_source||'unavailable'}`; const origins=[...new Set(Object.values(provenance).filter(value=>value&&value!=='not_observed'))]; const originChip=origins.length?`evidence ${origins.join(' · ')}`:'evidence not observed'; const evidenceChips=unavailable.slice(0,2).map(([metric,reason])=>`N/A ${metric}: ${reason}`); const modelUsage=capabilities.model_usage===true&&hasNumber(r.model_usage?.total_tokens)?`${Math.round(Number(r.model_usage.total_tokens)).toLocaleString()} tokens`:'N/A tokens'; document.querySelector('#detail-empty').hidden=true; document.querySelector('#detail').hidden=false; document.querySelector('#trace-diff-panel').hidden=true; document.querySelector('#detail-title').textContent=r.trial_id; const s=document.querySelector('#detail-status'); s.textContent=r.status; s.className=`status ${r.evaluation?.passed?'ok':'bad'}`; document.querySelector('#detail-stats').innerHTML=[`agent ${r.agent_version}`,`profile ${r.agent_profile||'default'}`,originChip,capabilityChip,...evidenceChips,modelUsage,`${r.changed_files?.length||0} files`].map(x=>`<span class="chip">${esc(x)}</span>`).join(''); state.traceEvents=d.trace || []; renderTrace(); renderDiff(r.git_diff, r.changed_files); document.querySelector('.detail-panel').scrollIntoView({behavior:'smooth',block:'nearest'}); }
document.querySelector('#refresh').onclick=load; document.querySelector('#version-filter').onchange=applyFilters; document.querySelector('#outcome-filter').onchange=applyFilters; document.querySelector('#case-select').onchange=e=>renderCaseDetail(e.target.value, false); document.querySelectorAll('.metric-tabs [data-metric]').forEach(tab=>tab.onclick=()=>{state.metric=tab.dataset.metric;if(state.selectedCase)renderCaseDetail(state.selectedCase,false);}); document.querySelectorAll('[data-trace-view]').forEach(tab=>tab.onclick=()=>{state.traceView=tab.dataset.traceView;renderTrace();}); document.querySelector('#close-case').onclick=()=>{document.querySelector('#case-detail-panel').hidden=true;state.selectedCase=null;renderMatrix();}; load();

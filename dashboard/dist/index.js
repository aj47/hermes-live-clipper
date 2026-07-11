(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!SDK || !registry) return;

  const { React, authedFetch } = SDK;
  const h = React.createElement;
  const { useEffect } = SDK.hooks;
  const api = "/api/plugins/hermes-live-clipper";
const state = { selected: null, detail: null, activeTab: "analyst", preview: null, lastStatus: null, notice: "", publisherTasks: {}, activityCandidate: null, selectedJobs: new Set(), selectedCandidates: new Set(), selectedRenders: new Set(), cleanupPlan: null };
const publisherBoard = "live-clipper-publishing";

const el = (tag, props = {}, children = []) => {
  const node = document.createElement(tag);
  Object.entries(props).forEach(([key, value]) => key === "class" ? node.className = value : key.startsWith("on") ? node.addEventListener(key.slice(2).toLowerCase(), value) : node.setAttribute(key, value));
  for (const child of [].concat(children)) node.append(child instanceof Node ? child : document.createTextNode(String(child)));
  return node;
};

async function request(path, options = {}) {
  const response = await authedFetch(api + path, {headers: {"Content-Type":"application/json"}, ...options});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Request failed (${response.status})`);
  return response.status === 204 ? null : response.json();
}

async function kanbanRequest(path, options = {}) {
  const response = await authedFetch(`/api/plugins/kanban${path}`, {headers:{"Content-Type":"application/json"}, ...options});
  if (!response.ok) throw new Error((await response.json().catch(() => ({}))).detail || `Hermes task request failed (${response.status})`);
  return response.status === 204 ? null : response.json();
}

async function loadPublisherTasks(renders = []) {
  const tracked = renders.filter(item => item.publisher_task_id);
  await Promise.all(tracked.map(async item => {
    try {
      const result = await kanbanRequest(`/tasks/${encodeURIComponent(item.publisher_task_id)}?board=${encodeURIComponent(publisherBoard)}`);
      state.publisherTasks[item.id] = result.task || result;
    } catch (error) {
      state.publisherTasks[item.id] = {status:"unavailable", error:error.message};
    }
  }));
}

function formatBytes(bytes) {
  if (!bytes) return "0 B";
  const units = ["B","KB","MB","GB","TB"]; let index = 0;
  while (bytes >= 1024 && index < units.length - 1) { bytes /= 1024; index++; }
  return `${bytes.toFixed(index ? 1 : 0)} ${units[index]}`;
}

function formatDuration(seconds) {
  if (seconds == null) return "—";
  const total = Math.round(Number(seconds));
  return `${Math.floor(total / 60)}:${String(total % 60).padStart(2, "0")}`;
}

function formatActivityTime(value) {
  if (!value) return "just now";
  const normalized = value.includes("T") ? value : `${value.replace(" ", "T")}Z`;
  const date = new Date(normalized);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString([], {month:"short",day:"numeric",hour:"numeric",minute:"2-digit"});
}

function oneSentence(value, fallback) {
  const text = String(value || fallback || "").replace(/\s+/g, " ").trim();
  const first = text.match(/^.*?[.!?](?:\s|$)/)?.[0]?.trim() || text;
  const short = first.length > 180 ? `${first.slice(0,177).replace(/\s+\S*$/, "")}…` : first;
  return short && !/[.!?…]$/.test(short) ? `${short}.` : short;
}

function selectionSet(kind) {
  return kind === "job" ? state.selectedJobs : kind === "candidate" ? state.selectedCandidates : state.selectedRenders;
}

function toggleSelection(kind, id, checked) {
  const selected = selectionSet(kind);
  checked ? selected.add(id) : selected.delete(id);
  state.cleanupPlan = null;
  render(state.lastStatus);
}

function selectionCheckbox(kind, id, label, disabled = false) {
  const input = el("input",{type:"checkbox","aria-label":label,...(selectionSet(kind).has(id)?{checked:"true"}:{}),...(disabled?{disabled:"true"}:{}),onchange:event=>toggleSelection(kind,id,event.target.checked)});
  return el("label",{class:`selection-check ${kind}-selection ${disabled?"disabled":""}`,title:disabled?"Stop active work before deleting it":label},[input,el("span",{"aria-hidden":"true"})]);
}

function selectionPayload(forcePublisherAssets = false) {
  return {
    job_ids:[...state.selectedJobs],
    candidate_ids:[...state.selectedCandidates],
    render_ids:[...state.selectedRenders],
    force_publisher_assets:forcePublisherAssets,
  };
}

function selectedCount() {
  return state.selectedJobs.size + state.selectedCandidates.size + state.selectedRenders.size;
}

function clearSelection() {
  state.selectedJobs.clear();
  state.selectedCandidates.clear();
  state.selectedRenders.clear();
  state.cleanupPlan = null;
}

async function reviewCleanup(forcePublisherAssets = false) {
  state.cleanupPlan = await request("/cleanup/preview",{method:"POST",body:JSON.stringify(selectionPayload(forcePublisherAssets))});
  render(state.lastStatus);
}

async function executeCleanup() {
  const plan = state.cleanupPlan;
  if (!plan || plan.blocked.length) return;
  const total = Object.values(plan.counts).reduce((sum,value)=>sum+Number(value||0),0);
  if (!window.confirm(`Permanently delete this selection and reclaim ${formatBytes(plan.reclaimable_bytes)}? This removes media files and cannot be undone.`)) return;
  const deletedSelectedJob = plan.selection.job_ids.includes(state.selected);
  const result = await request("/cleanup/execute",{method:"POST",body:JSON.stringify({...selectionPayload(plan.force_publisher_assets),expected_bytes:plan.reclaimable_bytes})});
  if (state.preview?.url) URL.revokeObjectURL(state.preview.url);
  state.preview = null;
  state.activityCandidate = null;
  clearSelection();
  if (deletedSelectedJob) { state.selected = null; state.detail = null; }
  showNotice(`Deleted ${total} database records and reclaimed ${formatBytes(result.reclaimable_bytes)}.`);
  await refresh();
}

async function refresh({ passive = false } = {}) {
  try {
    const currentPlayer = document.querySelector(".clip-player video");
    if (passive && currentPlayer && !currentPlayer.paused && !currentPlayer.ended) return;
    const playback = currentPlayer ? { time: currentPlayer.currentTime, paused: currentPlayer.paused } : null;
    const status = await request("/status");
    if (!state.selected && status.jobs.length) state.selected = status.jobs[0].id;
    if (state.selected) {
      state.detail = await request(`/jobs/${state.selected}`);
      await loadPublisherTasks(state.detail.renders);
    }
    render(status);
    const nextPlayer = document.querySelector(".clip-player video");
    if (playback && nextPlayer) {
      nextPlayer.currentTime = playback.time;
      if (!playback.paused) nextPlayer.play().catch(() => {});
    }
  } catch (error) { showError(error.message); }
}

function showError(message) {
  document.querySelector("#hlc-error")?.replaceChildren(message);
}

function showNotice(message) {
  state.notice = message;
  document.querySelector("#hlc-notice")?.replaceChildren(message);
}

function render(status) {
  state.lastStatus = status;
  const root = document.querySelector("#hermes-live-clipper");
  if (!root) return;
  root.replaceChildren(
    el("header", {class:"hero"}, [el("div", {}, [el("p", {class:"eyebrow"}, "HERMES MEDIA WORKER"), el("h1", {}, "Live Clipper"), el("p", {class:"dek"}, "Turn a public livestream into transcript-grounded draft clips while it is still live.")]), resourcePills(status)]),
    el("section", {class:"submit-card"}, [jobForm(), el("p", {class:"legal"}, "Only clip content you are authorized to use. Public availability does not grant redistribution rights."), el("p", {id:"hlc-notice", class:"success"}, state.notice), el("p", {id:"hlc-error", class:"error"})]),
    el("div", {class:"workspace"}, [jobList(status.jobs), detailPanel()]),
    bulkSelectionBar(),
    cleanupReview()
  );
}

function bulkSelectionBar() {
  const count = selectedCount();
  if (!count) return "";
  return el("aside",{class:"bulk-bar","aria-label":"Bulk actions"},[el("div",{},[el("strong",{},`${count} selected`),el("span",{},`${state.selectedJobs.size} streams · ${state.selectedCandidates.size} suggestions · ${state.selectedRenders.size} renders`)]),el("button",{class:"ghost",onclick:()=>{clearSelection();render(state.lastStatus);}},"Clear"),el("button",{onclick:()=>reviewCleanup().catch(error=>showError(error.message))},"Review deletion")]);
}

function cleanupReview() {
  const plan = state.cleanupPlan;
  if (!plan) return "";
  const publisherBlocks = plan.blocked.filter(item=>item.kind==="publisher_asset");
  const hardBlocks = plan.blocked.filter(item=>item.kind!=="publisher_asset");
  const hasPublisherAssets = Number(plan.counts.publisher_handoffs||0)>0;
  return el("div",{class:"cleanup-scrim",role:"dialog","aria-modal":"true","aria-label":"Review deletion"},[el("section",{class:"cleanup-review"},[el("div",{class:"cleanup-head"},[el("div",{},[el("p",{class:"eyebrow"},"STORAGE CLEANUP"),el("h2",{},"Review permanent deletion"),el("p",{class:"muted"},"Reject remains non-destructive. This action removes the selected media and database records.")]),el("button",{class:"ghost",onclick:()=>{state.cleanupPlan=null;render(state.lastStatus);},"aria-label":"Close deletion review"},"Close")]),el("div",{class:"reclaim-number"},[el("strong",{},formatBytes(plan.reclaimable_bytes)),el("span",{},"reclaimable")]),el("div",{class:"cleanup-counts"},Object.entries(plan.counts).filter(([,value])=>value).map(([key,value])=>el("div",{},[el("strong",{},value),el("span",{},key.replaceAll("_"," "))]))),plan.warnings.map(message=>el("p",{class:"cleanup-warning"},message)),hardBlocks.map(item=>el("p",{class:"cleanup-blocked"},item.message)),hasPublisherAssets?el("label",{class:"force-cleanup"},[el("input",{type:"checkbox",...(plan.force_publisher_assets?{checked:"true"}:{}),onchange:event=>reviewCleanup(event.target.checked).catch(error=>showError(error.message))}),el("span",{},[el("strong",{},"Also delete publisher assets"),el("small",{},publisherBlocks.length?"Required before deletion; preserved media or a Hermes task still references this render.":"Enabled: publisher handoffs and preserved outbox media will also be removed.")])]):"",el("div",{class:"cleanup-actions"},[el("button",{class:"ghost",onclick:()=>{state.cleanupPlan=null;render(state.lastStatus);}},"Cancel"),el("button",{class:"danger",...(plan.blocked.length?{disabled:"true"}:{}),onclick:()=>executeCleanup().catch(error=>showError(error.message))},plan.blocked.length?"Resolve protected items":`Delete and reclaim ${formatBytes(plan.reclaimable_bytes)}`)])])]);
}

function resourcePills(status) {
  const r = status.resources;
  return el("div", {class:"resources"}, [el("span", {}, `CPU ${r.cpu_percent.toFixed(0)}%`), el("span", {}, `RAM ${r.memory_percent.toFixed(0)}%`), el("span", {}, `${formatBytes(r.workspace_bytes)} stored`), el("span", {class: status.llm_available ? "ok" : "warn"}, status.llm_available ? "Hermes analysis ready" : "Hermes analysis unavailable")]);
}

function jobForm() {
  const input = el("input", {type:"url", placeholder:"Paste a public YouTube or Twitch live URL", required:"true"});
  const checkbox = el("input", {type:"checkbox"});
  const form = el("form", {onsubmit: async event => { event.preventDefault(); showError(""); try { const job = await request("/jobs", {method:"POST", body:JSON.stringify({url:input.value,start_mode:checkbox.checked?"from_start":"live_edge"})}); state.selected=job.id; input.value=""; await refresh(); } catch(error) { showError(error.message); } }}, [input, el("button", {type:"submit"}, "Watch stream"), el("label", {class:"check"}, [checkbox," Best-effort rewind to start"])]);
  return form;
}

function jobList(jobs) {
  const terminal = new Set(["stopped","completed","failed","needs_attention"]);
  const selectable = jobs.filter(job=>terminal.has(job.state));
  const allSelected = selectable.length>0 && selectable.every(job=>state.selectedJobs.has(job.id));
  return el("aside", {class:"jobs"}, [el("div", {class:"section-title"}, [el("h2", {}, "Streams"),el("button",{class:"select-all",...(selectable.length?{}:{disabled:"true"}),onclick:()=>{selectable.forEach(job=>allSelected?state.selectedJobs.delete(job.id):state.selectedJobs.add(job.id));state.cleanupPlan=null;render(state.lastStatus);}},allSelected?"Clear":"Select stopped"),el("span", {}, `${jobs.length}`)]), ...jobs.map(job => el("div",{class:`job-row ${job.id===state.selected?"selected":""}`},[selectionCheckbox("job",job.id,`Select stream ${job.title||job.external_id||job.id}`,!terminal.has(job.state)),el("button", {class:"job", onclick:()=>{state.selected=job.id;state.activityCandidate=null;refresh();}}, [el("strong", {}, job.title || job.external_id || "Resolving stream"), el("small", {}, job.provider || "source"), el("span", {class:`badge ${job.state}`}, job.state.replaceAll("_"," "))])]))]);
}

function detailPanel() {
  if (!state.detail) return el("section", {class:"empty"}, "Paste a livestream link to begin.");
  const {job, words, candidates, renders = []} = state.detail;
  const content = state.activeTab === "editor" ? clipsPanel(renders, "editor") : state.activeTab === "publisher" ? clipsPanel(renders, "publisher") : state.activeTab === "transcript" ? transcriptPanel(words) : candidatePanel(candidates);
  return el("section", {class:"detail"}, [
    el("div", {class:"detail-head"}, [el("div", {}, [el("p", {class:"eyebrow"}, job.provider || "STREAM"),el("h2", {}, job.title || job.external_id),el("a", {href:job.canonical_url,target:"_blank",rel:"noreferrer"}, job.canonical_url)]),el("button", {class:"secondary",onclick:async()=>{await request(`/jobs/${job.id}/stop`,{method:"POST"});refresh();}}, "Stop")]),
    job.last_error?el("p",{class:"notice"},job.last_error):"",
    pipelineRail(candidates, renders, words),
    activityPanel(),
    content,
  ]);
}

function pipelineRail(candidates, renders, words) {
  const ready = renders.filter(item => item.state === "ready").length;
  const handedOff = renders.filter(item => item.publisher_status || item.publisher_result).length;
  const published = renders.filter(item => item.publisher_result?.status === "published").length;
  const stages = [
    {id:"analyst",number:"01",role:"Story Analyst",verb:"Finds the story",metric:`${candidates.length} moments`,copy:"Scores hooks, payoff, and standalone value."},
    {id:"editor",number:"02",role:"Video Editor",verb:"Builds the draft",metric:`${ready} renders`,copy:"Cuts and verifies source-aspect video."},
    {id:"publisher",number:"03",role:"Publisher & Growth",verb:"Ships the story",metric:published?`${published} published`:`${handedOff} handed off`,copy:"Packages, publishes, and verifies receipts."},
  ];
  return el("div",{class:"pipeline-shell"},[
    el("nav",{class:"pipeline-rail","aria-label":"Clip production agents"},stages.map((stage,index)=>el("button",{class:`agent-stage ${state.activeTab===stage.id?"active":""}`,onclick:()=>{state.activeTab=stage.id;state.activityCandidate=null;render(state.lastStatus);}},[el("span",{class:"stage-number"},stage.number),el("div",{},[el("span",{class:"stage-verb"},stage.verb),el("strong",{},stage.role),el("p",{},stage.copy)]),el("span",{class:"stage-metric"},stage.metric),index<stages.length-1?el("span",{class:"stage-arrow","aria-hidden":"true"},"→"):""]))),
    el("button",{class:`transcript-switch ${state.activeTab==="transcript"?"active":""}`,onclick:()=>{state.activeTab="transcript";state.activityCandidate=null;render(state.lastStatus);}},["Transcript",el("span",{},words.length)]),
  ]);
}

function activityPanel() {
  if (!state.activityCandidate || !state.detail) return "";
  const candidate = state.detail.candidates.find(item => item.id === state.activityCandidate);
  if (!candidate) return "";
  const entries = (state.detail.activity || []).filter(item => item.candidate_id === candidate.id);
  const relevantRenders = (state.detail.renders || []).filter(item => item.candidate_id === candidate.id && item.publisher_task_id);
  const liveTask = relevantRenders.map(item => state.publisherTasks[item.id]).find(Boolean);
  const roles = {
    story_analyst:{initials:"SA",label:"Story Analyst"},
    video_editor:{initials:"VE",label:"Video Editor"},
    publisher_growth:{initials:"PG",label:"Publisher & Growth"},
    review_desk:{initials:"AJ",label:"Review Desk"},
    system:{initials:"SY",label:"System"},
  };
  return el("section",{class:"activity-panel"},[
    el("div",{class:"activity-head"},[el("div",{},[el("p",{class:"eyebrow"},"CLIP ACTIVITY"),el("h3",{},candidate.title),el("p",{class:"muted"},`${entries.length} recorded actions across the production pipeline.`)]),el("button",{class:"ghost",onclick:()=>{state.activityCandidate=null;render(state.lastStatus);},"aria-label":"Close clip activity"},"Close")]),
    liveTask?el("div",{class:"live-agent"},[el("span",{class:"pulse"}),el("strong",{},"Hermes Publisher"),el("span",{},String(liveTask.status||"queued").replaceAll("_"," ")),liveTask.current_run_id?el("code",{},liveTask.current_run_id):""]):"",
    el("div",{class:"activity-timeline"},entries.length?entries.map(entry=>{const role=roles[entry.role]||roles.system;return el("article",{class:"activity-entry","data-role":entry.role},[el("span",{class:"agent-avatar"},role.initials),el("div",{class:"activity-copy"},[el("div",{class:"activity-line"},[el("strong",{},role.label),el("span",{class:`activity-status ${entry.status}`},String(entry.status).replaceAll("_"," ")),el("time",{},formatActivityTime(entry.created_at))]),el("p",{},entry.message),entry.render_id?el("code",{},entry.render_id):""])]);}):[el("p",{class:"muted"},"No activity has been recorded for this clip yet.")]),
  ]);
}

function candidatePanel(candidates) {
  const allSelected = candidates.length>0 && candidates.every(item=>state.selectedCandidates.has(item.id));
  return el("div", {class:"panel tab-panel role-panel analyst-panel"}, [el("div", {class:"section-title"}, [el("div",{},[el("p",{class:"role-kicker"},"STORY ANALYST · AGENT 01"),el("h3",{},"Editorial opportunities"),el("p",{class:"muted"},"Hermes reviews the rolling transcript, scores each hook, and explains why the moment can stand alone.")]),el("div",{class:"section-tools"},[el("button",{class:"select-all",...(candidates.length?{}:{disabled:"true"}),onclick:()=>{candidates.forEach(item=>allSelected?state.selectedCandidates.delete(item.id):state.selectedCandidates.add(item.id));state.cleanupPlan=null;render(state.lastStatus);}},allSelected?"Clear selection":"Select all"),el("span",{},`${candidates.length}`)])]), el("div",{class:"candidate-grid"},candidates.length?candidates.map(candidateCard):[el("p",{class:"muted"},"Suggestions begin after five minutes of transcribed speech.")])]);
}

function latestReadyRender(candidateId) {
  return [...(state.detail?.renders || [])]
    .filter(item => item.candidate_id === candidateId && item.state === "ready")
    .sort((a,b) => b.version - a.version)[0] || null;
}

async function openRenderedClip(candidateId) {
  const ready = latestReadyRender(candidateId);
  if (!ready) throw new Error("No completed render is available for this suggestion yet");
  state.activeTab = "editor";
  state.activityCandidate = null;
  await previewClip(ready);
}

function candidateCard(c) {
  const action = async name => { await request(`/candidates/${c.id}/action`, {method:"POST",body:JSON.stringify({action:name})}); refresh(); };
  const busy = c.state === "rendering" || c.state === "render_queued";
  const readyRender = latestReadyRender(c.id);
  const primaryAction = readyRender ? el("button",{class:"view-render",onclick:()=>openRenderedClip(c.id).catch(error=>showError(error.message))},"View rendered clip") : el("button",{onclick:()=>action("render"),...(busy?{disabled:"true"}:{})},busy?"Rendering…":"Render clip");
  const rationale = oneSentence(c.rationale, "Hermes ranked this as a strong standalone moment with a clear payoff.");
  return el("article", {class:`candidate ${state.selectedCandidates.has(c.id)?"selected-for-cleanup":""}`}, [selectionCheckbox("candidate",c.id,`Select suggestion ${c.title}`),el("div",{class:"score","aria-label":`${Math.round(c.confidence*100)} out of 100 clip score`},[el("strong",{},`${Math.round(c.confidence*100)}`),el("span",{},"/ 100"),el("small",{},"CLIP SCORE")]),el("div",{class:"candidate-copy"},[el("h4",{},c.title),c.hook?el("p",{class:"candidate-hook"},c.hook):"",el("div",{class:"hook-rationale"},[el("span",{},"WHY IT HOOKS"),el("p",{},rationale)]),el("small",{class:"candidate-meta"},`${formatDuration(c.end_seconds-c.start_seconds)} · ${c.state.replaceAll("_"," ")}`),el("div",{class:"actions"},[primaryAction,el("button",{class:"secondary",onclick:()=>action("accept")},"Accept"),el("button",{class:"ghost",onclick:()=>action("reject")},"Reject"),el("button",{class:"ghost",onclick:()=>{state.activityCandidate=c.id;render(state.lastStatus);}},"Activity")])])]);
}

async function previewClip(renderItem) {
  const response = await authedFetch(`${api}/renders/${renderItem.id}/media`);
  if (!response.ok) throw new Error(`Could not load clip (${response.status})`);
  if (state.preview?.url) URL.revokeObjectURL(state.preview.url);
  state.preview = { id: renderItem.id, url: URL.createObjectURL(await response.blob()) };
  render(state.lastStatus);
}

async function saveClip(renderItem) {
  const response = await authedFetch(`${api}/renders/${renderItem.id}/media`);
  if (!response.ok) throw new Error(`Could not save clip (${response.status})`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url;
  link.download = `${renderItem.title.replace(/[^a-z0-9]+/gi,"-").replace(/^-|-$/g,"").toLowerCase()}-v${renderItem.version}.mp4`;
  link.click();
  window.setTimeout(() => URL.revokeObjectURL(url), 1000);
}

async function sendToHermesPublisher(renderItem) {
  if (!window.confirm(`Start a Hermes publishing agent for “${renderItem.title}”? It may upload and publish this MP4 to the signed-in TikTok and YouTube accounts.`)) return;
  showError("");
  showNotice("Preserving the MP4 and starting a Hermes publisher…");
  const handoff = await request(`/renders/${renderItem.id}/publisher-handoff`, {method:"POST"});
  const result = await kanbanRequest(`/tasks?board=${encodeURIComponent(publisherBoard)}`, {method:"POST", body:JSON.stringify(handoff.task)});
  const task = result.task || result;
  const taskId = task.id || task.task_id || result.taskId || null;
  if (!taskId) throw new Error("Hermes created a publisher task without returning its task ID");
  await request(`/renders/${renderItem.id}/publisher-handoff/complete`, {method:"POST", body:JSON.stringify({task_id:taskId})});
  showNotice(`Hermes publisher task ${taskId} is queued on this Mac. Publication will only be shown after verified platform receipts.`);
  await refresh();
}

function clipsPanel(renders, mode = "editor") {
  const ready = renders.filter(item => item.state === "ready");
  const inFlight = renders.filter(item => item.state === "rendering");
  const failed = renders.filter(item => item.state === "failed");
  const previewItem = ready.find(item => item.id === state.preview?.id);
  const publisherMode = mode === "publisher";
  const ordered = publisherMode ? [...ready].sort((a,b)=>Number(Boolean(b.publisher_status))-Number(Boolean(a.publisher_status))||b.confidence-a.confidence) : ready;
  const allSelected = ready.length>0 && ready.every(item=>state.selectedRenders.has(item.id));
  return el("div", {class:`tab-panel clips-view role-panel ${publisherMode?"publisher-panel":"editor-panel"}`}, [
    previewItem ? el("section",{class:"clip-player"},[el("div",{class:"player-copy"},[el("p",{class:"eyebrow"},`VERSION ${previewItem.version}`),el("h3",{},previewItem.title),el("p",{class:"muted"},`${formatDuration(previewItem.duration)} · ${Math.round(previewItem.confidence*100)} confidence`)]),el("video",{src:state.preview.url,controls:"true",preload:"metadata"})]) : "",
    el("div", {class:"section-title"}, [el("div",{},[el("p",{class:"role-kicker"},publisherMode?"PUBLISHER & GROWTH · AGENT 03":"VIDEO EDITOR · AGENT 02"),el("h3",{},publisherMode?"Publishing desk":"Rendered drafts"),el("p",{class:"muted"},publisherMode?"Package approved drafts, dispatch Hermes, and verify real platform receipts.":"Preview, verify, and hand finished drafts to the publishing desk.")]),el("div",{class:"section-tools"},[el("button",{class:"select-all",...(ready.length?{}:{disabled:"true"}),onclick:()=>{ready.forEach(item=>allSelected?state.selectedRenders.delete(item.id):state.selectedRenders.add(item.id));state.cleanupPlan=null;render(state.lastStatus);}},allSelected?"Clear selection":"Select all"),el("span",{},`${ready.length} ready`)])]),
    !publisherMode&&inFlight.length ? el("div",{class:"render-strip"},inFlight.map(item=>el("span",{},`${item.title} · rendering`))) : "",
    el("div",{class:"clip-grid"},ordered.length?ordered.map(item=>clipCard(item,mode)):[el("p",{class:"muted"},"No completed clips yet. Render a suggestion or wait for automatic drafts.")]),
    !publisherMode&&failed.length ? el("details",{class:"failed-renders"},[el("summary",{},`${failed.length} earlier failed render versions`),el("button",{class:"select-all",onclick:()=>{const allFailedSelected=failed.every(item=>state.selectedRenders.has(item.id));failed.forEach(item=>allFailedSelected?state.selectedRenders.delete(item.id):state.selectedRenders.add(item.id));state.cleanupPlan=null;render(state.lastStatus);}},failed.every(item=>state.selectedRenders.has(item.id))?"Clear failed selection":"Select failed renders"),...failed.map(item=>el("div",{class:`failed-render-row ${state.selectedRenders.has(item.id)?"selected-for-cleanup":""}`},[selectionCheckbox("render",item.id,`Select failed render ${item.title} version ${item.version}`),el("p",{},`v${item.version} · ${item.title} · ${formatBytes(item.size_bytes)} · ${item.error||"failed"}`)]))]) : "",
  ]);
}

function clipCard(item, mode = "editor") {
  const task = state.publisherTasks[item.id];
  const receipt = item.publisher_result;
  const taskStatus = task?.status || (item.publisher_status === "queued" ? "queued" : item.publisher_status);
  const published = receipt?.status === "published";
  const active = ["queued","ready","pending","waiting","running","claimed","in_progress"].includes(taskStatus);
  const finished = ["completed","done","blocked","failed","timed_out","gave_up"].includes(taskStatus) || Boolean(receipt);
  const tracked = Boolean(item.publisher_task_id);
  const publisherLabel = published ? "Published to TikTok + YouTube" : active ? `Hermes ${taskStatus}…` : taskStatus === "unavailable" ? "Hermes status unavailable" : finished ? `Hermes ${receipt?.status || taskStatus}` : item.publisher_status === "prepared" ? "Retry Hermes publisher" : "Publish with Hermes";
  const statusText = receipt?.summary || (taskStatus && taskStatus !== "prepared" ? `Hermes task ${item.publisher_task_id || ""} · ${taskStatus}` : "Hermes will use the signed-in TikTok and YouTube accounts.");
  const locked = tracked || active || finished || published;
  const roleAction = mode === "publisher" ? el("button",{class:"publisher",onclick:()=>sendToHermesPublisher(item).catch(error=>{showNotice("");showError(error.message);}),...(locked?{disabled:"true"}:{})},publisherLabel) : el("button",{class:"publisher",onclick:()=>{state.activeTab="publisher";state.activityCandidate=item.candidate_id;render(state.lastStatus);}},"Open in Publisher");
  return el("article",{class:`clip-card ${state.preview?.id===item.id?"selected":""} ${state.selectedRenders.has(item.id)?"selected-for-cleanup":""}`},[selectionCheckbox("render",item.id,`Select render ${item.title} version ${item.version}`),el("div",{class:"clip-card-top"},[el("span",{class:"version"},`v${item.version}`),el("span",{class:`ready-dot ${published?"published":""}`},published?"published":active?"Hermes working":"ready")]),el("h4",{},item.title),el("p",{class:"clip-meta"},`${formatDuration(item.duration)} · ${formatBytes(item.size_bytes)} · ${item.start_seconds.toFixed(1)}–${item.end_seconds.toFixed(1)}s`),el("p",{class:"publisher-status"},statusText),el("div",{class:"actions clip-actions"},[el("button",{onclick:()=>previewClip(item).catch(error=>showError(error.message))},"Preview"),el("button",{class:"secondary",onclick:()=>saveClip(item).catch(error=>showError(error.message))},"Save MP4"),roleAction,el("button",{class:"ghost",onclick:()=>{state.activityCandidate=item.candidate_id;render(state.lastStatus);}},"Activity")])]);
}

function transcriptPanel(words) {
  return el("div",{class:"panel transcript tab-panel"},[el("div",{class:"section-title"},[el("h3",{},"Rolling transcript"),el("span",{},`${words.length} words`)]),el("div",{class:"word-flow"},words.length?words.map(word=>el("button",{title:`${word.start_seconds.toFixed(2)}s`,"data-word-id":word.id},word.text)):[el("p",{class:"muted"},"Waiting for locally transcribed words…")])]);
}

function LiveClipperApp() {
  useEffect(() => {
    refresh();
    const timer = window.setInterval(() => refresh({ passive: true }), 5000);
    return () => window.clearInterval(timer);
  }, []);
  return h("main", { id: "hermes-live-clipper" });
}

registry.register("hermes-live-clipper", LiveClipperApp);
})();

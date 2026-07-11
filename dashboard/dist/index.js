(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!SDK || !registry) return;

  const { React, authedFetch } = SDK;
  const h = React.createElement;
  const { useEffect } = SDK.hooks;
  const api = "/api/plugins/hermes-live-clipper";
const state = { selected: null, detail: null, activeTab: "suggestions", preview: null, lastStatus: null, notice: "" };

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

async function refresh({ passive = false } = {}) {
  try {
    const currentPlayer = document.querySelector(".clip-player video");
    if (passive && currentPlayer && !currentPlayer.paused && !currentPlayer.ended) return;
    const playback = currentPlayer ? { time: currentPlayer.currentTime, paused: currentPlayer.paused } : null;
    const status = await request("/status");
    if (!state.selected && status.jobs.length) state.selected = status.jobs[0].id;
    if (state.selected) state.detail = await request(`/jobs/${state.selected}`);
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
    el("div", {class:"workspace"}, [jobList(status.jobs), detailPanel()])
  );
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
  return el("aside", {class:"jobs"}, [el("div", {class:"section-title"}, [el("h2", {}, "Streams"), el("span", {}, `${jobs.length}`)]), ...jobs.map(job => el("button", {class:`job ${job.id===state.selected?"selected":""}`, onclick:()=>{state.selected=job.id;refresh();}}, [el("strong", {}, job.title || job.external_id || "Resolving stream"), el("small", {}, job.provider || "source"), el("span", {class:`badge ${job.state}`}, job.state.replaceAll("_"," "))]))]);
}

function detailPanel() {
  if (!state.detail) return el("section", {class:"empty"}, "Paste a livestream link to begin.");
  const {job, words, candidates, renders = []} = state.detail;
  const readyCount = renders.filter(render => render.state === "ready").length;
  const tabs = [
    ["suggestions", "Suggestions", candidates.length],
    ["clips", "Generated clips", readyCount],
    ["transcript", "Transcript", words.length],
  ];
  const content = state.activeTab === "clips" ? clipsPanel(renders) : state.activeTab === "transcript" ? transcriptPanel(words) : candidatePanel(candidates);
  return el("section", {class:"detail"}, [
    el("div", {class:"detail-head"}, [el("div", {}, [el("p", {class:"eyebrow"}, job.provider || "STREAM"),el("h2", {}, job.title || job.external_id),el("a", {href:job.canonical_url,target:"_blank",rel:"noreferrer"}, job.canonical_url)]),el("button", {class:"secondary",onclick:async()=>{await request(`/jobs/${job.id}/stop`,{method:"POST"});refresh();}}, "Stop")]),
    job.last_error?el("p",{class:"notice"},job.last_error):"",
    el("nav", {class:"clip-tabs", "aria-label":"Live clipper views"}, tabs.map(([id,label,count]) => el("button", {class:id===state.activeTab?"active":"", onclick:()=>{state.activeTab=id;render(state.lastStatus);}}, [label,el("span",{},count)]))),
    content,
  ]);
}

function candidatePanel(candidates) {
  return el("div", {class:"panel tab-panel"}, [el("div", {class:"section-title"}, [el("div",{},[el("h3",{},"Clip suggestions"),el("p",{class:"muted"},"Strong moments are rendered automatically. Render again to create another version.")]),el("span",{},`${candidates.length}`)]), el("div",{class:"candidate-grid"},candidates.length?candidates.map(candidateCard):[el("p",{class:"muted"},"Suggestions begin after five minutes of transcribed speech.")])]);
}

function candidateCard(c) {
  const action = async name => { await request(`/candidates/${c.id}/action`, {method:"POST",body:JSON.stringify({action:name})}); refresh(); };
  const busy = c.state === "rendering" || c.state === "render_queued";
  const renderLabel = busy ? "Rendering…" : c.state === "draft_ready" ? "Render another version" : "Render clip";
  return el("article", {class:"candidate"}, [el("div",{class:"score"},`${Math.round(c.confidence*100)}`),el("div",{class:"candidate-copy"},[el("h4",{},c.title),el("p",{},c.hook||c.rationale||""),el("small",{},`${formatDuration(c.end_seconds-c.start_seconds)} · ${c.state.replaceAll("_"," ")}`),el("div",{class:"actions"},[el("button",{onclick:()=>action("render"),...(busy?{disabled:"true"}:{})},renderLabel),el("button",{class:"secondary",onclick:()=>action("accept")},"Accept"),el("button",{class:"ghost",onclick:()=>action("reject")},"Reject")])])]);
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

async function sendToPublisher(renderItem) {
  if (!window.confirm(`Queue “${renderItem.title}” for the editor/publisher? This preserves the MP4 and creates an editorial task; it does not publish automatically.`)) return;
  showError("");
  showNotice("Preparing a durable editor/publisher handoff…");
  const handoff = await request(`/renders/${renderItem.id}/publisher-handoff`, {method:"POST"});
  const response = await authedFetch("/api/plugins/techfren-review/qa-decision", {
    method: "POST",
    headers: {"Content-Type":"application/json"},
    body: JSON.stringify(handoff.payload),
  });
  if (!response.ok) {
    const problem = await response.json().catch(() => ({}));
    throw new Error(problem.detail || `Editor/publisher queue failed (${response.status})`);
  }
  const result = response.status === 204 ? {} : await response.json().catch(() => ({}));
  const taskId = result.taskId || result.task_id || result.id || null;
  await request(`/renders/${renderItem.id}/publisher-handoff/complete`, {method:"POST", body:JSON.stringify({task_id:taskId})});
  showNotice(taskId ? `Queued for the editor/publisher as task ${taskId}. It has not been published yet.` : "Queued for the editor/publisher. It has not been published yet.");
  await refresh();
}

function clipsPanel(renders) {
  const ready = renders.filter(item => item.state === "ready");
  const inFlight = renders.filter(item => item.state === "rendering");
  const failed = renders.filter(item => item.state === "failed");
  const previewItem = ready.find(item => item.id === state.preview?.id);
  return el("div", {class:"tab-panel clips-view"}, [
    previewItem ? el("section",{class:"clip-player"},[el("div",{class:"player-copy"},[el("p",{class:"eyebrow"},`VERSION ${previewItem.version}`),el("h3",{},previewItem.title),el("p",{class:"muted"},`${formatDuration(previewItem.duration)} · ${Math.round(previewItem.confidence*100)} confidence`)]),el("video",{src:state.preview.url,controls:"true",preload:"metadata"})]) : "",
    el("div", {class:"section-title"}, [el("div",{},[el("h3",{},"Generated clips"),el("p",{class:"muted"},"Every completed version stays here until you delete the stream.")]),el("span",{},`${ready.length} ready`)]),
    inFlight.length ? el("div",{class:"render-strip"},inFlight.map(item=>el("span",{},`${item.title} · rendering`))) : "",
    el("div",{class:"clip-grid"},ready.length?ready.map(clipCard):[el("p",{class:"muted"},"No completed clips yet. Render a suggestion or wait for automatic drafts.")]),
    failed.length ? el("details",{class:"failed-renders"},[el("summary",{},`${failed.length} earlier failed render versions`),...failed.map(item=>el("p",{},`v${item.version} · ${item.title} · ${item.error||"failed"}`))]) : "",
  ]);
}

function clipCard(item) {
  const sent = item.publisher_status === "queued";
  const publisherLabel = sent ? "Sent to publisher" : item.publisher_status === "prepared" ? "Retry publisher queue" : "Send to editor/publisher";
  return el("article",{class:`clip-card ${state.preview?.id===item.id?"selected":""}`},[el("div",{class:"clip-card-top"},[el("span",{class:"version"},`v${item.version}`),el("span",{class:"ready-dot"},sent?"publisher queued":"ready")]),el("h4",{},item.title),el("p",{class:"clip-meta"},`${formatDuration(item.duration)} · ${formatBytes(item.size_bytes)} · ${item.start_seconds.toFixed(1)}–${item.end_seconds.toFixed(1)}s`),el("div",{class:"actions clip-actions"},[el("button",{onclick:()=>previewClip(item).catch(error=>showError(error.message))},"Preview"),el("button",{class:"secondary",onclick:()=>saveClip(item).catch(error=>showError(error.message))},"Save MP4"),el("button",{class:"publisher",onclick:()=>sendToPublisher(item).catch(error=>{showNotice("");showError(error.message);}),...(sent?{disabled:"true"}:{})},publisherLabel)])]);
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

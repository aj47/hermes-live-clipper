(function () {
  "use strict";

  const SDK = window.__HERMES_PLUGIN_SDK__;
  const registry = window.__HERMES_PLUGINS__;
  if (!SDK || !registry) return;

  const { React, authedFetch } = SDK;
  const h = React.createElement;
  const { useEffect } = SDK.hooks;
  const api = "/api/plugins/hermes-live-clipper";
const state = { selected: null, detail: null };

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

async function refresh() {
  try {
    const status = await request("/status");
    if (!state.selected && status.jobs.length) state.selected = status.jobs[0].id;
    if (state.selected) state.detail = await request(`/jobs/${state.selected}`);
    render(status);
  } catch (error) { showError(error.message); }
}

function showError(message) {
  document.querySelector("#hlc-error")?.replaceChildren(message);
}

function render(status) {
  const root = document.querySelector("#hermes-live-clipper");
  if (!root) return;
  root.replaceChildren(
    el("header", {class:"hero"}, [el("div", {}, [el("p", {class:"eyebrow"}, "HERMES MEDIA WORKER"), el("h1", {}, "Live Clipper"), el("p", {class:"dek"}, "Turn a public livestream into transcript-grounded draft clips while it is still live.")]), resourcePills(status)]),
    el("section", {class:"submit-card"}, [jobForm(), el("p", {class:"legal"}, "Only clip content you are authorized to use. Public availability does not grant redistribution rights."), el("p", {id:"hlc-error", class:"error"})]),
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
  const {job, words, candidates} = state.detail;
  return el("section", {class:"detail"}, [el("div", {class:"detail-head"}, [el("div", {}, [el("p", {class:"eyebrow"}, job.provider || "STREAM"),el("h2", {}, job.title || job.external_id),el("a", {href:job.canonical_url,target:"_blank",rel:"noreferrer"}, job.canonical_url)]),el("button", {class:"secondary",onclick:async()=>{await request(`/jobs/${job.id}/stop`,{method:"POST"});refresh();}}, "Stop")]), job.last_error?el("p",{class:"notice"},job.last_error):"", el("div", {class:"columns"}, [candidatePanel(candidates), transcriptPanel(words)])]);
}

function candidatePanel(candidates) {
  return el("div", {class:"panel"}, [el("div", {class:"section-title"}, [el("h3",{},"Clip drafts"),el("span",{},`${candidates.length}`)]), ...(candidates.length?candidates.map(candidateCard):[el("p",{class:"muted"},"Suggestions begin after five minutes of transcribed speech.")])]);
}

function candidateCard(c) {
  const action = async name => { await request(`/candidates/${c.id}/action`, {method:"POST",body:JSON.stringify({action:name})}); refresh(); };
  return el("article", {class:"candidate"}, [el("div",{class:"score"},`${Math.round(c.confidence*100)}`),el("div",{class:"candidate-copy"},[el("h4",{},c.title),el("p",{},c.hook||c.rationale||""),el("small",{},`${c.start_seconds.toFixed(1)}–${c.end_seconds.toFixed(1)}s · ${c.state}`),el("div",{class:"actions"},[el("button",{onclick:()=>action("render")},"Render"),el("button",{class:"secondary",onclick:()=>action("accept")},"Accept"),el("button",{class:"ghost",onclick:()=>action("reject")},"Reject")])])]);
}

function transcriptPanel(words) {
  return el("div",{class:"panel transcript"},[el("div",{class:"section-title"},[el("h3",{},"Rolling transcript"),el("span",{},`${words.length} words`)]),el("div",{class:"word-flow"},words.length?words.map(word=>el("button",{title:`${word.start_seconds.toFixed(2)}s`,"data-word-id":word.id},word.text)):[el("p",{class:"muted"},"Waiting for locally transcribed words…")])]);
}

function LiveClipperApp() {
  useEffect(() => {
    refresh();
    const timer = window.setInterval(refresh, 5000);
    return () => window.clearInterval(timer);
  }, []);
  return h("main", { id: "hermes-live-clipper" });
}

registry.register("hermes-live-clipper", LiveClipperApp);
})();

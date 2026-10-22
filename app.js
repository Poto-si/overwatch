"use strict";

const $ = id => document.getElementById(id);
const labels = {running: "Working", waiting: "Waiting", idle: "Idle", interrupted: "Interrupted", failed: "Failed", unknown: "Unknown"};
const state = {agents: [], snapshot: null, selected: null, collapsed: new Set(), paused: false, fetching: false, offline: false, view: "squares", provider: "codex"};
const providerKey = "potosi.overwatch.provider";
const validProvider = value => value === "claude" ? "claude" : "codex";
try { state.provider = validProvider(localStorage.getItem(providerKey)); } catch (_) { /* Storage is optional. */ }
function syncProviderControl() {
  const label = state.provider === "claude" ? "Claude" : "Codex";
  $("provider-label").textContent = label;
  $("provider-logo").dataset.provider = state.provider;
  $("provider-menu").querySelector("summary").setAttribute("aria-label", "Agent platform: " + label);
  document.querySelectorAll('input[name="agent-provider"]').forEach(input => { input.checked = input.value === state.provider; });
}
syncProviderControl();
const providerLabel = () => state.provider === "claude" ? "Claude Code" : "Codex";
const symbols = {running: "▶", waiting: "Ⅱ", idle: "○", interrupted: "×", failed: "!", unknown: "?"};
const compact = new Intl.NumberFormat("en", {notation: "compact", maximumFractionDigits: 1});
const exact = new Intl.NumberFormat("en");
const fmt = value => value == null ? "—" : compact.format(value);
const projectName = path => path ? path.split(/[\\/]/).filter(Boolean).slice(-2).join("/") : "Unknown project";
function ago(at) {
  if (!at) return "Unknown";
  const seconds = Math.max(0, Math.floor(Date.now() / 1000 - at));
  if (seconds < 5) return "Just now";
  if (seconds < 60) return seconds + "s ago";
  if (seconds < 3600) return Math.floor(seconds / 60) + "m ago";
  if (seconds < 86400) return Math.floor(seconds / 3600) + "h ago";
  return Math.floor(seconds / 86400) + "d ago";
}
function statusBadge(node, agent) {
  const value = Object.hasOwn(labels, agent.status) ? agent.status : "unknown";
  node.className = "status " + value;
  node.textContent = labels[value];
  node.title = agent.status_reason;
}
function node(tag, className, text) {
  const element = document.createElement(tag);
  if (className) element.className = className;
  if (text != null) element.textContent = text;
  return element;
}
function filtered() {
  const query = $("search").value.trim().toLowerCase();
  const project = $("project-filter").value;
  const status = $("status-filter").value;
  const open = $("active-only").checked;
  return state.agents.filter(agent =>
    (!query || [agent.name, agent.model, agent.cwd, agent.id].join(" ").toLowerCase().includes(query)) &&
    (!project || agent.cwd === project) && (!status || agent.status === status) &&
    (!open || ["running", "waiting"].includes(agent.status) || (agent.status === "unknown" && agent.turn_started_at && !agent.turn_ended_at))
  );
}
function updateProjects() {
  const select = $("project-filter");
  const selected = select.value;
  const projects = [...new Set(state.agents.map(a => a.cwd).filter(Boolean))].sort();
  const signature = JSON.stringify(projects);
  if (select.dataset.signature === signature) return;
  select.dataset.signature = signature;
  select.replaceChildren(new Option("All projects", ""));
  for (const project of projects) select.add(new Option(projectName(project), project));
  if (projects.includes(selected)) select.value = selected;
}
function renderStats(agents) {
  const recorded = agents.filter(agent => agent.tokens != null);
  const sums = recorded.reduce((acc, a) => {
    acc.input += a.tokens.input_tokens;
    acc.cached += a.tokens.cached_input_tokens;
    acc.total += a.tokens.total_tokens;
    return acc;
  }, {input: 0, cached: 0, total: 0});
  $("stat-agents").textContent = exact.format(agents.length);
  $("stat-active").textContent = agents.filter(a => a.status === "running").length + " / " + agents.filter(a => a.status === "waiting").length;
  $("stat-tokens").textContent = recorded.length ? fmt(sums.total) : "—";
  $("stat-tokens").title = recorded.length ? exact.format(sums.total) + " recorded tokens" : "No usage recorded";
  $("stat-cache").textContent = sums.input ? (100 * sums.cached / sums.input).toFixed(1) + "%" : "—";
  $("stat-token-caption").textContent = recorded.length + "/" + agents.length + " report usage · not cost or quota";
  $("stat-scope").textContent = "Current filters · " + new Set(agents.map(a => a.cwd)).size + " projects";
}
function renderTable(agents) {
  const focused = $("agent-rows").contains(document.activeElement) ? document.activeElement.dataset.focusKey : null;
  const visible = new Map(agents.map(a => [a.id, a]));
  const children = new Map();
  for (const agent of agents) {
    if (!children.has(agent.parent_id)) children.set(agent.parent_id, []);
    children.get(agent.parent_id).push(agent);
  }
  const visited = new Set();
  const rows = document.createDocumentFragment();
  function add(agent, depth) {
    if (visited.has(agent.id)) return;
    visited.add(agent.id);
    const offspring = children.get(agent.id) || [];
    const row = node("tr", "agent-row" + (state.selected === agent.id ? " selected" : ""));
    row.dataset.id = agent.id;
    const cell = node("td");
    const flex = node("div", "agent-cell");
    const indent = node("span", "tree-indent");
    indent.style.width = Math.min(depth, 7) * 18 + "px";
    flex.append(indent);
    if (offspring.length) {
      const toggle = node("button", "tree-toggle", state.collapsed.has(agent.id) ? "▸" : "▾");
      toggle.type = "button";
      toggle.dataset.focusKey = "toggle:" + agent.id;
      toggle.setAttribute("aria-label", (state.collapsed.has(agent.id) ? "Expand " : "Collapse ") + agent.name);
      toggle.setAttribute("aria-expanded", String(!state.collapsed.has(agent.id)));
      toggle.addEventListener("click", event => {
        event.stopPropagation();
        state.collapsed.has(agent.id) ? state.collapsed.delete(agent.id) : state.collapsed.add(agent.id);
        render();
      });
      flex.append(toggle);
    }
    flex.append(node("span", "agent-icon" + (agent.parent_id ? " child" : ""), agent.parent_id ? "↳" : "◈"));
    const text = node("div", "agent-text");
    const title = node("button", "agent-name", agent.name);
    title.type = "button";
    title.dataset.focusKey = "select:" + agent.id;
    title.title = agent.name;
    title.setAttribute("aria-pressed", String(state.selected === agent.id));
    const parentHidden = agent.parent_id && !visible.has(agent.parent_id) ? " · parent outside view" : "";
    text.append(title, node("span", "agent-subtitle", agent.model + parentHidden));
    flex.append(text);
    cell.append(flex);
    const statusCell = node("td");
    const badge = node("span");
    statusBadge(badge, agent);
    statusCell.append(badge);
    const tokens = node("td", "number", fmt(agent.tokens?.total_tokens));
    tokens.title = agent.tokens ? exact.format(agent.tokens.total_tokens) + " input + output" : "No usage recorded";
    const recent = node("td", "number muted", ago(agent.last_seen));
    recent.title = agent.last_seen ? new Date(agent.last_seen * 1000).toLocaleString() : "No activity recorded";
    row.append(cell, statusCell, tokens, recent);
    row.addEventListener("click", () => {
      state.selected = agent.id;
      render();
    });
    rows.append(row);
    if (state.collapsed.has(agent.id)) {
      function markDescendants(id) {
        for (const child of children.get(id) || []) {
          if (!visited.has(child.id)) { visited.add(child.id); markDescendants(child.id); }
        }
      }
      markDescendants(agent.id);
    } else offspring.forEach(child => add(child, depth + 1));
  }
  agents.filter(a => !a.parent_id || !visible.has(a.parent_id)).forEach(a => add(a, 0));
  // Malformed/cyclic relationships must not hide observed threads.
  agents.filter(a => !visited.has(a.id)).forEach(a => add(a, 0));
  $("agent-rows").replaceChildren(rows);
  if (focused) [...$("agent-rows").querySelectorAll("button")].find(b => b.dataset.focusKey === focused)?.focus({preventScroll: true});
  $("empty").hidden = agents.length !== 0;
  $("result-count").textContent = agents.length + " of " + state.agents.length + " observed sessions";
}
function renderSquares(agents) {
  const container = $("square-groups");
  const focused = container.contains(document.activeElement) ? document.activeElement.dataset.agentId : null;
  const groups = new Map();
  const ordered = [...agents].sort((a, b) => (a.created_at || 0) - (b.created_at || 0) || a.id.localeCompare(b.id));
  const numbers = new Map([...state.agents].sort((a, b) => (a.created_at || 0) - (b.created_at || 0) || a.id.localeCompare(b.id)).map((a, index) => [a.id, index + 1]));
  for (const agent of ordered) {
    const project = agent.cwd || "";
    if (!groups.has(project)) groups.set(project, []);
    groups.get(project).push(agent);
  }
  const fragment = document.createDocumentFragment();
  function caption(agent) {
    const label = labels[agent.status] || labels.unknown;
    $("square-caption").textContent = agent.name + " · " + label + " · " + agent.model + " · " + (agent.tokens ? exact.format(agent.tokens.total_tokens) + " tokens" : "Usage not recorded");
  }
  for (const [project, members] of [...groups].sort(([a], [b]) => a.localeCompare(b))) {
    const group = node("section", "square-group");
    group.setAttribute("aria-label", project || "Unknown project");
    const heading = node("div", "square-group-heading");
    const name = node("span", "square-project", projectName(project));
    name.title = project || "Project not recorded";
    const working = members.filter(a => a.status === "running").length;
    heading.append(name, node("span", "square-group-count", members.length + " agents" + (working ? " · " + working + " working" : "")));
    const grid = node("div", "square-grid");
    let subagentStack = null;
    for (const agent of members) {
      const status = Object.hasOwn(labels, agent.status) ? agent.status : "unknown";
      const selected = state.selected === agent.id;
      const tile = node("button", "agent-square " + status + (agent.parent_id ? " subagent-square" : "") + (selected ? " selected" : ""));
      tile.type = "button";
      tile.dataset.agentId = agent.id;
      tile.setAttribute("aria-pressed", String(selected));
      const description = agent.name + " · " + labels[status] + " · " + (agent.tokens ? exact.format(agent.tokens.total_tokens) + " tokens" : "Usage not recorded");
      tile.setAttribute("aria-label", description);
      tile.title = description + "\n" + agent.model + "\n" + agent.id + (agent.parent_id ? "\nParent: " + agent.parent_id : "\nRoot session");
      const header = node("span", "square-tile-heading");
      const symbol = node("span", "square-symbol", symbols[status]);
      symbol.setAttribute("aria-hidden", "true");
      const relation = node("span", "square-relation", agent.parent_id ? "↳" : "◈");
      relation.setAttribute("aria-hidden", "true");
      header.append(symbol, relation);
      tile.append(header, node("span", "square-number", String(numbers.get(agent.id)).padStart(2, "0")), node("span", "square-tokens", fmt(agent.tokens?.total_tokens)));
      tile.addEventListener("mouseenter", () => caption(agent));
      tile.addEventListener("focus", () => caption(agent));
      tile.addEventListener("click", () => { state.selected = agent.id; render(); });
      if (agent.parent_id) {
        // Fill each pair top to bottom before starting the next column.
        if (!subagentStack || subagentStack.childElementCount === 2) {
          subagentStack = node("div", "subagent-stack");
          grid.append(subagentStack);
        }
        subagentStack.append(tile);
      } else {
        subagentStack = null;
        grid.append(tile);
      }
    }
    group.append(heading, grid);
    fragment.append(group);
  }
  container.replaceChildren(fragment);
  const selected = agents.find(a => a.id === state.selected);
  if (selected) caption(selected);
  else $("square-caption").textContent = "No matching agents. Adjust your filters to see observed sessions.";
  if (focused) [...container.querySelectorAll("button")].find(b => b.dataset.agentId === focused)?.focus({preventScroll: true});
}
function setView(view) {
  state.view = view;
  render();
}
function renderDetail() {
  const agent = state.agents.find(a => a.id === state.selected);
  $("detail").hidden = !agent;
  $("detail-empty").hidden = !!agent;
  if (!agent) return;
  statusBadge($("detail-status"), agent);
  $("detail-name").textContent = agent.name;
  $("detail-reason").textContent = agent.status_reason + ".";
  for (const [field, value] of Object.entries({model: agent.model, provider: agent.provider, project: agent.cwd || "Unknown", source: agent.source + " · " + agent.status_source, id: agent.id, parent: agent.parent_id || "Root session"})) $("detail-" + field).textContent = value;
  const elapsed = agent.turn_started_at ? Math.max(0, (agent.turn_ended_at || agent.last_seen || agent.turn_started_at) - agent.turn_started_at) : null;
  $("detail-duration").textContent = elapsed == null ? "Unknown" : Math.floor(elapsed / 60) + "m " + Math.floor(elapsed % 60) + "s recorded";
  const t = agent.tokens;
  $("detail-total").textContent = t ? exact.format(t.total_tokens) : "Not recorded";
  const cached = t ? Math.min(t.input_tokens, t.cached_input_tokens) : 0;
  const uncached = t ? t.input_tokens - cached : 0;
  for (const [key, value] of Object.entries({input: uncached, cached, output: t?.output_tokens || 0})) {
    $("detail-" + key).textContent = t ? exact.format(value) : "—";
    $("bar-" + key).style.width = (t?.total_tokens ? value / t.total_tokens * 100 : 0) + "%";
  }
  $("detail-reasoning").textContent = t?.reasoning_output_tokens == null ? "Not recorded" : exact.format(t.reasoning_output_tokens);
  const window = Number(agent.context_window);
  const request = agent.last_request?.total_tokens;
  const ratio = window > 0 && request != null ? request / window * 100 : null;
  $("detail-context").textContent = ratio == null ? "Unknown" : fmt(request) + " / " + fmt(window);
  $("context-progress").value = ratio == null ? 0 : Math.min(100, ratio);
  $("context-progress").hidden = ratio == null;
  const events = (agent.activity || []).map(event => {
    const li = node("li");
    const at = node("time", "", ago(event.at));
    if (event.at) { at.dateTime = new Date(event.at * 1000).toISOString(); at.title = new Date(event.at * 1000).toLocaleString(); }
    li.append(node("p", "", event.text), at);
    return li;
  });
  $("activity").replaceChildren(...(events.length ? events : [node("li", "muted small", "No lifecycle/tool metadata recorded")]));
}
function render() {
  const agents = filtered();
  if (!agents.some(a => a.id === state.selected)) state.selected = agents[0]?.id || null;
  renderStats(agents);
  $("squares-view").hidden = state.view !== "squares" || agents.length === 0;
  $("table-view").hidden = state.view !== "table" || agents.length === 0;
  $("view-squares").setAttribute("aria-pressed", String(state.view === "squares"));
  $("view-table").setAttribute("aria-pressed", String(state.view === "table"));
  $("empty").hidden = agents.length !== 0;
  $("result-count").textContent = agents.length + " of " + state.agents.length + " observed sessions";
  if (state.view === "squares") renderSquares(agents);
  else renderTable(agents);
  renderDetail();
  const snapshot = state.snapshot;
  const stale = snapshot && !state.paused && Date.now() / 1000 - snapshot.generated_at > Math.max(10, 4 * (snapshot.refresh_interval || 2));
  if (snapshot) {
    $("updated").textContent = "Snapshot · " + ago(snapshot.generated_at);
    const notice = $("notice");
    notice.className = "notice" + (snapshot.mode === "demo" ? " demo" : "");
    const parts = snapshot.mode === "demo" ? ["DEMO · Sample agents. No real session data is being read."] : ["Read-only " + providerLabel() + " log observations. “Working” is inferred; stale open turns become “Unknown”."];
    if (snapshot.discovered > snapshot.limit) parts.push("Showing the " + snapshot.limit + " most recently modified rollout files of " + snapshot.discovered + ".");
    parts.push(...(snapshot.warnings || []));
    if (state.offline) parts.unshift("Observer disconnected. This is the last successfully received snapshot.");
    else if (stale) parts.unshift("Observer snapshot is stale. These are the last recorded observations.");
    notice.textContent = parts.join(" ");
    if (state.offline || stale) notice.classList.add("error");
  }
  $("connection").classList.toggle("offline", !!(state.offline || stale || state.paused));
  $("connection-label").textContent = state.offline ? "Disconnected" : state.paused ? "View paused" : stale ? "Snapshot stale" : "Observing";
}
async function refresh() {
  if (state.paused || state.fetching) return;
  state.fetching = true;
  const provider = state.provider;
  try {
    const response = await fetch("/api/snapshot?provider=" + encodeURIComponent(provider), {cache: "no-store", signal: AbortSignal.timeout(10000)});
    if (!response.ok) throw new Error("Snapshot unavailable");
    const snapshot = await response.json();
    if (state.provider !== provider) return;
    if (snapshot.loading) { $("notice").textContent = "Reading recent session logs. The first snapshot can take a moment…"; return; }
    state.snapshot = snapshot;
    state.agents = Array.isArray(snapshot.agents) ? snapshot.agents : [];
    state.offline = false;
    if (!state.agents.some(a => a.id === state.selected)) state.selected = state.agents[0]?.id || null;
    updateProjects();
    render();
  } catch (_) {
    if (state.provider !== provider) return;
    state.offline = true;
    if (!state.snapshot) { $("notice").className = "notice error"; $("notice").textContent = "Cannot reach the local observer. Start it with python3 server.py, then open its printed URL."; }
    render();
  } finally {
    state.fetching = false;
    if (state.provider !== provider) refresh();
  }
}
function selectProvider(value) {
  const provider = validProvider(value);
  if (provider === state.provider) return;
  state.provider = provider;
  syncProviderControl();
  state.agents = [];
  state.snapshot = null;
  state.selected = null;
  state.collapsed.clear();
  state.offline = false;
  state.paused = false;
  $("pause").textContent = "Pause view";
  $("pause").setAttribute("aria-pressed", "false");
  $("search").value = "";
  $("status-filter").value = "";
  $("active-only").checked = false;
  updateProjects();
  render();
  $("notice").className = "notice";
  $("notice").textContent = "Connecting to " + providerLabel() + " observations…";
  $("connection-label").textContent = "Connecting";
  $("updated").textContent = "Awaiting first snapshot";
  try { localStorage.setItem(providerKey, provider); } catch (_) { /* Storage is optional. */ }
  refresh();
}
document.querySelectorAll('input[name="agent-provider"]').forEach(input => input.addEventListener("change", () => {
  if (!input.checked) return;
  selectProvider(input.value);
  $("provider-menu").open = false;
  $("provider-menu").querySelector("summary").focus();
}));
window.addEventListener("storage", event => {
  if (event.key === providerKey || event.key === null) selectProvider(event.newValue);
});
for (const id of ["search", "project-filter", "status-filter", "active-only"]) $(id).addEventListener(id === "search" ? "input" : "change", render);
$("view-squares").addEventListener("click", () => setView("squares"));
$("view-table").addEventListener("click", () => setView("table"));
$("pause").addEventListener("click", () => {
  state.paused = !state.paused;
  $("pause").textContent = state.paused ? "Resume view" : "Pause view";
  $("pause").setAttribute("aria-pressed", String(state.paused));
  render();
  if (!state.paused) refresh();
});
refresh();
setInterval(() => { if (!state.paused) refresh(); }, 2000);

const AGENT_IDS = ["coordinate", "architect", "implement", "review"];

const SAMPLE_TASK = `Onboard the new source table customer.account.

Source system: Salesforce
Landing path: s3://landing/salesforce/account/dt=YYYY-MM-DD/
Sample fields: account_id, account_name, email, status, last_modified_date
Target domain: customer
Target table: account
Primary key: account_id
Incremental watermark: last_modified_date
Load frequency: once a day, about 50,000 rows per day
Data quality: account_id is required and unique; email must be a valid address; status allows only active and inactive
Business transforms: lowercase email; normalize status to lowercase
Repository: luanluankuaile/etl_onboarding
Base branch: dev
Proposed feature branch: feature/account_onboarding`;

const state = {
  route: "task",
  agents: [],
  pipeline: [],
  settings: null,
  githubSettings: null,
  githubMenuOpen: false,
  githubConfigOpen: true,
  githubTab: "settings",
  githubRepo: null,
  githubBranches: [],
  githubTree: null,
  githubCommits: [],
  githubPulls: [],
  githubIssues: [],
  githubFile: null,
  githubError: "",
  githubNotice: "",
  sessions: [],
  session: null,
  messages: [],
  view: localStorage.getItem("ag-view") || "timeline",
  drafts: {},
  targetAgent: "coordinate",
  manageAgent: "coordinate",
  prompt: null,
  promptNotice: "",
  streaming: false,
  activeStep: null,
  error: "",
  abort: null,
};

let flowId = 0;
let ignoreHash = false;

function readHash() {
  const hash = location.hash.replace(/^#\/?/, "");
  if (hash === "github" || hash.startsWith("github/")) {
    const tab = hash.startsWith("github/") ? hash.slice("github/".length) : "settings";
    return {
      route: "github",
      githubTab: ["settings", "branches", "files", "commits", "pulls", "issues"].includes(tab) ? tab : "settings",
      manageAgent: state.manageAgent || "coordinate",
    };
  }
  if (hash === "manage" || hash.startsWith("manage/")) {
    const id = hash.startsWith("manage/") ? hash.slice("manage/".length) : "";
    return {
      route: "manage",
      manageAgent: AGENT_IDS.includes(id) ? id : state.manageAgent || "coordinate",
    };
  }
  if (hash.startsWith("agent/")) {
    const id = hash.slice("agent/".length);
    if (AGENT_IDS.includes(id)) {
      return { route: id, manageAgent: state.manageAgent || "coordinate" };
    }
  }
  return { route: "task", manageAgent: state.manageAgent || "coordinate" };
}

function currentHash() {
  if (state.route === "manage") return `#/manage/${state.manageAgent || "coordinate"}`;
  if (state.route === "github") return `#/github/${state.githubTab || "settings"}`;
  if (state.route === "task") return "#/task";
  return `#/agent/${state.route}`;
}

function escapeHtml(value) {
  return String(value)
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;");
}

function agentById(id) {
  return state.agents.find((agent) => agent.id === id);
}

function errorMessage(data) {
  const detail = data && data.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail) && detail[0] && detail[0].msg) return detail[0].msg;
  return "The request did not finish";
}

async function api(path, options) {
  const response = await fetch(path, options);
  const data = await response.json().catch(() => ({}));
  if (!response.ok) throw new Error(errorMessage(data));
  return data;
}

async function readSSE(response, onEvent) {
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  while (true) {
    const { value, done } = await reader.read();
    if (done) break;
    buffer += decoder.decode(value, { stream: true });
    let splitAt = buffer.indexOf("\n\n");
    while (splitAt >= 0) {
      const raw = buffer.slice(0, splitAt);
      buffer = buffer.slice(splitAt + 2);
      const data = raw
        .split("\n")
        .filter((line) => line.startsWith("data:"))
        .map((line) => line.slice(5).trim())
        .join("\n");
      if (data) onEvent(JSON.parse(data));
      splitAt = buffer.indexOf("\n\n");
    }
  }
}

function inline(source) {
  return source
    .split(/(`[^`]+`)/g)
    .map((part) => {
      if (part.startsWith("`") && part.endsWith("`") && part.length >= 2) {
        return `<code>${part.slice(1, -1)}</code>`;
      }
      return part
        .replace(/\*\*([^*]+)\*\*/g, "<strong>$1</strong>")
        .replace(/(^|[^*])\*([^*\n]+)\*/g, "$1<em>$2</em>");
    })
    .join("");
}

function isSeparator(line) {
  if (!line.trim()) return false;
  const cells = splitRow(line);
  return cells.length > 0 && cells.every((cell) => /^:?-{3,}:?$/.test(cell.replace(/\s+/g, "")));
}

function splitRow(line) {
  let text = line.trim();
  if (text.startsWith("|")) text = text.slice(1);
  if (text.endsWith("|")) text = text.slice(0, -1);
  return text.split("|").map((cell) => cell.trim());
}

function renderTable(lines) {
  const content = lines.filter((line) => line.trim() && !isSeparator(line));
  if (!content.length) return "";
  const head = splitRow(content[0]);
  const body = content.slice(1).map(splitRow);
  const headHtml = head.map((cell) => `<th>${inline(cell)}</th>`).join("");
  const bodyHtml = body
    .map((row) => `<tr>${row.map((cell) => `<td>${inline(cell)}</td>`).join("")}</tr>`)
    .join("");
  return `<div class="table-wrap"><table><thead><tr>${headHtml}</tr></thead><tbody>${bodyHtml}</tbody></table></div>`;
}

function isTableStart(lines, index) {
  return (lines[index] || "").includes("|") && isSeparator(lines[index + 1] || "");
}

function isBlockStart(line, next) {
  return (
    line.startsWith("```") ||
    /^#{1,4} /.test(line) ||
    /^[-*] /.test(line) ||
    /^\d+\. /.test(line) ||
    line.startsWith("&gt;") ||
    (line.includes("|") && isSeparator(next || ""))
  );
}

function renderMarkdown(source) {
  const text = escapeHtml(source)
    .replaceAll("&lt;mark&gt;", "<mark>")
    .replaceAll("&lt;/mark&gt;", "</mark>");
  const lines = text.split("\n");
  const html = [];
  let index = 0;
  while (index < lines.length) {
    const line = lines[index];
    if (!line.trim()) {
      index += 1;
      continue;
    }
    if (line.startsWith("```")) {
      const buffer = [];
      index += 1;
      while (index < lines.length && !lines[index].startsWith("```")) {
        buffer.push(lines[index]);
        index += 1;
      }
      html.push(`<pre><code>${buffer.join("\n")}</code></pre>`);
      index += 1;
      continue;
    }
    const heading = line.match(/^(#{1,4}) /);
    if (heading) {
      const level = heading[1].length;
      html.push(`<h${level}>${inline(line.slice(level + 1))}</h${level}>`);
      index += 1;
      continue;
    }
    if (/^[-*_]{3,}$/.test(line.trim())) {
      html.push("<hr>");
      index += 1;
      continue;
    }
    if (isTableStart(lines, index)) {
      const tableLines = [];
      while (index < lines.length && lines[index].includes("|")) {
        tableLines.push(lines[index]);
        index += 1;
      }
      html.push(renderTable(tableLines));
      continue;
    }
    if (line.startsWith("&gt;")) {
      const buffer = [];
      while (index < lines.length && lines[index].startsWith("&gt;")) {
        buffer.push(lines[index].replace(/^&gt; ?/, ""));
        index += 1;
      }
      html.push(`<blockquote>${inline(buffer.join("<br>"))}</blockquote>`);
      continue;
    }
    if (/^[-*] /.test(line)) {
      const items = [];
      while (index < lines.length && /^[-*] /.test(lines[index])) {
        items.push(`<li>${inline(lines[index].slice(2))}</li>`);
        index += 1;
      }
      html.push(`<ul>${items.join("")}</ul>`);
      continue;
    }
    if (/^\d+\. /.test(line)) {
      const items = [];
      while (index < lines.length && /^\d+\. /.test(lines[index])) {
        items.push(`<li>${inline(lines[index].replace(/^\d+\. /, ""))}</li>`);
        index += 1;
      }
      html.push(`<ol>${items.join("")}</ol>`);
      continue;
    }
    const buffer = [line];
    index += 1;
    while (index < lines.length && lines[index].trim() && !isBlockStart(lines[index], lines[index + 1])) {
      buffer.push(lines[index]);
      index += 1;
    }
    html.push(`<p>${inline(buffer.join("<br>"))}</p>`);
  }
  return html.join("");
}

function formatTime(iso) {
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return "";
  return date.toLocaleString("en-US", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" });
}

function renderMessage(message, compact) {
  if (message.role === "user") {
    return `<article class="bubble user"><div class="bubble-label">You</div><div class="bubble-body">${escapeHtml(message.content)}</div></article>`;
  }
  const agent = agentById(message.agent_id);
  const role = agent ? agent.role : "Agent";
  const showStep = state.route === "task" && message.step_title && !(compact && message.step_id === "reply");
  const reasoning = message.reasoning
    ? `<details class="reasoning"><summary>${message.live && !message.content ? "Thinking" : "Reasoning"}</summary><div>${escapeHtml(message.reasoning)}</div></details>`
    : "";
  const body = message.content
    ? renderMarkdown(message.content)
    : message.live
      ? `<span class="caret"></span>`
      : "(Empty reply)";
  return `<article class="bubble agent agent-${message.agent_id || "coordinate"}">
    <header><span class="dot"></span><strong>${escapeHtml(role)}</strong>${showStep ? `<span>${escapeHtml(message.step_title)}</span>` : ""}</header>
    ${reasoning}
    <div class="md">${body}</div>
  </article>`;
}

function latestTail() {
  let lastUser = -1;
  state.messages.forEach((message, index) => {
    if (message.role === "user") lastUser = index;
  });
  return state.messages.slice(lastUser + 1);
}

function renderRail() {
  const tail = latestTail();
  return `<ol class="rail">${state.pipeline
    .map((step) => {
      const done = tail.some((message) => message.step_id === step.id && !message.live);
      const active = state.activeStep === step.id;
      const agent = agentById(step.agent_id);
      return `<li class="${done ? "done" : ""} ${active ? "active" : ""}"><strong>${escapeHtml(step.title)}</strong>${escapeHtml(agent ? agent.role : "")}</li>`;
    })
    .join("")}</ol>`;
}

function renderEmptyTask() {
  const cards = state.agents
    .map(
      (agent) => `<article class="intro-card agent-${agent.id}">
        <header><span class="dot"></span><strong>${escapeHtml(agent.role)}</strong><span>${escapeHtml(agent.name)}</span></header>
        <p>${escapeHtml(agent.welcome_message || agent.duty)}</p>
        <button type="button" class="linkish" data-route="${agent.id}">Open chat</button>
      </article>`
    )
    .join("");
  return `<div class="intro-grid">${cards}</div>`;
}

function renderEmptyAgent(agent) {
  const starters = (agent.conversation_starters || [])
    .map(
      (starter, index) =>
        `<button type="button" class="ghost" data-starter="${index}">${escapeHtml(starter)}</button>`
    )
    .join("");
  return `<article class="bubble agent agent-${agent.id}">
    <header><span class="dot"></span><strong>${escapeHtml(agent.role)}</strong><span>${escapeHtml(agent.name)}</span></header>
    <div class="md"><p>${escapeHtml(agent.welcome_message || "")}</p></div>
    <div class="composer-actions">${starters}</div>
  </article>`;
}

function renderBoard() {
  const users = state.messages.filter((message) => message.role === "user").map((message) => renderMessage(message)).join("");
  const columns = state.agents
    .map((agent) => {
      const items = state.messages.filter((message) => message.role === "assistant" && message.agent_id === agent.id);
      const body = items.length
        ? items.map((message) => renderMessage(message, true)).join("")
        : `<p class="muted">Nothing in this column yet</p>`;
      return `<section class="col agent-${agent.id}"><header><span class="dot"></span><strong>${escapeHtml(agent.role)}</strong><em>${escapeHtml(agent.duty)}</em></header><div class="col-body">${body}</div></section>`;
    })
    .join("");
  return `<div class="user-stack">${users}</div><div class="board">${columns}</div>`;
}

function transcriptHtml() {
  if (!state.messages.length) {
    if (state.route === "task") return state.view === "board" ? renderBoard() : renderEmptyTask();
    const agent = agentById(state.route);
    return agent ? renderEmptyAgent(agent) : "";
  }
  if (state.route === "task" && state.view === "board") return renderBoard();
  return `<div class="stack">${state.messages.map((message) => renderMessage(message)).join("")}</div>`;
}

function renderTranscript() {
  const transcript = document.querySelector("#transcript");
  if (!transcript) return;
  const nearBottom = transcript.scrollHeight - transcript.scrollTop - transcript.clientHeight < 140;
  transcript.innerHTML = transcriptHtml();
  if (state.streaming || nearBottom) transcript.scrollTop = transcript.scrollHeight;
}

function renderSidebar() {
  const taskActive = state.route === "task" ? "active" : "";
  const agentNav = state.agents
    .map((agent) => {
      const active = state.route === agent.id ? "active" : "";
      return `<button type="button" class="${active} agent-${agent.id}" data-route="${agent.id}"><span class="dot"></span>${escapeHtml(agent.role)} chat</button>`;
    })
    .join("");
  const promptNav = state.agents
    .map((agent) => {
      const active = state.route === "manage" && state.manageAgent === agent.id ? "active" : "";
      return `<button type="button" class="${active} agent-${agent.id}" data-route="manage" data-manage-agent="${agent.id}"><span class="dot"></span>${escapeHtml(agent.role)} prompt</button>`;
    })
    .join("");
  const githubNav = [
    ["settings", "Settings"],
    ["branches", "Branches"],
    ["files", "Files"],
    ["commits", "Commits"],
    ["pulls", "Pull requests"],
    ["issues", "Issues"],
  ].map(([tab, label]) => {
    const active = state.route === "github" && state.githubTab === tab ? "active" : "";
    const disabled = tab !== "settings" && !state.githubRepo ? "disabled" : "";
    return `<button type="button" class="${active} github-nav-item" data-route="github" data-github-tab="${tab}" ${disabled}><span class="dot"></span>${label}</button>`;
  }).join("");
  const sessions = state.sessions.length
    ? state.sessions
        .map((session) => {
          const active = state.session && state.session.id === session.id ? "active" : "";
          return `<div class="session-row ${active}">
            <button type="button" class="session-open" data-session="${session.id}">
              <span class="session-title">${escapeHtml(session.title)}</span>
              <span class="session-time">${escapeHtml(formatTime(session.updated_at))}</span>
            </button>
            <button type="button" class="icon-btn" data-delete="${session.id}" aria-label="Delete session">×</button>
          </div>`;
        })
        .join("")
    : `<p class="empty-side">No sessions yet</p>`;
  const sessionBlock = state.route === "manage" || state.route === "github"
    ? ""
    : `<div class="session-block ${state.sessions.length ? "" : "session-block-empty"}">
      <div class="nav-label">Recent</div>
      <div class="session-list">${sessions}</div>
    </div>`;
  const githubExpanded = state.githubMenuOpen;
  const keyOn = state.settings && state.settings.has_api_key;
  const keyText = keyOn ? state.settings.model : "No API key";
  document.querySelector("#sidebar").innerHTML = `
    <div class="brand">
      <span class="brand-mark" aria-hidden="true"><i></i><i></i><i></i><i></i></span>
      <div>
        <p class="brand-name">Agent Group</p>
        <p class="brand-sub">Dev group</p>
      </div>
    </div>
    <nav class="nav" aria-label="Pages">
      <div class="nav-label">Chat</div>
      <button type="button" class="${taskActive}" data-route="task"><span class="dot"></span>Group run</button>
      ${agentNav}
      <div class="nav-label">Prompt maintenance</div>
      ${promptNav}
      <div class="nav-label">Integrations</div>
      <button type="button" class="github-toggle ${state.route === "github" ? "active" : ""}" id="toggle-github-menu" aria-expanded="${githubExpanded}">
        <span><span class="dot"></span>GitHub</span><span class="nav-chevron" aria-hidden="true">›</span>
      </button>
      ${githubExpanded ? `<div class="github-subnav">${githubNav}</div>` : ""}
    </nav>
    ${sessionBlock}
    <button type="button" class="key-status" id="open-settings">
      <span class="status-dot ${keyOn ? "on" : ""}"></span>
      <span>${escapeHtml(keyText)}</span>
    </button>`;
}

function renderPage() {
  const agent = state.route === "task" ? null : agentById(state.route);
  const title = state.session
    ? state.session.title
    : state.route === "task"
      ? "Group task"
      : `${agent ? agent.role : ""} chat`;
  document.title = `${title} · Agent Group`;
  const sub = state.route === "task"
    ? "Coordinate leads the configured workflow. Repository actions are available from the GitHub workspace and are not executed by chat."
    : agent
      ? agent.duty
      : "";
  const viewToggle = state.route === "task"
    ? `<div class="segmented" role="tablist">
        <button type="button" data-view="timeline" class="${state.view === "timeline" ? "active" : ""}">Timeline</button>
        <button type="button" data-view="board" class="${state.view === "board" ? "active" : ""}">Board</button>
      </div>`
    : "";
  const target = state.route === "task"
    ? `<label>Send to
        <select id="target-agent">
          ${state.agents
            .map(
              (item) =>
                `<option value="${item.id}" ${item.id === state.targetAgent ? "selected" : ""}>${escapeHtml(item.role)}</option>`
            )
            .join("")}
        </select>
      </label>
      <button type="button" class="ghost" id="sample-btn">Insert sample</button>`
    : "";
  const runButton = state.route === "task" ? `<button type="button" class="primary" id="run-btn">Run full workflow</button>` : "";
  const githubShortcut = agent && agent.github_enabled
    ? `<button type="button" class="ghost" data-route="github" data-github-tab="branches">GitHub</button>`
    : "";
  const newChatButton = state.session || state.messages.length
    ? `<button type="button" class="ghost" id="new-chat">New chat</button>`
    : "";
  const draft = state.drafts[state.route] || "";
  const placeholder = state.route === "task"
    ? "Describe the source table, path, primary key, frequency, and quality rules"
    : `Message for ${agent ? agent.role : "the agent"}`;
  const rail = state.route === "task" ? renderRail() : "";
  const error = state.error ? `<div class="error-banner">${escapeHtml(state.error)}</div>` : "";
  document.querySelector("#main").innerHTML = `
    <section class="page">
      <header class="page-head">
        <div>
          ${state.route === "task" ? "" : `<p class="kicker">${escapeHtml(agent ? agent.name : "")}</p>`}
          <h1 id="page-title">${escapeHtml(title)}</h1>
          <p class="sub">${escapeHtml(sub)}</p>
        </div>
        <div class="head-actions">
          ${viewToggle}
          ${githubShortcut}
          ${newChatButton}
        </div>
      </header>
      ${rail}
      ${error}
      <div id="transcript">${transcriptHtml()}</div>
      <form id="composer" class="composer">
        <textarea id="composer-text" rows="4" placeholder="${escapeHtml(placeholder)}">${escapeHtml(draft)}</textarea>
        <div class="composer-bar">
          <div class="composer-left">${target}</div>
          <div class="composer-actions">
            <button type="button" class="stop-btn" id="stop-btn">Stop</button>
            <button type="submit" class="ghost send-btn" id="send-btn">${state.route === "task" ? "Ask selected" : "Send"}</button>
            ${runButton}
          </div>
        </div>
        <p class="hint">${state.route === "task" ? "Ctrl + Enter asks the selected agent. Ctrl + Shift + Enter runs the full workflow." : "Ctrl + Enter sends. This chat stays with this agent."}</p>
      </form>
    </section>`;
  const input = document.querySelector("#composer-text");
  if (input) input.disabled = state.streaming;
}

function rememberAgent(detail) {
  const agent = agentById(detail.id);
  if (!agent) return;
  agent.name = detail.name;
  agent.description = detail.description;
  agent.temperature = detail.temperature;
  agent.welcome_message = detail.welcome_message;
  agent.conversation_starters = detail.conversation_starters;
  agent.trigger_agents = detail.trigger_agents;
  agent.github_enabled = detail.github_enabled;
}

function renderManage() {
  const agent = agentById(state.manageAgent);
  const detail = state.prompt;
  const config = detail ? detail.config : {};
  const nestedAgentNames = (config.nested_agents || []).map((item) => typeof item === "string" ? item : item.name);
  const triggerOptions = state.agents
    .filter((item) => item.id !== state.manageAgent)
    .map((item) => {
      const selected = nestedAgentNames.includes(item.name) || nestedAgentNames.includes(item.role) || nestedAgentNames.includes(item.id);
      return `<label><input type="checkbox" data-trigger-agent="${item.id}" ${selected ? "checked" : ""}>${escapeHtml(item.role)}</label>`;
    })
    .join("");
  const githubEnabled = (config.toolkits || []).some((toolkit) => toolkit.type === "github");
  const configValue = (key) => config[key] == null ? "" : String(config[key]);
  document.title = "Prompts · Agent Group";
  const picks = state.agents
    .map((item) => {
      const active = item.id === state.manageAgent ? "active" : "";
      return `<button type="button" class="ghost agent-${item.id} ${active}" data-route="manage" data-manage-agent="${item.id}"><span class="dot"></span>${escapeHtml(item.role)}</button>`;
    })
    .join("");
  const error = state.error ? `<div class="error-banner">${escapeHtml(state.error)}</div>` : "";
  const editor = detail
    ? `<div class="prompt-meta">
        <span>Source file <code>${escapeHtml(detail.source_file)}</code></span>
        <span class="badge ${detail.customized ? "custom" : ""}">${detail.customized ? "Customized" : "File default"}</span>
      </div>
      <div class="prompt-config">
        <label class="prompt-config-name">Agent name<input data-prompt-config="name" value="${escapeHtml(configValue("name"))}" required></label>
        <label class="prompt-config-description">Description<textarea data-prompt-config="description" rows="2">${escapeHtml(configValue("description"))}</textarea></label>
        <label class="prompt-config-temperature">Temperature<input data-prompt-config="temperature" type="number" min="0" max="2" step="0.1" value="${escapeHtml(configValue("temperature"))}" required></label>
        <label class="prompt-config-max-tokens">Max tokens<input data-prompt-config="max_tokens" type="number" min="-1" step="1" value="${escapeHtml(configValue("max_tokens"))}" required></label>
        <label class="prompt-config-agent-type">Agent type<input data-prompt-config="agent_type" value="${escapeHtml(configValue("agent_type"))}" required></label>
        <label class="prompt-config-step-limit">Step limit<input data-prompt-config="step_limit" type="number" min="1" step="1" value="${escapeHtml(configValue("step_limit"))}" required></label>
        <label class="prompt-config-welcome">Welcome message<textarea data-prompt-config="welcome_message" rows="3">${escapeHtml(configValue("welcome_message"))}</textarea></label>
        <label class="prompt-config-starters">Conversation starters<textarea data-prompt-config="conversation_starters" rows="4">${escapeHtml((config.conversation_starters || []).join("\n"))}</textarea></label>
        <fieldset class="agent-capabilities">
          <legend>Capabilities</legend>
          <div><span>Can trigger agents</span><div class="capability-agents">${triggerOptions}</div></div>
          <label class="capability-github"><input id="github-enabled" type="checkbox" ${githubEnabled ? "checked" : ""}>GitHub workspace access</label>
        </fieldset>
      </div>
      <div class="prompt-body-heading"><label class="prompt-body-label" for="prompt-text">System prompt</label><button type="button" class="ghost" id="prompt-writer-open">AI Writer</button></div>
      <textarea id="prompt-text" spellcheck="false" required>${escapeHtml(detail.prompt)}</textarea>
      <div class="composer-bar">
        <p id="prompt-notice" class="hint">${escapeHtml(state.promptNotice || "Ctrl + S saves. Chat uses the saved prompt on the next message.")}</p>
        <div class="composer-actions">
          <button type="button" class="ghost" id="prompt-reset">Reset to file</button>
          <button type="button" class="primary" id="prompt-save">Save prompt</button>
        </div>
      </div>`
    : `<p class="muted">Loading the prompt from the repository file…</p>`;
  document.querySelector("#main").innerHTML = `
    <section class="page manage-page">
      <header class="page-head">
        <div>
          <p class="kicker">Agent management</p>
          <h1>Prompts</h1>
          <p class="sub">Edit the prompt for ${escapeHtml(agent ? agent.role : "an agent")}. The editor opens with the Markdown file from the repository.</p>
        </div>
      </header>
      <div class="prompt-picks">${picks}</div>
      ${error}
      <div class="prompt-editor">${editor}</div>
    </section>`;
}

function renderGithubBranches() {
  return `<section class="github-workspace">
    <header class="github-section-head"><div><h2>Branches</h2><p>List and create branches in the configured repository.</p></div><button type="button" class="ghost" data-gh-action="refresh">Refresh</button></header>
    <form id="gh-branch-form" class="github-inline-form">
      <label>New branch<input name="name" required placeholder="feature/my-change"></label>
      <label>Base branch<input name="base" value="${escapeHtml(state.githubRepo ? state.githubRepo.default_branch : "main")}" required></label>
      <button type="submit" class="primary">Create branch</button>
    </form>
    <div class="github-list">${state.githubBranches.map((branch) => `<div class="github-row"><strong>${escapeHtml(branch.name)}</strong><code>${escapeHtml((branch.commit && branch.commit.sha || "").slice(0, 10))}</code>${branch.protected ? '<span class="badge">Protected</span>' : ""}</div>`).join("") || '<p class="muted">No branches loaded.</p>'}</div>
  </section>`;
}

function renderGithubFiles() {
  const ref = state.githubTree ? state.githubTree.ref : state.githubRepo ? state.githubRepo.default_branch : "main";
  const files = state.githubTree && state.githubTree.tree ? state.githubTree.tree.filter((item) => item.type === "blob") : [];
  const selectedFile = state.githubFile;
  return `<section class="github-workspace">
    <header class="github-section-head"><div><h2>Files</h2><p>Browse UTF-8 text files and commit edits directly to a branch.</p></div></header>
    <div class="github-inline-form"><label>Branch<input id="gh-tree-ref" value="${escapeHtml(ref)}"></label><button type="button" class="ghost" data-gh-action="refresh">Load files</button></div>
    ${state.githubTree && state.githubTree.truncated ? '<p class="hint">GitHub truncated this file tree. Narrow the repository or browse a subdirectory.</p>' : ""}
    <div class="github-file-layout">
      <div class="github-file-list">${files.map((file) => `<button type="button" class="github-file-item ${selectedFile && selectedFile.path === file.path ? "active" : ""}" data-gh-file="${escapeHtml(file.path)}">${escapeHtml(file.path)}</button>`).join("") || '<p class="muted">Load a branch to browse files.</p>'}</div>
      <div class="github-file-editor">${selectedFile ? `<p class="github-file-path">${escapeHtml(selectedFile.path)} · ${escapeHtml(selectedFile.ref)}</p><textarea id="gh-file-content" spellcheck="false">${escapeHtml(selectedFile.content)}</textarea><label>Commit message<input id="gh-commit-message" value="Update ${escapeHtml(selectedFile.path)}" required></label><button type="button" class="primary" data-gh-action="save-file">Commit file</button>` : '<p class="muted">Choose a text file to open it.</p>'}</div>
    </div>
  </section>`;
}

function renderGithubCommits() {
  return `<section class="github-workspace"><header class="github-section-head"><div><h2>Commit history</h2><p>Recent commits on the default branch.</p></div><button type="button" class="ghost" data-gh-action="refresh">Refresh</button></header><div class="github-list">${state.githubCommits.map((item) => `<article class="github-row github-commit"><div><strong>${escapeHtml(item.commit.message.split("\n")[0])}</strong><p>${escapeHtml(item.commit.author.name)} · ${escapeHtml(formatTime(item.commit.author.date))}</p></div><a href="${escapeHtml(item.html_url)}" target="_blank" rel="noreferrer">${escapeHtml(item.sha.slice(0, 8))}</a></article>`).join("") || '<p class="muted">No commits loaded.</p>'}</div></section>`;
}

function renderGithubPulls() {
  const branches = state.githubBranches;
  return `<section class="github-workspace"><header class="github-section-head"><div><h2>Pull requests</h2><p>Create and review pull requests; merge is always an explicit action.</p></div><button type="button" class="ghost" data-gh-action="refresh">Refresh</button></header>
    <form id="gh-pull-form" class="github-form-grid"><label>Title<input name="title" required></label><label>Head branch<select name="head" required>${branches.map((item) => `<option value="${escapeHtml(item.name)}">${escapeHtml(item.name)}</option>`).join("")}</select></label><label>Base branch<input name="base" value="${escapeHtml(state.githubRepo ? state.githubRepo.default_branch : "main")}" required></label><label class="github-span">Description<textarea name="body" rows="3"></textarea></label><button type="submit" class="primary">Create pull request</button></form>
    <div class="github-list">${state.githubPulls.map((pull) => `<article class="github-row github-pull"><div><a href="${escapeHtml(pull.html_url)}" target="_blank" rel="noreferrer"><strong>#${pull.number} ${escapeHtml(pull.title)}</strong></a><p>${escapeHtml(pull.user.login)} · ${escapeHtml(pull.head.ref)} → ${escapeHtml(pull.base.ref)} · ${escapeHtml(pull.state)}</p></div>${pull.state === "open" ? `<button type="button" class="ghost" data-gh-action="merge" data-number="${pull.number}">Merge</button>` : ""}</article>`).join("") || '<p class="muted">No pull requests loaded.</p>'}</div>
  </section>`;
}

function renderGithubIssues() {
  return `<section class="github-workspace"><header class="github-section-head"><div><h2>Issues</h2><p>Create and close repository issues.</p></div><button type="button" class="ghost" data-gh-action="refresh">Refresh</button></header>
    <form id="gh-issue-form" class="github-form-grid"><label>Title<input name="title" required></label><label>Labels<input name="labels" placeholder="bug, enhancement"></label><label class="github-span">Description<textarea name="body" rows="3"></textarea></label><button type="submit" class="primary">Create issue</button></form>
    <div class="github-list">${state.githubIssues.map((issue) => `<article class="github-row github-pull"><div><a href="${escapeHtml(issue.html_url)}" target="_blank" rel="noreferrer"><strong>#${issue.number} ${escapeHtml(issue.title)}</strong></a><p>${escapeHtml(issue.user.login)} · ${escapeHtml(issue.state)}</p></div>${issue.state === "open" ? `<button type="button" class="ghost" data-gh-action="close-issue" data-number="${issue.number}">Close</button>` : ""}</article>`).join("") || '<p class="muted">No issues loaded.</p>'}</div>
  </section>`;
}

function renderGithub() {
  const config = state.githubSettings || {};
  const tabs = ["branches", "files", "commits", "pulls", "issues"];
  const labels = { branches: "Branches", files: "Files", commits: "Commits", pulls: "Pull requests", issues: "Issues" };
  const content = state.githubTab === "settings" ? "" : state.githubRepo ? {
    branches: renderGithubBranches,
    files: renderGithubFiles,
    commits: renderGithubCommits,
    pulls: renderGithubPulls,
    issues: renderGithubIssues,
  }[state.githubTab]() : '<section class="github-workspace github-pending"><p>Connect a repository to use GitHub tools.</p></section>';
  const savedRepo = config.owner && config.repo ? `${config.owner}/${config.repo}` : "";
  const repoName = state.githubRepo ? state.githubRepo.full_name : savedRepo;
  const hasSavedConnection = config.has_token && config.owner && config.repo;
  const connectionPanel = state.githubConfigOpen || state.githubTab === "settings" || !hasSavedConnection
    ? `<section class="github-settings">
        <h2>GitHub settings</h2>
        <div class="github-form-grid">
          <label>Repository<input id="gh-repository" value="${escapeHtml(savedRepo)}" placeholder="owner/repository" autocomplete="off"></label>
          <label>Authentication token<input id="gh-token" type="password" autocomplete="new-password" placeholder="${config.has_token ? `Saved token ${escapeHtml(config.token_hint)} · blank keeps it` : "Personal access token"}"></label>
          <div class="github-actions"><button type="button" class="primary" id="gh-test">Save and connect</button>${config.has_token ? '<button type="button" class="ghost" data-gh-action="remove-token">Remove token</button>' : ""}</div>
        </div>
      </section>`
    : `<section class="github-connection"><div><strong>${escapeHtml(repoName)}</strong><span>${state.githubRepo ? `Connected · ${escapeHtml(state.githubRepo.default_branch)}` : `Saved token ${escapeHtml(config.token_hint || "")}`}</span></div><div class="github-actions"><button type="button" class="ghost" id="gh-edit">Change</button><button type="button" class="primary" id="gh-test">Connect</button>${config.has_token ? '<button type="button" class="ghost" data-gh-action="remove-token">Remove token</button>' : ""}</div></section>`;
  const status = state.githubNotice ? `<p class="github-notice">${escapeHtml(state.githubNotice)}</p>` : "";
  const error = state.githubError ? `<div class="error-banner">${escapeHtml(state.githubError)}</div>` : "";
  document.title = "GitHub · Agent Group";
  document.querySelector("#main").innerHTML = `
    <section class="page github-page">
      <header class="page-head"><div><h1>GitHub</h1></div></header>
      ${connectionPanel}
      ${status}
      ${error}
      ${content}
    </section>`;
}

async function saveGithubSettings() {
  const token = document.querySelector("#gh-token").value.trim();
  const repository = document.querySelector("#gh-repository").value.trim().split("/");
  if (repository.length !== 2 || !repository[0] || !repository[1]) {
    throw new Error("Enter the repository as owner/repository");
  }
  const payload = {
    owner: repository[0],
    repo: repository[1],
  };
  if (token) payload.token = token;
  state.githubSettings = await api("/api/github/settings", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  document.querySelector("#gh-token").value = "";
  state.githubRepo = null;
  state.githubNotice = "Settings saved locally.";
  state.githubError = "";
  renderGithub();
}

async function connectGithub() {
  if (state.githubConfigOpen) await saveGithubSettings();
  state.githubRepo = await api("/api/github/test", { method: "POST" });
  state.githubTab = state.githubTab === "settings" ? "branches" : state.githubTab;
  state.githubConfigOpen = false;
  state.githubNotice = `Connected to ${state.githubRepo.full_name}.`;
  state.githubError = "";
  if (location.hash !== currentHash()) {
    ignoreHash = true;
    location.hash = currentHash();
  }
  renderGithubSidebar();
  await refreshGithubTab();
}

function renderGithubSidebar() {
  renderSidebar();
}

function selectGithubTab(tab) {
  state.githubTab = tab;
  state.githubMenuOpen = true;
  state.githubConfigOpen = tab === "settings";
  state.githubNotice = "";
  state.githubError = "";
  state.githubFile = null;
  if (location.hash !== currentHash()) {
    ignoreHash = true;
    location.hash = currentHash();
  }
  renderSidebar();
  renderGithub();
  if (tab !== "settings" && state.githubRepo) refreshGithubTab();
}

async function refreshGithubTab() {
  state.githubError = "";
  try {
    const query = state.githubTab === "files"
      ? `/api/github/tree?ref=${encodeURIComponent(document.querySelector("#gh-tree-ref")?.value || "")}`
      : state.githubTab === "commits"
        ? `/api/github/commits?ref=${encodeURIComponent(state.githubRepo ? state.githubRepo.default_branch : "")}`
        : `/api/github/${state.githubTab === "pulls" ? "pulls" : state.githubTab}`;
    const data = await api(query);
    if (state.githubTab === "branches") state.githubBranches = data;
    if (state.githubTab === "files") state.githubTree = data;
    if (state.githubTab === "commits") state.githubCommits = data;
    if (state.githubTab === "pulls") state.githubPulls = data;
    if (state.githubTab === "issues") state.githubIssues = data;
  } catch (error) {
    state.githubError = error.message || "GitHub request failed";
  }
  renderGithub();
}

async function performGithubAction(action, target) {
  state.githubError = "";
  state.githubNotice = "";
  try {
    if (action === "save-settings") {
      await saveGithubSettings();
      return;
    }
    if (action === "remove-token") {
      if (!window.confirm("Remove the saved GitHub token from this machine?")) return;
      state.githubSettings = await api("/api/github/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ owner: state.githubSettings.owner, repo: state.githubSettings.repo, clear_token: true }),
      });
      state.githubRepo = null;
      state.githubConfigOpen = true;
      state.githubNotice = "Saved token removed.";
      renderGithub();
      return;
    }
    if (action === "connect") {
      await connectGithub();
      return;
    }
    if (action === "refresh") {
      await refreshGithubTab();
      return;
    }
    if (action === "create-branch") {
      const form = document.querySelector("#gh-branch-form");
      await api("/api/github/branches", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(form))) });
      state.githubNotice = "Branch created.";
      await refreshGithubTab();
      return;
    }
    if (action === "open-file") {
      const path = target;
      const ref = state.githubTree ? state.githubTree.ref : "";
      state.githubFile = await api(`/api/github/file?path=${encodeURIComponent(path)}&ref=${encodeURIComponent(ref)}`);
      renderGithub();
      return;
    }
    if (action === "save-file") {
      if (!state.githubFile) return;
      if (!window.confirm(`Commit changes to ${state.githubFile.path} on ${state.githubFile.ref}?`)) return;
      const content = document.querySelector("#gh-file-content").value;
      const message = document.querySelector("#gh-commit-message").value.trim();
      await api("/api/github/file", { method: "PUT", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ path: state.githubFile.path, content, message, branch: state.githubFile.ref }) });
      state.githubNotice = "File committed.";
      state.githubFile = { ...state.githubFile, content };
      renderGithub();
      return;
    }
    if (action === "create-pull") {
      const form = document.querySelector("#gh-pull-form");
      await api("/api/github/pulls", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(Object.fromEntries(new FormData(form))) });
      state.githubNotice = "Pull request created.";
      await refreshGithubTab();
      return;
    }
    if (action === "merge") {
      if (!window.confirm(`Merge pull request #${target}?`)) return;
      await api(`/api/github/pulls/${target}/merge`, { method: "PUT" });
      state.githubNotice = `Pull request #${target} merged.`;
      await refreshGithubTab();
      return;
    }
    if (action === "create-issue") {
      const form = document.querySelector("#gh-issue-form");
      const values = Object.fromEntries(new FormData(form));
      values.labels = values.labels ? values.labels.split(",").map((label) => label.trim()).filter(Boolean) : [];
      await api("/api/github/issues", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(values) });
      state.githubNotice = "Issue created.";
      await refreshGithubTab();
      return;
    }
    if (action === "close-issue") {
      if (!window.confirm(`Close issue #${target}?`)) return;
      await api(`/api/github/issues/${target}`, { method: "PATCH", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ state: "closed" }) });
      state.githubNotice = `Issue #${target} closed.`;
      await refreshGithubTab();
    }
  } catch (error) {
    state.githubError = error.message || "GitHub request failed";
    renderGithub();
  }
}

function promptFormValue() {
  if (!state.prompt) return null;
  const config = { ...state.prompt.config };
  delete config.model;
  for (const key of ["name", "description", "agent_type", "welcome_message"]) {
    const input = document.querySelector(`[data-prompt-config="${key}"]`);
    if (input) config[key] = input.value;
  }
  for (const key of ["temperature", "max_tokens", "step_limit"]) {
    const input = document.querySelector(`[data-prompt-config="${key}"]`);
    if (input) config[key] = Number(input.value);
  }
  const starters = document.querySelector('[data-prompt-config="conversation_starters"]');
  if (starters) config.conversation_starters = starters.value.split("\n").filter((item) => item.trim());
  config.nested_agents = Array.from(document.querySelectorAll("[data-trigger-agent]:checked"))
    .map((input) => agentById(input.dataset.triggerAgent))
    .filter(Boolean)
    .map((agent) => ({ name: agent.name }));
  const githubEnabled = document.querySelector("#github-enabled")?.checked || false;
  const toolkits = Array.isArray(config.toolkits) ? [...config.toolkits] : [];
  const githubToolkitIndex = toolkits.findIndex((toolkit) => toolkit.type === "github");
  if (githubEnabled && githubToolkitIndex < 0) {
    toolkits.push({ toolkit: "github", type: "github", meta: { label: "GitHub" }, settings: {}, tools: [] });
  } else if (!githubEnabled && githubToolkitIndex >= 0) {
    toolkits.splice(githubToolkitIndex, 1);
  }
  if (toolkits.length || Object.hasOwn(config, "toolkits")) config.toolkits = toolkits;
  const body = document.querySelector("#prompt-text");
  return { config, prompt: body ? body.value : "" };
}

async function loadPrompt(agentId) {
  const detail = await api(`/api/agents/${agentId}/prompt`);
  if (state.route !== "manage" || state.manageAgent !== agentId) return;
  state.prompt = detail;
  state.error = "";
  rememberAgent(detail);
  renderManage();
}

async function savePrompt() {
  if (!document.querySelector("#prompt-text") || state.route !== "manage") return;
  const form = document.querySelector(".prompt-editor");
  if (form && !Array.from(form.querySelectorAll("input, textarea")).every((input) => input.reportValidity())) return;
  try {
    const detail = await api(`/api/agents/${state.manageAgent}/prompt`, {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(promptFormValue()),
    });
    state.prompt = detail;
    state.promptNotice = "Saved. The next message uses this prompt.";
    state.error = "";
    rememberAgent(detail);
    renderManage();
    renderSidebar();
  } catch (error) {
    state.error = error.message || "The prompt was not saved";
    let banner = document.querySelector(".error-banner");
    if (!banner) {
      banner = document.createElement("div");
      banner.className = "error-banner";
      const picks = document.querySelector(".prompt-picks");
      if (picks && picks.parentNode) picks.parentNode.insertBefore(banner, picks.nextSibling);
    }
    if (banner) banner.textContent = state.error;
  }
}

function openPromptWriter() {
  document.querySelector("#prompt-writer-requirements").value = "";
  document.querySelector("#prompt-writer-result").value = "";
  document.querySelector("#prompt-writer-error").textContent = "";
  document.querySelector("#prompt-writer-preview").hidden = true;
  document.querySelector("#prompt-writer-use").disabled = true;
  document.querySelector("#prompt-writer-dialog").showModal();
  document.querySelector("#prompt-writer-requirements").focus();
}

async function generatePromptDraft() {
  const requirements = document.querySelector("#prompt-writer-requirements").value.trim();
  const currentPrompt = document.querySelector("#prompt-text")?.value || "";
  const generateButton = document.querySelector("#prompt-writer-generate");
  const errorBox = document.querySelector("#prompt-writer-error");
  if (!requirements) {
    errorBox.textContent = "Enter the changes you want the AI Writer to make.";
    return;
  }
  generateButton.disabled = true;
  generateButton.textContent = "Generating…";
  errorBox.textContent = "";
  try {
    const result = await api(`/api/agents/${state.manageAgent}/prompt/assist`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ current_prompt: currentPrompt, requirements }),
    });
    document.querySelector("#prompt-writer-result").value = result.prompt;
    document.querySelector("#prompt-writer-preview").hidden = false;
    document.querySelector("#prompt-writer-use").disabled = false;
  } catch (error) {
    errorBox.textContent = error.message || "AI Writer could not generate a draft.";
  } finally {
    generateButton.disabled = false;
    generateButton.textContent = "Generate merged draft";
  }
}

function insertPromptDraft() {
  const result = document.querySelector("#prompt-writer-result").value.trim();
  if (!result) return;
  document.querySelector("#prompt-text").value = result;
  document.querySelector("#prompt-writer-dialog").close();
  state.promptNotice = "AI draft inserted. Review it, then save the prompt.";
  const notice = document.querySelector("#prompt-notice");
  if (notice) notice.textContent = state.promptNotice;
}

async function resetPrompt() {
  if (!window.confirm("Replace this editor with the Markdown file from the repository?")) return;
  const detail = await api(`/api/agents/${state.manageAgent}/prompt/reset`, { method: "POST" });
  state.prompt = detail;
  state.promptNotice = "Restored from the repository file.";
  state.error = "";
  rememberAgent(detail);
  renderManage();
  renderSidebar();
}

function confirmDiscard() {
  return true;
}

async function loadSessions() {
  const query = state.route === "task"
    ? "/api/sessions?kind=group"
    : `/api/sessions?kind=agent&agent_id=${state.route}`;
  state.sessions = await api(query);
  renderSidebar();
}

function captureDraft() {
  const input = document.querySelector("#composer-text");
  if (input) state.drafts[state.route] = input.value;
}

function setRoute(route, manageAgent, githubTab) {
  if (state.streaming && state.abort) state.abort.abort();
  flowId += 1;
  captureDraft();
  state.route = route;
  if (manageAgent) state.manageAgent = manageAgent;
  if (githubTab) state.githubTab = githubTab;
  state.session = null;
  state.messages = [];
  state.error = "";
  state.promptNotice = "";
  state.activeStep = null;
  state.streaming = false;
  document.body.classList.remove("is-streaming");
  const nextHash = currentHash();
  if (location.hash !== nextHash) {
    ignoreHash = true;
    location.hash = nextHash;
  }
  if (route === "manage") {
    state.prompt = null;
    renderSidebar();
    renderManage();
    loadPrompt(state.manageAgent).catch((error) => {
      state.error = error.message;
      renderManage();
    });
    return;
  }
  if (route === "github") {
    state.githubMenuOpen = true;
    state.githubConfigOpen = state.githubTab === "settings";
    renderSidebar();
    renderGithub();
    if (state.githubTab !== "settings" && state.githubRepo) refreshGithubTab();
    return;
  }
  renderPage();
  loadSessions().catch((error) => {
    state.error = error.message;
    renderPage();
  });
}

function handleStreamEvent(event) {
  if (event.type === "session") {
    state.session = event.session;
    const title = document.querySelector("#page-title");
    if (title) title.textContent = event.session.title;
    return;
  }
  if (event.type === "message" && event.message.role === "user") {
    const index = state.messages.findIndex((message) => message.id === "optimistic");
    if (index >= 0) state.messages.splice(index, 1, event.message);
    else state.messages.push(event.message);
    renderTranscript();
    return;
  }
  if (event.type === "step_start") {
    state.activeStep = event.step_id;
    state.messages.push({
      id: `live-${event.step_id}`,
      role: "assistant",
      agent_id: event.agent_id,
      step_id: event.step_id,
      step_title: event.step_title,
      content: "",
      reasoning: "",
      live: true,
      created_at: new Date().toISOString(),
    });
    renderPageKeepingDraft();
    return;
  }
  if (event.type === "delta") {
    const live = state.messages.findLast((message) => message.live);
    if (!live) return;
    if (event.channel === "reasoning") live.reasoning = `${live.reasoning || ""}${event.content || ""}`;
    else live.content += event.content || "";
    renderTranscript();
    const rail = document.querySelector(".rail");
    if (rail) rail.outerHTML = renderRail();
    return;
  }
  if (event.type === "step_end") {
    const index = state.messages.findLastIndex((message) => message.live);
    if (index >= 0) {
      event.message.reasoning = state.messages[index].reasoning;
      state.messages.splice(index, 1, event.message);
    } else {
      state.messages.push(event.message);
    }
    state.activeStep = null;
    renderTranscript();
    const rail = document.querySelector(".rail");
    if (rail) rail.outerHTML = renderRail();
    return;
  }
  if (event.type === "error") {
    state.error = event.detail || "Generation failed";
    const live = state.messages.findLast((message) => message.live);
    if (live) live.live = false;
    const banner = document.querySelector(".error-banner");
    if (!banner) renderPageKeepingDraft();
    else banner.textContent = state.error;
  }
}

function renderPageKeepingDraft() {
  captureDraft();
  renderPage();
}

async function submit(pipeline) {
  if (state.streaming) return;
  const input = document.querySelector("#composer-text");
  const text = (input ? input.value : "").trim();
  if (!text) return;
  if (!state.settings || !state.settings.has_api_key) {
    openSettings();
    return;
  }
  const currentFlow = ++flowId;
  state.error = "";
  state.drafts[state.route] = "";
  state.messages.push({
    id: "optimistic",
    role: "user",
    content: text,
    created_at: new Date().toISOString(),
  });
  state.streaming = true;
  document.body.classList.add("is-streaming");
  renderPage();
  const controller = new AbortController();
  state.abort = controller;
  const payload = pipeline
    ? { message: text, session_id: state.session ? state.session.id : null }
    : {
        message: text,
        session_id: state.session ? state.session.id : null,
        agent_id: state.route === "task" ? state.targetAgent : state.route,
        scope: state.route === "task" ? "group" : "agent",
      };
  try {
    const response = await fetch(pipeline ? "/api/tasks/run" : "/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
      signal: controller.signal,
    });
    if (!response.ok) {
      const data = await response.json().catch(() => ({}));
      throw new Error(errorMessage(data));
    }
    await readSSE(response, handleStreamEvent);
  } catch (error) {
    if (currentFlow !== flowId) return;
    if (error.name === "AbortError") {
      if (state.session) {
        const data = await api(`/api/sessions/${state.session.id}`).catch(() => null);
        if (data && currentFlow === flowId) {
          state.session = data;
          state.messages = data.messages;
        }
      }
    } else {
      state.error = error.message || "The request did not finish";
      if (state.messages.at(-1) && state.messages.at(-1).id === "optimistic") {
        state.messages.pop();
        state.drafts[state.route] = text;
      }
    }
  } finally {
    if (currentFlow !== flowId) return;
    state.streaming = false;
    state.activeStep = null;
    state.abort = null;
    document.body.classList.remove("is-streaming");
    await loadSessions().catch(() => {});
    renderPage();
  }
}

async function openSession(id) {
  if (state.streaming) return;
  const data = await api(`/api/sessions/${id}`);
  state.session = data;
  state.messages = data.messages;
  state.error = "";
  renderPage();
  renderSidebar();
}

function openSettings() {
  const dialog = document.querySelector("#settings-dialog");
  const select = document.querySelector("#model");
  const models = (state.settings && state.settings.models) || ["deepseek-flash", "deepseek-v4-pro"];
  const current = state.settings ? state.settings.model : models[0];
  select.innerHTML = models
    .map((model) => `<option value="${escapeHtml(model)}" ${model === current ? "selected" : ""}>${escapeHtml(model)}</option>`)
    .join("");
  document.querySelector("#key-hint").textContent = state.settings && state.settings.has_api_key
    ? `Current key ${state.settings.key_hint}`
    : "No key yet. Saving writes it to back_end/.env.";
  document.querySelector("#settings-error").textContent = "";
  dialog.showModal();
  document.querySelector("#api-key").focus();
}

function bindEvents() {
  document.addEventListener("click", async (event) => {
  try {
    if (event.target.closest("#toggle-github-menu")) {
      state.githubMenuOpen = !state.githubMenuOpen;
      renderSidebar();
      return;
    }
    const routeButton = event.target.closest("[data-route]");
    if (routeButton && routeButton.dataset.route) {
      const route = routeButton.dataset.route;
      const manageAgent = routeButton.dataset.manageAgent || null;
      if (route === "manage") {
        const nextAgent = manageAgent || state.manageAgent || "coordinate";
        if (state.route === "manage" && nextAgent === state.manageAgent) return;
        if (!confirmDiscard()) return;
        setRoute("manage", nextAgent);
        return;
      }
      if (route === "github") {
        const nextTab = routeButton.dataset.githubTab || "settings";
        if (state.route === "github") {
          if (nextTab !== state.githubTab) selectGithubTab(nextTab);
          return;
        }
        if (!confirmDiscard()) return;
        setRoute("github", null, nextTab);
        return;
      }
      if (route !== state.route) {
        if (!confirmDiscard()) return;
        setRoute(route);
      }
      return;
    }
    const githubFile = event.target.closest("[data-gh-file]");
    if (githubFile) {
      await performGithubAction("open-file", githubFile.dataset.ghFile);
      return;
    }
    const githubAction = event.target.closest("[data-gh-action]");
    if (githubAction) {
      await performGithubAction(githubAction.dataset.ghAction, githubAction.dataset.number);
      return;
    }
    if (event.target.closest("#gh-save")) {
      await performGithubAction("save-settings");
      return;
    }
    if (event.target.closest("#gh-edit")) {
      state.githubConfigOpen = true;
      renderGithub();
      return;
    }
    if (event.target.closest("#gh-test")) {
      await performGithubAction("connect");
      return;
    }
    const deleteButton = event.target.closest("[data-delete]");
    if (deleteButton) {
      const id = deleteButton.dataset.delete;
      if (!window.confirm("Delete this session?")) return;
      await api(`/api/sessions/${id}`, { method: "DELETE" });
      if (state.session && state.session.id === id) {
        state.session = null;
        state.messages = [];
      }
      await loadSessions();
      renderPage();
      return;
    }
    const sessionButton = event.target.closest("[data-session]");
    if (sessionButton) {
      await openSession(sessionButton.dataset.session);
      return;
    }
    const starter = event.target.closest("[data-starter]");
    if (starter) {
      const agent = agentById(state.route);
      const text = agent && agent.conversation_starters[Number(starter.dataset.starter)];
      const input = document.querySelector("#composer-text");
      if (text && input) {
        input.value = text;
        state.drafts[state.route] = text;
        input.focus();
      }
      return;
    }
    const viewButton = event.target.closest("[data-view]");
    if (viewButton) {
      state.view = viewButton.dataset.view;
      localStorage.setItem("ag-view", state.view);
      document.querySelectorAll("[data-view]").forEach((button) => {
        button.classList.toggle("active", button.dataset.view === state.view);
      });
      renderTranscript();
      return;
    }
    if (event.target.closest("#new-chat")) {
      captureDraft();
      state.session = null;
      state.messages = [];
      state.error = "";
      state.drafts[state.route] = "";
      renderPage();
      renderSidebar();
      return;
    }
    if (event.target.closest("#sample-btn")) {
      const input = document.querySelector("#composer-text");
      if (!input) return;
      input.value = SAMPLE_TASK;
      state.drafts[state.route] = SAMPLE_TASK;
      input.focus();
      return;
    }
    if (event.target.closest("#run-btn")) {
      submit(true);
      return;
    }
    if (event.target.closest("#stop-btn")) {
      if (state.abort) state.abort.abort();
      return;
    }
    if (event.target.closest("#open-settings")) openSettings();
    if (event.target.closest("#close-settings")) document.querySelector("#settings-dialog").close();
    if (event.target.closest("#prompt-save")) {
      await savePrompt();
      return;
    }
    if (event.target.closest("#prompt-reset")) {
      await resetPrompt();
      return;
    }
    if (event.target.closest("#prompt-writer-open")) {
      openPromptWriter();
      return;
    }
    if (event.target.closest("#prompt-writer-generate")) {
      await generatePromptDraft();
      return;
    }
    if (event.target.closest("#prompt-writer-use")) {
      insertPromptDraft();
      return;
    }
    if (event.target.closest("#prompt-writer-cancel")) {
      document.querySelector("#prompt-writer-dialog").close();
      return;
    }
  } catch (error) {
    state.error = error.message || "The action did not finish";
    if (state.route === "manage") renderManage();
    else if (state.route === "github") {
      state.githubError = state.error;
      renderGithub();
    }
    else if (document.querySelector("#composer")) renderPage();
  }
  });

  document.addEventListener("submit", (event) => {
    if (["gh-branch-form", "gh-pull-form", "gh-issue-form"].includes(event.target.id)) {
      event.preventDefault();
      const action = event.target.id === "gh-branch-form"
        ? "create-branch"
        : event.target.id === "gh-pull-form"
          ? "create-pull"
          : "create-issue";
      performGithubAction(action);
      return;
    }
    if (event.target.id === "composer") {
      event.preventDefault();
      submit(false);
    }
    if (event.target.id === "settings-form") {
      event.preventDefault();
      saveSettings();
    }
  });

  document.addEventListener("change", (event) => {
    if (event.target.id === "target-agent") state.targetAgent = event.target.value;
  });

  document.addEventListener("keydown", (event) => {
    if (event.target.id === "prompt-text" && (event.ctrlKey || event.metaKey) && event.key.toLowerCase() === "s") {
      event.preventDefault();
      savePrompt();
      return;
    }
    if (event.target.id !== "composer-text") return;
    if (event.key === "Enter" && (event.ctrlKey || event.metaKey)) {
      event.preventDefault();
      submit(state.route === "task" && event.shiftKey);
    }
  });

  window.addEventListener("hashchange", () => {
    if (ignoreHash) {
      ignoreHash = false;
      return;
    }
    const parsed = readHash();
    const same = parsed.route === state.route && (parsed.route !== "manage" || parsed.manageAgent === state.manageAgent);
    const sameGithubTab = parsed.route !== "github" || parsed.githubTab === state.githubTab;
    if (same && sameGithubTab) return;
    if (!confirmDiscard()) {
      ignoreHash = true;
      location.hash = currentHash();
      return;
    }
    setRoute(parsed.route, parsed.manageAgent, parsed.githubTab);
  });
}

async function saveSettings() {
  const apiKey = document.querySelector("#api-key").value.trim();
  const model = document.querySelector("#model").value;
  const payload = { model };
  if (apiKey) payload.api_key = apiKey;
  try {
    state.settings = await api("/api/settings", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    document.querySelector("#api-key").value = "";
    document.querySelector("#settings-dialog").close();
    renderSidebar();
  } catch (error) {
    document.querySelector("#settings-error").textContent = error.message;
  }
}

async function init() {
  bindEvents();
  if (!location.hash) history.replaceState(null, "", "#/task");
  const parsed = readHash();
  state.route = parsed.route;
  state.manageAgent = parsed.manageAgent;
  state.githubTab = parsed.githubTab || "settings";
  state.githubMenuOpen = state.route === "github";
  try {
    const boot = await api("/api/bootstrap");
    state.agents = boot.agents;
    state.pipeline = boot.pipeline;
    state.settings = boot.settings;
    state.githubSettings = boot.github;
    state.githubConfigOpen = state.githubTab === "settings" || !(boot.github && boot.github.has_token && boot.github.owner && boot.github.repo);
    if (state.route === "manage") {
      renderSidebar();
      renderManage();
      await loadPrompt(state.manageAgent);
    } else if (state.route === "github") {
      renderSidebar();
      renderGithub();
    } else {
      await loadSessions();
      renderPage();
    }
  } catch (error) {
    document.querySelector("#main").innerHTML = `<p class="error-banner">${escapeHtml(error.message)}</p>`;
  }
}

init();

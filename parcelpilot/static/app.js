const state = { history: [], account: null };
const loginCard = document.querySelector("#login-card");
const chatCard = document.querySelector("#chat-card");
const messages = document.querySelector("#messages");
const accountBadge = document.querySelector("#account-badge");
const identitySelect = document.querySelector("#identity");

function getCookie(name) {
  const prefix = `${name}=`;
  return document.cookie.split(";").map((part) => part.trim()).find((part) => part.startsWith(prefix))?.slice(prefix.length) || "";
}

async function request(path, options = {}) {
  const headers = new Headers(options.headers || {});
  const csrf = getCookie("parcelpilot_csrf");
  if (csrf) headers.set("X-CSRF-Token", csrf);
  const response = await fetch(path, { ...options, headers, credentials: "same-origin" });
  const payload = response.status === 204 ? {} : await response.json();
  if (!response.ok) throw new Error(payload.detail || "The request could not be completed.");
  return payload;
}

function addMessage(kind, text) {
  const element = document.createElement("article");
  element.className = `message ${kind}`;
  const paragraph = document.createElement("p");
  paragraph.className = "message-text";
  if (kind === "assistant") {
    renderSafeMarkdown(paragraph, text);
  } else {
    paragraph.textContent = text;
  }
  element.append(paragraph);
  messages.append(element);
  messages.scrollTop = messages.scrollHeight;
  return element;
}

function renderSafeMarkdown(parent, text) {
  // Deliberately supports only inline emphasis/code. All content is inserted as
  // text nodes, so model output can never become executable HTML.
  const tokenPattern = /(\*\*[^*]+\*\*|`[^`]+`)/g;
  let cursor = 0;
  for (const match of text.matchAll(tokenPattern)) {
    if (match.index > cursor) parent.append(document.createTextNode(text.slice(cursor, match.index)));
    const token = match[0];
    const element = document.createElement(token.startsWith("**") ? "strong" : "code");
    element.textContent = token.slice(token.startsWith("**") ? 2 : 1, token.startsWith("**") ? -2 : -1);
    parent.append(element);
    cursor = match.index + token.length;
  }
  if (cursor < text.length) parent.append(document.createTextNode(text.slice(cursor)));
}

function addList(parent, className, entries, mapper) {
  const list = document.createElement("ul");
  list.className = className;
  entries.forEach((entry) => {
    const item = document.createElement("li");
    item.textContent = mapper(entry);
    list.append(item);
  });
  parent.append(list);
}

function renderTrace(parent, trace) {
  if (!trace?.length) return;
  const details = document.createElement("details");
  details.className = "tool-trace";
  const summary = document.createElement("summary");
  summary.textContent = `Tool activity (${trace.length})`;
  details.append(summary);
  addList(details, "tool-list", trace, (entry) => `${entry.name}: ${entry.summary}`);
  parent.append(details);
}

function renderCitations(parent, citations) {
  if (!citations?.length) return;
  const section = document.createElement("section");
  section.className = "citations";
  const heading = document.createElement("strong");
  heading.textContent = "Evidence trail";
  section.append(heading);
  const chips = document.createElement("div");
  chips.className = "evidence-chips";
  citations.forEach((citation) => {
    const chip = document.createElement("span");
    chip.className = `evidence-chip ${citation.relation}`;
    const label = citation.relation === "overridden" ? "Default overridden" : citation.relation === "applied" ? "Applied rule" : "Retrieved evidence";
    chip.textContent = `${label}: ${citation.source_id} · ${citation.section}`;
    chips.append(chip);
  });
  section.append(chips);
  parent.append(section);
}

function renderVerification(parent, result) {
  if (!result.needs_verification) return;
  const notice = document.createElement("div");
  notice.className = "verification";
  notice.textContent = result.verification_reasons?.join(" ") || "This answer needs human verification.";
  parent.append(notice);
}

function renderReliability(parent, reliability) {
  if (!reliability || (!reliability.signals?.length && reliability.state === "grounded")) return;
  const section = document.createElement("section");
  section.className = `reliability ${reliability.state}`;
  const title = document.createElement("strong");
  title.textContent = reliability.state === "insufficient_evidence" ? "Unable to verify" : reliability.state === "needs_verification" ? "Verification required" : "Authority filter";
  section.append(title);
  const signals = reliability.signals || [];
  addList(section, "reliability-list", signals, (signal) => `${signal.kind.replaceAll("_", " ")}: ${signal.message}`);
  parent.append(section);
}

async function confirmProposal(proposal, card) {
  const confirmButton = card.querySelector(".confirm");
  confirmButton.disabled = true;
  try {
    const result = await request(`/api/actions/${encodeURIComponent(proposal.proposal_id)}/confirm`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ expected_payload_hash: proposal.payload_hash }),
    });
    card.querySelector(".proposal-meta").textContent = `Escalation ${result.action.status.replace("_", " ")} (${result.action.action_id}).`;
    card.querySelector(".proposal-actions").remove();
  } catch (error) {
    confirmButton.disabled = false;
    addMessage("system", error.message);
  }
}

async function cancelProposal(proposal, card) {
  try {
    const result = await request(`/api/actions/${encodeURIComponent(proposal.proposal_id)}/cancel`, { method: "POST" });
    card.querySelector(".proposal-meta").textContent = `Proposal ${result.proposal.status}.`;
    card.querySelector(".proposal-actions").remove();
  } catch (error) {
    addMessage("system", error.message);
  }
}

function renderProposal(parent, proposal) {
  const card = document.createElement("section");
  card.className = "proposal";
  const title = document.createElement("p");
  title.className = "proposal-title";
  title.textContent = "Escalation ready for confirmation";
  const summary = document.createElement("p");
  summary.className = "proposal-meta";
  summary.textContent = proposal.summary;
  const meta = document.createElement("p");
  meta.className = "proposal-meta";
  meta.textContent = `Reason: ${proposal.reason_code}. Expires: ${new Date(proposal.expires_at).toLocaleTimeString()}.`;
  const actions = document.createElement("div");
  actions.className = "proposal-actions";
  const confirm = document.createElement("button");
  confirm.className = "confirm";
  confirm.textContent = "Confirm escalation";
  confirm.addEventListener("click", () => confirmProposal(proposal, card));
  const cancel = document.createElement("button");
  cancel.className = "danger";
  cancel.textContent = "Cancel";
  cancel.addEventListener("click", () => cancelProposal(proposal, card));
  actions.append(confirm, cancel);
  card.append(title, summary, meta, actions);
  parent.append(card);
}

async function loadIdentities() {
  const data = await request("/api/demo-identities");
  data.forEach((identity) => {
    const option = document.createElement("option");
    option.value = identity.identity;
    option.textContent = identity.display_name;
    identitySelect.append(option);
  });
}

document.querySelector("#login-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  try {
    await request("/auth/demo-login", { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify({ identity: identitySelect.value }) });
    const me = await request("/api/me");
    state.account = me.account;
    accountBadge.textContent = `${me.account.account_name} · ${me.account.plan}`;
    accountBadge.classList.remove("hidden");
    loginCard.classList.add("hidden");
    chatCard.classList.remove("hidden");
    addMessage("assistant", `You’re signed in to ${me.account.account_name}. I can check orders, support terms, and known issues for this account.`);
  } catch (error) {
    addMessage("system", error.message);
  }
});

document.querySelector("#chat-form").addEventListener("submit", async (event) => {
  event.preventDefault();
  const textarea = document.querySelector("#message");
  const send = document.querySelector("#send");
  const text = textarea.value.trim();
  if (!text) return;
  addMessage("user", text);
  textarea.value = "";
  send.disabled = true;
  const pending = addMessage("assistant", "Checking the relevant records and policies…");
  try {
    const result = await request("/api/chat", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ message: text, history: state.history.slice(-10) }),
    });
    pending.remove();
    const reply = addMessage("assistant", result.answer);
    renderTrace(reply, result.tool_trace);
    renderCitations(reply, result.citations);
    renderReliability(reply, result.reliability);
    renderVerification(reply, result);
    result.action_proposals?.forEach((proposal) => renderProposal(reply, proposal));
    state.history.push({ role: "user", content: text }, { role: "assistant", content: result.answer });
  } catch (error) {
    pending.remove();
    addMessage("system", error.message);
  } finally {
    send.disabled = false;
    textarea.focus();
  }
});

loadIdentities().catch((error) => addMessage("system", error.message));

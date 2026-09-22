const state = {
  connected: false,
  labels: [],
  label: "ALL",
  query: "",
  messages: [],
  nextPage: null,
  selectedId: null,
  loadingList: false,
  grokReady: false,
  deepseekReady: false,
  notionReady: false,
  llm: "grok",
  categories: {},
  categoryFilter: "all",
  classifyPrompt: "",
  taskExtractionPrompt: "",
  classifyLimit: 1000,
  classifyCategories: [],
  promptDirty: false,
  sortedView: false,
  gmailCompose: false,
  gmailModify: false,
  bulkActionRunning: false,
  scanningReplies: false,
  analysingAccount: false,
  activeRunId: "",
  scheduler: null,
  replyDraftBodies: {},
  replyDraftLoading: new Set(),
};

const CATEGORY_ORDER = ["invoice", "private", "social", "travel", "work", "other"];
const DEFAULT_CATEGORIES = [
  {
    slug: "invoice",
    name: "Invoices",
    definition:
      "bills, invoices, payment due, tax documents, order confirmations, payment receipts, tickets already paid. A PDF named like an invoice, receipt, or order is a strong hint. Cloud/disk/memory/quota alerts are not invoices.",
  },
  {
    slug: "private",
    name: "Private",
    definition:
      "one-to-one mail from a real person — family, friends, personal notes. Never private if unsubscribe or mailing_list is true, or if the mail is a blog, digest, newsletter, or subscription, even when it feels personal.",
  },
  {
    slug: "social",
    name: "Social",
    definition:
      "marketing, sales, discounts, cold outreach, social networks, community notifications, forums, newsletters, digests, blog mailings, mailing lists, subscriptions",
  },
  {
    slug: "travel",
    name: "Travel",
    definition: "flights, hotels, itineraries, boarding passes",
  },
  {
    slug: "work",
    name: "Work",
    definition: "colleagues, workplace tools, jobs, professional mail that is not a newsletter",
  },
  {
    slug: "other",
    name: "Other",
    definition:
      "anything that does not fit, including system alerts (storage full, no memory left, backups, monitoring)",
  },
];

const els = {
  labels: document.getElementById("labels"),
  account: document.getElementById("account"),
  authBtn: document.getElementById("auth-btn"),
  searchForm: document.getElementById("search-form"),
  search: document.getElementById("search"),
  refresh: document.getElementById("refresh"),
  list: document.getElementById("message-list"),
  listMeta: document.getElementById("list-meta"),
  listEmpty: document.getElementById("list-empty"),
  bulkArchive: document.getElementById("bulk-archive"),
  bulkTrash: document.getElementById("bulk-trash"),
  loadMore: document.getElementById("load-more"),
  reader: document.getElementById("reader"),
  banner: document.getElementById("banner"),
  desk: document.getElementById("desk"),
  deskBtn: document.getElementById("desk-btn"),
  deskClose: document.getElementById("desk-close"),
  grokForm: document.getElementById("grok-form"),
  grokKey: document.getElementById("grok-key"),
  grokHint: document.getElementById("grok-hint"),
  grokClear: document.getElementById("grok-clear"),
  deepseekForm: document.getElementById("deepseek-form"),
  deepseekKey: document.getElementById("deepseek-key"),
  deepseekHint: document.getElementById("deepseek-hint"),
  deepseekClear: document.getElementById("deepseek-clear"),
  notionForm: document.getElementById("notion-form"),
  notionKey: document.getElementById("notion-key"),
  notionDatabase: document.getElementById("notion-database"),
  notionHint: document.getElementById("notion-hint"),
  notionClear: document.getElementById("notion-clear"),
  categoryBar: document.getElementById("category-bar"),
  classifyForm: document.getElementById("classify-form"),
  classifyLimit: document.getElementById("classify-limit"),
  categoryList: document.getElementById("category-list"),
  addCategory: document.getElementById("add-category"),
  promptPreview: document.getElementById("prompt-preview"),
  taskExtractionPrompt: document.getElementById("task-extraction-prompt"),
  classifyReset: document.getElementById("classify-reset"),
  promptStatus: document.getElementById("prompt-status"),
  scanRepliesBtn: document.getElementById("scan-replies-btn"),
  scanRepliesHeader: document.getElementById("scan-replies-header"),
  gmailReconnect: document.getElementById("gmail-reconnect"),
  gmailComposeNote: document.getElementById("gmail-compose-note"),
  analysisStartDate: document.getElementById("analysis-start-date"),
  analyseAccountBtn: document.getElementById("analyse-account-btn"),
  analysisSummary: document.getElementById("analysis-summary"),
  schedulerEnabled: document.getElementById("scheduler-enabled"),
  schedulerTime: document.getElementById("scheduler-time"),
  schedulerLookback: document.getElementById("scheduler-lookback"),
  schedulerNext: document.getElementById("scheduler-next"),
  schedulerSave: document.getElementById("scheduler-save"),
  schedulerRunNow: document.getElementById("scheduler-run-now"),
  schedulerLastRun: document.getElementById("scheduler-last-run"),
  schedulerRunLog: document.getElementById("scheduler-run-log"),
};

let toastTimer = null;

function showBanner(text) {
  if (toastTimer) {
    clearTimeout(toastTimer);
    toastTimer = null;
  }
  els.banner.textContent = text;
  els.banner.classList.remove("success", "error");
  els.banner.setAttribute("role", "status");
  els.banner.classList.toggle("hidden", !text);
}

function showToast(text, type = "success") {
  showBanner(text);
  els.banner.classList.add(type === "error" ? "error" : "success");
  els.banner.setAttribute("role", type === "error" ? "alert" : "status");
  toastTimer = setTimeout(() => {
    els.banner.classList.add("hidden");
    toastTimer = null;
  }, type === "error" ? 7000 : 4500);
}

async function api(path, options = {}) {
  const res = await fetch(path, options);
  const data = await res.json().catch(() => ({}));
  if (!res.ok) {
    const detail = data.detail || data.error || res.statusText;
    throw new Error(typeof detail === "string" ? detail : JSON.stringify(detail));
  }
  return data;
}

function filingCategories() {
  const cats = state.classifyCategories && state.classifyCategories.length
    ? state.classifyCategories
    : DEFAULT_CATEGORIES;
  return cats;
}

function categoryFor(message) {
  const hit = state.categories[message.id] || {};
  return hit.category || message.category || "";
}

function categoryLabel(message) {
  const hit = state.categories[message.id] || {};
  return hit.label || message.category_label || "";
}

function needsReply(message) {
  const hit = state.categories[message.id] || {};
  return Boolean(hit.needs_reply || message.needs_reply);
}

function replyReason(message) {
  const hit = state.categories[message.id] || {};
  return hit.needs_reply_reason || message.needs_reply_reason || "";
}

function replyDraftId(message) {
  const hit = state.categories[message.id] || {};
  return hit.reply_draft_id || message.reply_draft_id || "";
}

function draftBodyLoaded(messageId) {
  return Object.prototype.hasOwnProperty.call(state.replyDraftBodies, messageId);
}

async function loadMissingReplyDrafts(messages) {
  const pending = (messages || []).filter(
    (message) =>
      needsReply(message) &&
      !draftBodyLoaded(message.id) &&
      !state.replyDraftLoading.has(message.id)
  );
  if (!pending.length) return;
  pending.forEach((message) => state.replyDraftLoading.add(message.id));
  await Promise.all(
    pending.map(async (message) => {
      try {
        const data = await api(`/api/replies/draft/${encodeURIComponent(message.id)}`);
        state.replyDraftBodies[message.id] = data.body || "";
      } catch (_) {
        state.replyDraftBodies[message.id] = "";
      } finally {
        state.replyDraftLoading.delete(message.id);
      }
    })
  );
  renderList();
}

function categoryCounts() {
  const counts = { all: state.messages.length, unsorted: 0, "needs-reply": 0 };
  for (const message of state.messages) {
    if (needsReply(message)) counts["needs-reply"] += 1;
    const category = categoryFor(message);
    if (!category) {
      counts.unsorted += 1;
      continue;
    }
    counts[category] = (counts[category] || 0) + 1;
  }
  return counts;
}

function visibleMessages() {
  if (state.categoryFilter === "all") return state.messages;
  if (state.categoryFilter === "unsorted") {
    return state.messages.filter((item) => !categoryFor(item));
  }
  if (state.categoryFilter === "needs-reply") {
    return state.messages.filter((item) => needsReply(item));
  }
  return state.messages.filter((item) => categoryFor(item) === state.categoryFilter);
}

function filterLabel() {
  if (state.categoryFilter === "all") return "all";
  if (state.categoryFilter === "unsorted") return "unsorted";
  if (state.categoryFilter === "needs-reply") return "needs reply";
  const hit = filingCategories().find((item) => item.slug === state.categoryFilter);
  return (hit && hit.name) || state.categoryFilter;
}

function renderLabels() {
  if (!els.labels) return;
  const counts = categoryCounts();
  const cats = filingCategories();
  const slugs = cats.map((item) => item.slug);
  if (
    state.categoryFilter !== "all"
    && state.categoryFilter !== "unsorted"
    && state.categoryFilter !== "needs-reply"
    && !slugs.includes(state.categoryFilter)
  ) {
    state.categoryFilter = "all";
  }
  const items = [{ key: "all", name: "All" }];
  for (const cat of cats) {
    items.push({ key: cat.slug, name: cat.name || cat.slug });
  }
  if (counts["needs-reply"]) {
    items.push({ key: "needs-reply", name: "Needs reply" });
  }
  if (counts.unsorted) {
    items.push({ key: "unsorted", name: "Unsorted" });
  }
  els.labels.innerHTML = "";
  for (const item of items) {
    const btn = document.createElement("button");
    btn.type = "button";
    btn.dataset.cat = item.key;
    btn.className = "label-btn" + (state.categoryFilter === item.key ? " active" : "");
    btn.innerHTML = `<span class="label-name">${escapeHtml(item.name)}</span><span class="label-count">${counts[item.key] || 0}</span>`;
    btn.addEventListener("click", () => {
      state.categoryFilter = item.key;
      renderList();
    });
    els.labels.appendChild(btn);
  }
  if (els.categoryBar) {
    els.categoryBar.classList.add("hidden");
    els.categoryBar.innerHTML = "";
  }
}

function renderList() {
  const messages = visibleMessages();
  els.list.innerHTML = "";
  els.listEmpty.classList.toggle("hidden", messages.length > 0 || state.loadingList);
  const atCap = state.messages.length >= 1000;
  const filed = state.messages.filter((item) => categoryFor(item)).length;
  els.loadMore.classList.toggle("hidden", !state.nextPage || atCap || state.categoryFilter !== "all");
  const showBulk = state.connected && messages.length > 0;
  if (els.bulkArchive) els.bulkArchive.classList.toggle("hidden", !showBulk);
  if (els.bulkTrash) els.bulkTrash.classList.toggle("hidden", !showBulk);
  els.listMeta.textContent = state.connected
    ? `${messages.length} shown · ${state.messages.length} fetched${atCap ? " · last 1,000" : ""}${filed ? ` · ${filed} filed` : ""} · ${filterLabel().toLowerCase()}`
    : "Connect Gmail to fill the tray.";
  renderLabels();

  for (const message of messages) {
    const row = document.createElement("button");
    row.type = "button";
    row.className = "row";
    if (message.unread || message.unread) row.classList.add("unread");
    if (message.id === state.selectedId) row.classList.add("selected");
    const sender = message.from || {};
    const stamp = categoryLabel(message);
    const cat = categoryFor(message);
    const flags = [];
    if (message.has_pdf) flags.push("PDF");
    if (message.list_unsubscribe || message.list_id || message.bulk) flags.push("List");
    if (needsReply(message)) flags.push("Needs reply");
    const stampBits = [];
    if (stamp) stampBits.push(escapeHtml(stamp));
    if (flags.length) stampBits.push(escapeHtml(flags.join(" · ")));
    const draftBody = state.replyDraftBodies[message.id] || "";
    const draftField = needsReply(message)
      ? `<div class="row-draft">
          <span>Draft response</span>
          <p>${draftBodyLoaded(message.id)
            ? escapeHtml(draftBody || "No draft generated yet.")
            : "Loading draft response…"}</p>
        </div>`
      : "";
    row.innerHTML = `
      <span class="dot"></span>
      <span>
        <p class="row-from">${escapeHtml(sender.name || sender.email || "Unknown")}</p>
        <p class="row-subject">${escapeHtml(message.subject)}</p>
        <p class="row-snippet">${escapeHtml(message.snippet || "")}</p>
        ${stampBits.length ? `<p class="row-cat${needsReply(message) ? " needs-reply" : ""}" data-cat="${escapeHtml(cat)}">${stampBits.join(" · ")}</p>` : ""}
        ${draftField}
      </span>
      <span class="row-date">${escapeHtml((message.date && message.date.label) || "")}</span>
    `;
    row.addEventListener("click", () => openMessage(message.id));
    els.list.appendChild(row);
  }
  loadMissingReplyDrafts(messages);
}

function idleReader(title, body) {
  els.reader.className = "reader reader-idle";
  els.reader.innerHTML = `
    <div class="idle-card">
      <p class="postmark">ADEUS · READ ONLY</p>
      <h2>${escapeHtml(title)}</h2>
      <p>${escapeHtml(body)}</p>
    </div>
  `;
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;");
}

async function loadStatus() {
  const data = await api("/api/status");
  state.connected = data.connected;
  els.account.textContent = data.connected
    ? data.profile.email
    : "Not connected";
  els.authBtn.textContent = data.connected ? "Disconnect" : "Connect Gmail";
  state.gmailCompose = Boolean(data.gmail_compose);
  state.gmailModify = Boolean(data.gmail_modify);
  updateComposeNote();
  applyAiSettings(data.grok || data.ai || {});
  return data;
}

function applyAiSettings(ai) {
  state.grokReady = Boolean(ai.grok_configured);
  state.deepseekReady = Boolean(ai.deepseek_configured);
  state.notionReady = Boolean(ai.notion_configured);
  state.llm = ai.preferred_llm || (state.grokReady ? "grok" : "deepseek");
  if (els.grokHint) {
    els.grokHint.textContent = ai.grok_configured
      ? `Saved Grok key ${ai.grok_key_hint}`
      : "No Grok key saved yet.";
  }
  if (els.deepseekHint) {
    els.deepseekHint.textContent = ai.deepseek_configured
      ? `Saved DeepSeek key ${ai.deepseek_key_hint}`
      : "No DeepSeek key saved yet.";
  }
  if (els.notionHint) {
    els.notionHint.textContent = ai.notion_configured
      ? `Notion connected ${ai.notion_key_hint}`
      : "No Notion integration saved yet.";
  }
  if (els.notionDatabase && ai.notion_database_id != null) {
    els.notionDatabase.value = ai.notion_database_id || "";
  }
  if (els.deskBtn) {
    const ready = [];
    if (state.grokReady) ready.push("Grok");
    if (state.deepseekReady) ready.push("DeepSeek");
    els.deskBtn.textContent = ready.length ? `Settings · ${ready.join(" + ")}` : "Settings";
  }
  if (ai.classify_limit != null) {
    state.classifyLimit = Math.max(1, Math.min(Number(ai.classify_limit) || 1000, 1000));
  }
  if (Array.isArray(ai.classify_categories) && ai.classify_categories.length) {
    state.classifyCategories = ai.classify_categories;
  } else if (!state.classifyCategories.length) {
    state.classifyCategories = DEFAULT_CATEGORIES.map((item) => ({ ...item }));
  }
  if (ai.classify_prompt != null) {
    state.classifyPrompt = ai.classify_prompt || state.classifyPrompt;
  }
  if (ai.task_extraction_prompt != null) {
    state.taskExtractionPrompt = ai.task_extraction_prompt || "";
  }
  if (!state.promptDirty) {
    fillSettingsForm();
  }
  updatePromptStatus();
  renderLabels();
}

function categoryOrder() {
  const fromSettings = (state.classifyCategories || []).map((item) => item.slug).filter(Boolean);
  return fromSettings.length ? fromSettings : CATEGORY_ORDER;
}

function letterCount() {
  return Math.max(1, Math.min(Number(state.classifyLimit) || 1000, 1000));
}

function slugify(value) {
  return String(value || "")
    .toLowerCase()
    .replace(/[^a-z0-9]+/g, "-")
    .replace(/^-+|-+$/g, "")
    .slice(0, 40) || "category";
}

function uniqueSlug(name, used, preferred) {
  let slug = slugify(preferred || name);
  const base = slug;
  let n = 2;
  while (used.has(slug)) {
    slug = `${base}-${n}`;
    n += 1;
  }
  used.add(slug);
  return slug;
}

function collectCategories() {
  if (!els.categoryList) return state.classifyCategories;
  const used = new Set();
  const rows = [...els.categoryList.querySelectorAll(".category-row")];
  const out = [];
  for (const row of rows) {
    const name = (row.querySelector(".cat-name")?.value || "").trim();
    const definition = (row.querySelector(".cat-def")?.value || "").trim();
    if (!name && !definition) continue;
    const locked = row.dataset.slug || "";
    const slug = uniqueSlug(name || definition, used, locked);
    out.push({
      slug,
      name: name || "Category",
      definition: definition || name || "Category",
    });
  }
  return out.length ? out : DEFAULT_CATEGORIES.map((item) => ({ ...item }));
}

function buildPromptFromCategories(categories) {
  const cats = categories && categories.length ? categories : DEFAULT_CATEGORIES;
  const slugs = cats.map((item) => item.slug).join(", ");
  const lines = [
    "Classify each email into exactly one category. Use the subject, sender, snippet, and the flags Adeus adds (unsubscribe, mailing_list, pdf, files).",
    "",
    `Return one of these slugs only: ${slugs}.`,
    "",
  ];
  for (const item of cats) {
    lines.push(`- ${item.slug}: ${item.definition}`);
  }
  lines.push("");
  lines.push("If two labels could apply, pick the more specific one.");
  return lines.join("\n");
}

function markSettingsDirty() {
  state.promptDirty = true;
  updatePromptPreview();
  updatePromptStatus();
}

function updatePromptPreview() {
  if (!els.promptPreview) return;
  els.promptPreview.textContent = buildPromptFromCategories(collectCategories());
}

function updatePromptStatus() {
  if (!els.promptStatus) return;
  els.promptStatus.textContent = state.promptDirty
    ? "Unsaved. Save these settings before running analysis."
    : "Saved. Analysis sends this combined prompt to DeepSeek.";
  els.promptStatus.classList.toggle("unsaved", state.promptDirty);
}

function categoryRow(cat) {
  const row = document.createElement("div");
  row.className = "category-row";
  if (cat.slug) row.dataset.slug = cat.slug;
  row.innerHTML = `
    <div class="category-row-head">
      <input class="cat-name" type="text" maxlength="60" placeholder="Name, e.g. Invoices" value="${escapeHtml(cat.name || "")}" />
      <button class="btn btn-ghost cat-remove" type="button">Remove</button>
    </div>
    <textarea class="cat-def" rows="3" maxlength="800" placeholder="How DeepSeek should file this…">${escapeHtml(cat.definition || "")}</textarea>
    <p class="desk-hint cat-slug">${cat.slug ? `slug · ${escapeHtml(cat.slug)}` : "slug · set on save"}</p>
  `;
  row.querySelector(".cat-name").addEventListener("input", () => {
    const hint = row.querySelector(".cat-slug");
    if (hint && !row.dataset.slug) {
      hint.textContent = `slug · ${slugify(row.querySelector(".cat-name").value)}`;
    }
    markSettingsDirty();
  });
  row.querySelector(".cat-def").addEventListener("input", markSettingsDirty);
  row.querySelector(".cat-remove").addEventListener("click", () => {
    if (els.categoryList.querySelectorAll(".category-row").length <= 1) {
      showToast("Keep at least one category.", "error");
      return;
    }
    row.remove();
    markSettingsDirty();
  });
  return row;
}

function fillSettingsForm() {
  if (els.classifyLimit) els.classifyLimit.value = String(letterCount());
  if (els.taskExtractionPrompt) {
    els.taskExtractionPrompt.value = state.taskExtractionPrompt;
  }
  if (els.categoryList) {
    els.categoryList.innerHTML = "";
    const cats = state.classifyCategories.length
      ? state.classifyCategories
      : DEFAULT_CATEGORIES;
    for (const cat of cats) {
      els.categoryList.appendChild(categoryRow(cat));
    }
  }
  state.promptDirty = false;
  updatePromptPreview();
  updatePromptStatus();
}

function setDeskTab(tab) {
  const selected = ["keys", "settings", "scheduler"].includes(tab) ? tab : "keys";
  document.querySelectorAll(".desk-tab").forEach((btn) => {
    const active = btn.dataset.tab === selected;
    btn.classList.toggle("active", active);
    btn.setAttribute("aria-selected", active ? "true" : "false");
  });
  document.querySelectorAll(".desk-pane").forEach((pane) => {
    pane.classList.toggle("hidden", pane.id !== `desk-pane-${selected}`);
  });
}

function openDesk(tab) {
  els.desk.classList.remove("hidden");
  els.desk.setAttribute("aria-hidden", "false");
  els.grokKey.value = "";
  els.deepseekKey.value = "";
  if (els.notionKey) els.notionKey.value = "";
  if (!state.promptDirty) fillSettingsForm();
  else updatePromptPreview();
  const pane = typeof tab === "string" ? tab : (state.deepseekReady ? "settings" : "keys");
  setDeskTab(pane);
  const card = els.desk.querySelector(".desk-card");
  if (card) card.scrollTop = 0;
  if (tab === "keys" || !state.deepseekReady) {
    (state.grokReady ? els.deepseekKey : els.grokKey).focus();
  }
}

function closeDesk() {
  els.desk.classList.add("hidden");
  els.desk.setAttribute("aria-hidden", "true");
}

function bindLlmDock(messageId) {
  const summarizeBtn = document.getElementById("llm-summarize");
  const askBtn = document.getElementById("llm-ask");
  const question = document.getElementById("llm-question");
  const reply = document.getElementById("llm-reply");
  const provider = document.getElementById("llm-provider");
  if (!summarizeBtn) return;
  if (provider) {
    provider.value = state.llm;
    provider.addEventListener("change", () => {
      state.llm = provider.value;
      api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ preferred_llm: state.llm }),
      })
        .then(() => showToast("AI provider preference saved."))
        .catch((err) => showToast(err.message, "error"));
    });
  }

  async function run(kind) {
    const chosen = provider ? provider.value : state.llm;
    const ready = chosen === "deepseek" ? state.deepseekReady : state.grokReady;
    if (!ready) {
      openDesk();
      showToast(`Save a ${chosen === "deepseek" ? "DeepSeek" : "Grok"} API key in the AI desk first.`, "error");
      return;
    }
    state.llm = chosen;
    reply.textContent = `${chosen === "deepseek" ? "DeepSeek" : "Grok"} is reading the letter…`;
    try {
      const data = kind === "summarize"
        ? await api(`/api/${chosen}/summarize/${encodeURIComponent(messageId)}`, { method: "POST" })
        : await api(`/api/${chosen}/ask`, {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
              message_id: messageId,
              question: question.value.trim() || "What does this letter want from me?",
            }),
          });
      reply.textContent = data.text;
    } catch (err) {
      reply.textContent = err.message;
      showToast(err.message, "error");
    }
  }

  summarizeBtn.addEventListener("click", () => run("summarize"));
  askBtn.addEventListener("click", () => run("ask"));
}

async function loadLabels() {
  renderLabels();
}

function rememberCategories(messages) {
  for (const message of messages || []) {
    const prev = state.categories[message.id] || {};
    if (message.category || message.needs_reply || prev.needs_reply) {
      state.categories[message.id] = {
        ...prev,
        category: message.category || prev.category,
        label: message.category_label || prev.label,
        needs_reply: Boolean(message.needs_reply || prev.needs_reply),
        needs_reply_reason: message.needs_reply_reason || prev.needs_reply_reason || "",
        reply_draft_id: message.reply_draft_id || prev.reply_draft_id || "",
      };
    }
  }
}

function applyReplyItems(items) {
  for (const [id, hit] of Object.entries(items || {})) {
    const prev = state.categories[id] || {};
    state.categories[id] = {
      ...prev,
      category: hit.category || prev.category,
      label: hit.label || prev.label,
      needs_reply: Boolean(hit.needs_reply),
      needs_reply_reason: hit.needs_reply_reason || "",
      reply_draft_id: hit.reply_draft_id || "",
    };
  }
  for (const message of state.messages) {
    const hit = state.categories[message.id];
    if (!hit) continue;
    message.needs_reply = hit.needs_reply;
    message.needs_reply_reason = hit.needs_reply_reason;
    message.reply_draft_id = hit.reply_draft_id;
    if (hit.category) {
      message.category = hit.category;
      message.category_label = hit.label;
    }
  }
}

function updateComposeNote() {
  if (!els.gmailComposeNote) return;
  const missing = state.connected && (!state.gmailCompose || !state.gmailModify);
  els.gmailComposeNote.classList.toggle("hidden", !missing);
  if (els.gmailReconnect) els.gmailReconnect.classList.toggle("hidden", !missing);
}

async function saveSortedTray() {
  if (!state.messages.length) return;
  await api("/api/classifications/tray", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      label: state.label,
      query: state.query,
      messages: state.messages,
    }),
  });
}

async function restoreSortedView(cached) {
  const items = cached.items || {};
  state.categories = items;
  const tray = cached.tray || [];
  if (tray.length && (!cached.label || cached.label === state.label)) {
    state.messages = tray;
    state.label = cached.label || state.label;
    state.query = cached.query || state.query;
    if (els.search) els.search.value = state.query;
    state.nextPage = null;
    state.sortedView = true;
    rememberCategories(tray);
    renderLabels();
    renderList();
    showToast(
      cached.stale
        ? `Restored ${tray.length} previously filed letters. Run analysis if you changed the prompt.`
        : `Restored ${tray.length} previously filed letters.`
    );
    return true;
  }
  if (Object.keys(items).length && state.connected) {
    const params = new URLSearchParams({
      label: state.label,
      q: state.query,
      limit: String(letterCount()),
    });
    const data = await api(`/api/messages/recent?${params}`);
    state.messages = data.messages || [];
    state.nextPage = null;
    state.sortedView = true;
    rememberCategories(state.messages);
    renderList();
    await saveSortedTray();
    showToast(`Restored stamps on ${state.messages.filter((item) => categoryFor(item)).length} of ${state.messages.length} letters.`);
    return true;
  }
  return false;
}

async function loadMessages({ reset = false, append = false } = {}) {
  if (!state.connected) {
    state.messages = [];
    state.nextPage = null;
    renderList();
    return;
  }
  state.loadingList = true;
  if (reset) {
    state.messages = [];
    state.nextPage = null;
    els.list.innerHTML = "<div class='skeleton'></div><div class='skeleton'></div><div class='skeleton'></div>";
  }
  const params = new URLSearchParams({
    label: state.label,
    q: state.query,
  });
  if (append && state.nextPage) params.set("page_token", state.nextPage);
  try {
    const data = await api(`/api/messages?${params}`);
    const incoming = data.messages || [];
    for (const message of incoming) {
      if (message.category) {
        state.categories[message.id] = {
          category: message.category,
          label: message.category_label,
        };
      }
    }
    const merged = append ? state.messages.concat(incoming) : incoming;
    state.messages = merged.slice(0, 1000);
    state.nextPage = state.messages.length >= 1000 ? null : (data.next_page_token || data.next_page_token || null);
    showBanner("");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    state.loadingList = false;
    renderList();
  }
}

function closeLetter() {
  state.selectedId = null;
  document.getElementById("app").classList.remove("is-reading");
  renderList();
  idleReader("Select a letter.", "Open a message from the tray to read it here.");
}

function bindReplyDraftEditor(messageId) {
  const editor = document.getElementById("reply-draft-body");
  const saveBtn = document.getElementById("reply-draft-save");
  const regenerateBtn = document.getElementById("reply-draft-regenerate");
  const status = document.getElementById("reply-draft-status");
  if (!editor || !saveBtn || !regenerateBtn || !status) return;

  async function loadDraft() {
    try {
      const data = await api(`/api/replies/draft/${encodeURIComponent(messageId)}`);
      editor.value = data.body || "";
      state.replyDraftBodies[messageId] = data.body || "";
      status.textContent = data.draft_id
        ? "Gmail draft loaded. Edit it here, then save."
        : "No draft yet. Generate one with the model.";
    } catch (err) {
      status.textContent = err.message;
      showToast(err.message, "error");
    }
  }

  saveBtn.addEventListener("click", async () => {
    const body = editor.value.trim();
    if (!body) {
      status.textContent = "Write a reply before saving.";
      showToast("Write a reply before saving.", "error");
      return;
    }
    saveBtn.disabled = true;
    status.textContent = "Saving Gmail draft…";
    try {
      const data = await api(`/api/replies/draft/${encodeURIComponent(messageId)}`, {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ body }),
      });
      applyReplyItems(data.items);
      state.replyDraftBodies[messageId] = data.body || body;
      status.textContent = "Draft saved to this Gmail thread.";
      showToast("Draft saved to this Gmail thread.");
      renderList();
    } catch (err) {
      status.textContent = err.message;
      showToast(err.message, "error");
    } finally {
      saveBtn.disabled = false;
    }
  });

  regenerateBtn.addEventListener("click", async () => {
    regenerateBtn.disabled = true;
    saveBtn.disabled = true;
    status.textContent = "The model is regenerating the reply…";
    try {
      const data = await api(
        `/api/replies/draft/${encodeURIComponent(messageId)}/regenerate`,
        { method: "POST" }
      );
      editor.value = data.body || "";
      applyReplyItems(data.items);
      state.replyDraftBodies[messageId] = data.body || "";
      status.textContent = "Reply regenerated and saved to the Gmail thread.";
      showToast("Reply regenerated and saved to the Gmail thread.");
      renderList();
    } catch (err) {
      status.textContent = err.message;
      showToast(err.message, "error");
    } finally {
      regenerateBtn.disabled = false;
      saveBtn.disabled = false;
    }
  });

  loadDraft();
}

async function openMessage(id) {
  state.selectedId = id;
  document.getElementById("app").classList.add("is-reading");
  renderList();
  els.reader.className = "reader";
  els.reader.innerHTML = "<div class='idle-card'><p class='postmark'>Opening letter…</p></div>";
  els.reader.scrollIntoView({ behavior: "smooth", block: "nearest" });
  try {
    const message = await api(`/api/messages/${encodeURIComponent(id)}`);
    const sender = message.from || {};
    const flags = [
      (message.unread || message.unread) ? "Unread" : "Read",
      (message.starred || message.starred) ? "Starred" : null,
      message.important ? "Important" : null,
      needsReply(message) ? "Needs reply" : null,
    ].filter(Boolean);
    const attachments = (message.attachments || [])
      .map((item) => `<span class="chip">${escapeHtml(item.filename)}</span>`)
      .join("");
    els.reader.innerHTML = `
      <article class="letter">
        <header class="letter-head">
          <div class="letter-toolbar">
            <button class="back-btn" id="close-letter" type="button">Back to tray</button>
            <div class="letter-meta">
              <span>${escapeHtml((message.date && message.date.label) || "")}</span>
            </div>
          </div>
          <h2 class="letter-subject">${escapeHtml(message.subject)}</h2>
          <p class="letter-from">${escapeHtml(sender.name || "Unknown")} <span>&lt;${escapeHtml(sender.email || "")}&gt;</span></p>
          <div class="flags">${flags.map((flag) => `<span class="flag${flag === "Needs reply" ? " needs-reply" : ""}">${flag}</span>`).join("")}</div>
          ${needsReply(message) && replyReason(message) ? `<p class="reply-reason">${escapeHtml(replyReason(message))}</p>` : ""}
        </header>
        <div class="letter-body" id="letter-body"></div>
        ${attachments ? `<div class="attachments">${attachments}</div>` : ""}
        ${needsReply(message) || replyDraftId(message) ? `
          <section class="reply-editor">
            <div class="reply-editor-head">
              <p class="postmark">DRAFT REPLY</p>
              <span id="reply-draft-status">Loading Gmail draft…</span>
            </div>
            <textarea id="reply-draft-body" rows="7" placeholder="Write or regenerate a reply…"></textarea>
            <div class="desk-actions">
              <button class="btn btn-brass" id="reply-draft-save" type="button">Save draft</button>
              <button class="btn btn-ghost" id="reply-draft-regenerate" type="button">Regenerate reply</button>
            </div>
          </section>
        ` : ""}
        <div class="grok-dock">
          <div class="llm-bar">
            <p class="postmark">ASK</p>
            <label class="sr-only" for="llm-provider">Model</label>
            <select id="llm-provider">
              <option value="grok">Grok</option>
              <option value="deepseek">DeepSeek</option>
            </select>
          </div>
          <textarea id="llm-question" placeholder="Ask about this letter…"></textarea>
          <div class="desk-actions">
            <button class="btn btn-brass" id="llm-summarize" type="button">Summarize</button>
            <button class="btn btn-ghost" id="llm-ask" type="button">Ask</button>
          </div>
          <p class="grok-reply" id="llm-reply"></p>
        </div>
      </article>
    `;
    document.getElementById("close-letter").addEventListener("click", closeLetter);
    bindLlmDock(id);
    bindReplyDraftEditor(id);
    const body = document.getElementById("letter-body");
    const thread = Array.isArray(message.thread) && message.thread.length ? message.thread : [message];
    thread.forEach((item) => {
      const section = document.createElement("section");
      section.className = "thread-item";
      if (thread.length > 1) {
        const meta = document.createElement("p");
        meta.className = "thread-meta";
        const who = item.from || {};
        meta.textContent = `${who.name || who.email || "Unknown"} · ${(item.date && item.date.label) || ""}`;
        section.appendChild(meta);
      }
      const pre = document.createElement("pre");
      pre.className = "letter-text";
      pre.textContent = ((item.body && item.body.text) || item.snippet || "").trim() || "(no plain text in this letter)";
      section.appendChild(pre);
      body.appendChild(section);
    });
  } catch (err) {
    els.reader.className = "reader reader-error";
    els.reader.innerHTML = `<div class="error-card"><p class="postmark">UNDELIVERED</p><h2>Could not open it.</h2><p>${escapeHtml(err.message)}</p></div>`;
  }
}

els.authBtn.addEventListener("click", async () => {
  if (state.connected) {
    try {
      await api("/api/auth/logout", { method: "POST" });
      state.connected = false;
      state.messages = [];
      state.selectedId = null;
      await loadStatus();
      await loadLabels();
      renderList();
      closeLetter();
      idleReader("Disconnected.", "Connect again when you want to read the evening mail.");
      showToast("Gmail disconnected.");
    } catch (err) {
      showToast(err.message, "error");
    }
    return;
  }
  els.authBtn.disabled = true;
  els.authBtn.textContent = "Waiting for Google…";
  showBanner("A browser window should open. Sign in and allow Gmail read and draft access.");
  try {
    await api("/api/auth/login", { method: "POST" });
    showBanner("");
    await loadStatus();
    await loadLabels();
    await loadMessages({ reset: true });
    showToast("Gmail connected successfully.");
  } catch (err) {
    // Another sign-in is mid-flight (e.g. a double click). Poll until the
    // flow the user already started completes instead of stranding them.
    if (/already in progress|still waiting/i.test(err.message)) {
      showBanner("A sign-in is already open in your browser. Waiting for it to finish…");
      const connected = await waitForConnection();
      showBanner("");
      if (connected) {
        await loadLabels();
        await loadMessages({ reset: true });
        showToast("Gmail connected successfully.");
        els.authBtn.disabled = false;
        return;
      }
    }
    showToast(err.message, "error");
    els.authBtn.textContent = "Connect Gmail";
  } finally {
    els.authBtn.disabled = false;
  }
});

async function waitForConnection(maxMs = 130000) {
  const started = Date.now();
  while (Date.now() - started < maxMs) {
    await new Promise((resolve) => setTimeout(resolve, 3000));
    try {
      const data = await loadStatus();
      if (data.connected) return true;
    } catch (_) {}
  }
  return false;
}

els.deskBtn.addEventListener("click", () => openDesk());
els.deskClose.addEventListener("click", closeDesk);
els.desk.addEventListener("click", (event) => {
  if (event.target === els.desk) closeDesk();
});
els.grokForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = els.grokKey.value.trim();
  if (!key) {
    showToast("Paste a Grok API key first.", "error");
    return;
  }
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grok_api_key: key, preferred_llm: "grok" }),
    });
    els.grokKey.value = "";
    applyAiSettings(data);
    showToast("Grok key saved on this machine.");
  } catch (err) {
    showToast(err.message, "error");
  }
});
els.grokClear.addEventListener("click", async () => {
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ grok_api_key: "" }),
    });
    els.grokKey.value = "";
    applyAiSettings(data);
    showToast("Grok key removed.");
  } catch (err) {
    showToast(err.message, "error");
  }
});

els.deepseekForm.addEventListener("submit", async (event) => {
  event.preventDefault();
  const key = els.deepseekKey.value.trim();
  if (!key) {
    showToast("Paste a DeepSeek API key first.", "error");
    return;
  }
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deepseek_api_key: key, preferred_llm: "deepseek" }),
    });
    els.deepseekKey.value = "";
    applyAiSettings(data);
    showToast("DeepSeek key saved on this machine.");
  } catch (err) {
    showToast(err.message, "error");
  }
});
els.deepseekClear.addEventListener("click", async () => {
  try {
    const data = await api("/api/settings", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ deepseek_api_key: "" }),
    });
    els.deepseekKey.value = "";
    applyAiSettings(data);
    showToast("DeepSeek key removed.");
  } catch (err) {
    showToast(err.message, "error");
  }
});

if (els.notionForm) {
  els.notionForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    const key = els.notionKey.value.trim();
    const database = els.notionDatabase.value.trim();
    if (!key && !state.notionReady) {
      showToast("Paste a Notion integration token first.", "error");
      return;
    }
    if (!database) {
      showToast("Paste a Notion to-do database URL or ID.", "error");
      return;
    }
    const payload = { notion_database_id: database };
    if (key) payload.notion_api_key = key;
    try {
      const data = await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      els.notionKey.value = "";
      applyAiSettings(data);
      showToast("Notion integration saved.");
    } catch (err) {
      showToast(err.message, "error");
    }
  });
}
if (els.notionClear) {
  els.notionClear.addEventListener("click", async () => {
    try {
      const data = await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ notion_api_key: "", notion_database_id: "" }),
      });
      els.notionKey.value = "";
      els.notionDatabase.value = "";
      applyAiSettings(data);
      showToast("Notion integration removed.");
    } catch (err) {
      showToast(err.message, "error");
    }
  });
}

async function saveClassifySettings() {
  const categories = collectCategories();
  const limit = Math.max(1, Math.min(Number(els.classifyLimit && els.classifyLimit.value) || letterCount(), 1000));
  const data = await api("/api/settings", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      classify_limit: limit,
      classify_categories: categories,
      task_extraction_prompt: (els.taskExtractionPrompt?.value || "").trim(),
    }),
  });
  state.promptDirty = false;
  applyAiSettings(data);
  return data.classify_prompt || buildPromptFromCategories(categories);
}

document.querySelectorAll(".desk-tab").forEach((btn) => {
  btn.addEventListener("click", () => setDeskTab(btn.dataset.tab));
});
if (els.addCategory) {
  els.addCategory.addEventListener("click", () => {
    if (!els.categoryList) return;
    els.categoryList.appendChild(categoryRow({ name: "", definition: "", slug: "" }));
    markSettingsDirty();
  });
}
if (els.classifyLimit) {
  els.classifyLimit.addEventListener("input", () => {
    const n = Math.max(1, Math.min(Number(els.classifyLimit.value) || 1, 1000));
    state.classifyLimit = n;
    state.promptDirty = true;
    updatePromptStatus();
  });
}
if (els.taskExtractionPrompt) {
  els.taskExtractionPrompt.addEventListener("input", markSettingsDirty);
}
if (els.classifyForm) {
  els.classifyForm.addEventListener("submit", async (event) => {
    event.preventDefault();
    try {
      await saveClassifySettings();
      showToast("Analysis settings and task extraction prompt saved.");
    } catch (err) {
      showToast(err.message, "error");
    }
  });
}
if (els.classifyReset) {
  els.classifyReset.addEventListener("click", async () => {
    try {
      state.promptDirty = false;
      const data = await api("/api/settings", {
        method: "PUT",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          classify_prompt: "",
          task_extraction_prompt: "",
          classify_limit: 1000,
          classify_categories: [],
        }),
      });
      applyAiSettings(data);
      showToast("Analysis settings restored to the defaults.");
    } catch (err) {
      showToast(err.message, "error");
    }
  });
}

async function reconnectGmail() {
  els.authBtn.disabled = true;
  showBanner("A browser window should open. Allow Gmail read and draft access.");
  try {
    await api("/api/auth/login?force=true", { method: "POST" });
    await loadStatus();
    showToast(
      state.gmailCompose
        ? "Gmail reconnected with draft access."
        : "Connected, but draft access is still missing. Allow compose when Google asks.",
      state.gmailCompose ? "success" : "error"
    );
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    els.authBtn.disabled = false;
    els.authBtn.textContent = state.connected ? "Disconnect" : "Connect Gmail";
  }
}

async function scanReplies() {
  if (state.scanningReplies) return;
  if (!state.connected) {
    showToast("Connect Gmail before scanning threads.", "error");
    return;
  }
  if (!state.grokReady && !state.deepseekReady) {
    openDesk("keys");
    showToast("Save a Grok or DeepSeek API key in the AI desk first.", "error");
    return;
  }
  state.scanningReplies = true;
  if (els.scanRepliesBtn) els.scanRepliesBtn.disabled = true;
  if (els.scanRepliesHeader) els.scanRepliesHeader.disabled = true;
  try {
    const listing = await api("/api/replies/threads");
    state.gmailCompose = Boolean(listing.can_draft);
    updateComposeNote();
    const threads = listing.threads || [];
    if (!threads.length) {
      showToast("No threads in the current tray. Sort mail first, then scan.", "error");
      return;
    }
    let scanned = 0;
    let flagged = 0;
    let drafts = 0;
    let notionTodos = 0;
    let skipped = 0;
    const errors = [];
    for (let i = 0; i < threads.length; i += 1) {
      showBanner(`Scanning thread ${i + 1} of ${threads.length}…`);
      const result = await api("/api/replies/scan", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ message_ids: [threads[i].id] }),
      });
      applyReplyItems(result.items);
      scanned += result.scanned || 0;
      flagged += result.flagged || 0;
      drafts += result.drafts_created || 0;
      notionTodos += result.notion_created || 0;
      skipped += result.skipped || 0;
      if (result.errors && result.errors.length) errors.push(...result.errors);
      renderList();
    }
    let summary = `Scanned ${scanned} threads. ${flagged} need a reply. ${drafts} Gmail drafts created.`;
    if (notionTodos) summary += ` ${notionTodos} Notion to-dos created.`;
    if (skipped) summary += ` ${skipped} already had drafts.`;
    if (!listing.can_draft) {
      summary += " Drafts were skipped — reconnect Gmail and allow compose access.";
    }
    if (errors.length) summary += ` ${errors[0]}`;
    showToast(summary, errors.length ? "error" : "success");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    state.scanningReplies = false;
    if (els.scanRepliesBtn) els.scanRepliesBtn.disabled = false;
    if (els.scanRepliesHeader) els.scanRepliesHeader.disabled = false;
  }
}

async function bulkAct(action) {
  if (state.bulkActionRunning) return;
  if (!state.connected) {
    showToast("Connect Gmail first.", "error");
    return;
  }
  const isTrash = action === "trash";
  const verb = isTrash ? "Delete" : "Archive";
  // A plain search with no category filter acts on every Gmail match
  // server-side; otherwise act on the messages in the current filtered view.
  const useQuery = state.categoryFilter === "all" && Boolean(state.query);
  let ids = [];
  let count = 0;
  let capped = false;
  let scope = "";
  try {
    if (useQuery) {
      const preview = await api("/api/messages/bulk/preview", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ query: state.query }),
      });
      count = preview.count;
      capped = preview.capped;
      scope = `matching “${state.query}”`;
    } else {
      ids = visibleMessages().map((item) => item.id);
      count = ids.length;
      scope = `in “${filterLabel()}”`;
    }
  } catch (err) {
    showToast(err.message, "error");
    return;
  }
  if (!count) {
    showToast("No emails match this selection.", "error");
    return;
  }
  const lines = [
    `${verb} ${count}${capped ? "+" : ""} email${count === 1 ? "" : "s"} ${scope}?`,
    isTrash
      ? "They will move to Gmail Trash and stay recoverable for about 30 days."
      : "They will leave the inbox but stay in All Mail.",
  ];
  if (capped) {
    lines.push("Large selection: this run handles the first 2,000. Run it again for the rest.");
  }
  if (!window.confirm(lines.join("\n\n"))) return;

  state.bulkActionRunning = true;
  if (els.bulkArchive) els.bulkArchive.disabled = true;
  if (els.bulkTrash) els.bulkTrash.disabled = true;
  showBanner(`${isTrash ? "Deleting" : "Archiving"} ${count}${capped ? "+" : ""} emails…`);
  try {
    const result = await api("/api/messages/bulk", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        action,
        ids: useQuery ? [] : ids,
        query: useQuery ? state.query : null,
      }),
    });
    const gone = new Set(result.ids || ids);
    state.messages = state.messages.filter((item) => !gone.has(item.id));
    if (state.selectedId && gone.has(state.selectedId)) closeLetter();
    showBanner("");
    renderList();
    let summary = `${isTrash ? "Deleted" : "Archived"} ${result.count} email${result.count === 1 ? "" : "s"}.`;
    if (result.failed) summary += ` ${result.failed} failed.`;
    if (result.capped) summary += " More remain — run it again for the rest.";
    showToast(summary, result.failed ? "error" : "success");
  } catch (err) {
    showBanner("");
    showToast(err.message, "error");
  } finally {
    state.bulkActionRunning = false;
    if (els.bulkArchive) els.bulkArchive.disabled = false;
    if (els.bulkTrash) els.bulkTrash.disabled = false;
  }
}

function renderAccountAnalysisSummary(summary) {
  if (!els.analysisSummary) return;
  const grouped = { ...(summary.categories || {}) };
  for (const item of summary.items || []) {
    const label = item.label || item.category || "Uncategorised";
    grouped[label] = Number(grouped[label] || 0) + 1;
  }
  const active = state.categoryFilter;
  const activeLabel = active === "all"
    ? "All categories"
    : (filingCategories().find((item) => item.slug === active)?.name || active);
  const categories = Object.entries(grouped).sort(([a], [b]) => {
    if (a === activeLabel) return -1;
    if (b === activeLabel) return 1;
    return a.localeCompare(b);
  });
  const categoryMarkup = categories.length
    ? categories.map(([label, count]) => `
        <div class="analysis-overview-row${label === activeLabel ? " active" : ""}">
          <span>${escapeHtml(label)}</span>
          <strong>${Number(count) || 0}</strong>
        </div>
      `).join("")
    : `<p class="analysis-empty">No threads analysed yet.</p>`;
  const needsReplies = summary.reply_items || (summary.items || []).filter((item) => item.needs_reply);
  const taskItems = summary.task_items || [];
  const taskMarkup = taskItems.length
    ? taskItems.map((item) => `
        <article class="analysis-task">
          <div class="analysis-task-head">
            <div>
              <p>${escapeHtml(item.subject || "(no subject)")}</p>
              <h4>${escapeHtml(item.task_title || "Email task")}</h4>
            </div>
            ${item.notion_url ? `<a href="${escapeHtml(item.notion_url)}" target="_blank" rel="noreferrer">Open in Notion ↗</a>` : ""}
          </div>
          ${item.reason ? `<p class="analysis-reason">${escapeHtml(item.reason)}</p>` : ""}
        </article>
      `).join("")
    : `<p class="analysis-empty">No Notion tasks are linked to this range.</p>`;
  const replyMarkup = needsReplies.length
    ? needsReplies.map((item) => {
        const details = [
          item.deadline ? `<span>Deadline · ${escapeHtml(item.deadline)}</span>` : "",
          item.importance ? `<span>Importance · ${escapeHtml(item.importance)}</span>` : "",
        ].filter(Boolean).join("");
        return `
          <article class="analysis-task">
            <div class="analysis-task-head">
              <div>
                <p>${escapeHtml(item.subject || "(no subject)")}</p>
                <h4>${escapeHtml(item.task_title || `Reply to: ${item.subject || "(no subject)"}`)}</h4>
              </div>
              <a href="https://mail.google.com/mail/u/0/#all/${encodeURIComponent(item.thread_id)}" target="_blank" rel="noreferrer">Open email ↗</a>
            </div>
            <p class="analysis-reason">${escapeHtml(item.reason || "This thread is waiting for your reply.")}</p>
            ${details ? `<div class="analysis-task-meta">${details}</div>` : ""}
            ${item.task_details ? `<p>${escapeHtml(item.task_details)}</p>` : ""}
            ${item.additional_details ? `<p>${escapeHtml(item.additional_details)}</p>` : ""}
            ${item.notion_url ? `<p><a href="${escapeHtml(item.notion_url)}" target="_blank" rel="noreferrer">Open task in Notion ↗</a></p>` : ""}
          </article>
        `;
      }).join("")
    : `<p class="analysis-empty">No analysed threads currently need your reply.</p>`;
  const errorMarkup = summary.errors.length
    ? `<div class="analysis-errors"><strong>${summary.errors.length} error${summary.errors.length === 1 ? "" : "s"}</strong>${summary.errors.slice(0, 10).map((error) => `<p>${escapeHtml(error)}</p>`).join("")}</div>`
    : "";
  const compactMetric = (total, added, label) => {
    const totalCount = Number(total || 0);
    const newCount = Number(added || 0);
    return `
      <span class="analysis-stat">
        <span class="analysis-stat-total"><strong>${totalCount}</strong><small>${escapeHtml(label)}</small></span>
        <em class="new-count${newCount > 0 ? " has-new" : ""}">+${newCount} new</em>
      </span>
    `;
  };
  els.analysisSummary.classList.remove("hidden");
  els.analysisSummary.innerHTML = `
    <div class="analysis-summary-head">
      <div>
        <p class="eyebrow">Analysis · ${escapeHtml(summary.range || summary.startDate || summary.start_date || "")}</p>
        <h3><span class="action-mark">!</span> Needs Action</h3>
        <p>${summary.needs_reply ?? needsReplies.length} thread${Number(summary.needs_reply ?? needsReplies.length) === 1 ? "" : "s"} need attention · ${summary.skipped_analyzed || 0} previous result${Number(summary.skipped_analyzed || 0) === 1 ? "" : "s"} reused.</p>
      </div>
      <div class="analysis-metrics">
        ${compactMetric(
          summary.total || summary.discovered,
          summary.threads_analyzed ?? summary.analysed,
          "threads"
        )}
        ${compactMetric(
          summary.work_threads ?? summary.work,
          summary.new_work_threads,
          "work"
        )}
        ${compactMetric(
          summary.tasks_total ?? summary.tasks_created ?? summary.tasksCreated,
          summary.tasks_created ?? summary.tasksCreated,
          "tasks"
        )}
        ${compactMetric(
          summary.needs_reply ?? summary.needsReply,
          summary.new_needs_reply,
          "need reply"
        )}
      </div>
    </div>
    <div class="analysis-section analysis-needs">
      <h4>Needs your reply</h4>
      ${replyMarkup}
    </div>
    <div class="analysis-section analysis-notion">
      <h4>Notion tasks · ${summary.tasks_total ?? taskItems.length}</h4>
      ${taskMarkup}
    </div>
    <div class="analysis-section analysis-overview">
      <h4>Overview by category · ${escapeHtml(activeLabel)}</h4>
      <div class="analysis-overview-grid">${categoryMarkup}</div>
    </div>
    ${errorMarkup}
  `;
}

async function analyseGmailAccount() {
  if (state.activeRunId) {
    await cancelActiveAnalysis();
    return;
  }
  if (state.analysingAccount) return;
  const startDate = (els.analysisStartDate && els.analysisStartDate.value) || "";
  if (!startDate) {
    showToast("Choose a start date first.", "error");
    return;
  }
  if (!state.connected) {
    showToast("Connect Gmail before analysing the account.", "error");
    return;
  }
  if (!state.deepseekReady) {
    openDesk("keys");
    showToast("Save a DeepSeek API key before running analysis.", "error");
    return;
  }
  if (!state.notionReady) {
    openDesk("keys");
    showToast("Connect a Notion task database before running analysis.", "error");
    return;
  }
  if (state.promptDirty) {
    openDesk("settings");
    showToast("Save the Task Extraction Prompt and category settings first.", "error");
    return;
  }
  try {
    const listing = await api(
      `/api/account-analysis/threads?start_date=${encodeURIComponent(startDate)}`
    );
    const count = Number(listing.count || 0);
    if (!count) {
      showToast(`No Gmail threads found from ${startDate}.`, "error");
      return;
    }
    const analysisCount = Number(listing.analysis_count || count);
    if (
      count > 100 &&
      !window.confirm(
        `This range contains ${count} threads to download; ${analysisCount} need analysis. Continue?`
      )
    ) {
      showToast(`Analysis cancelled before starting. ${count} threads were found.`, "error");
      return;
    }
    const run = await api("/api/analysis/runs", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        start_date: startDate,
        create_drafts: true,
        reanalyze: false,
      }),
    });
    startRunPolling(run);
  } catch (err) {
    showToast(err.message, "error");
  }
}

async function cancelActiveAnalysis() {
  if (!state.activeRunId) return;
  try {
    await api(
      `/api/analysis/runs/${encodeURIComponent(state.activeRunId)}/cancel`,
      { method: "POST" }
    );
    if (els.analyseAccountBtn) {
      els.analyseAccountBtn.disabled = true;
      els.analyseAccountBtn.textContent = "Cancelling…";
    }
    showBanner("Stopping full analysis after the current thread…");
  } catch (err) {
    showToast(err.message, "error");
  }
}

function formatRunTime(value) {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.getTime()) ? value : date.toLocaleString();
}

function runCounts(run) {
  const summary = (run && run.summary) || {};
  return {
    analysed: summary.threads_analyzed || 0,
    drafts: summary.drafts_created || 0,
    tasks: summary.tasks_created || 0,
  };
}

function renderSchedulerStatus(data, { preserveInputs = false } = {}) {
  if (!data) return;
  state.scheduler = data;
  const config = data.config || {};
  if (!preserveInputs) {
    if (els.schedulerEnabled) els.schedulerEnabled.checked = Boolean(config.enabled);
    if (els.schedulerTime) els.schedulerTime.value = config.run_time || "07:00";
    if (els.schedulerLookback) els.schedulerLookback.value = String(config.lookback_hours || 24);
  }
  if (els.schedulerNext) {
    els.schedulerNext.textContent = config.enabled
      ? `Next run: ${formatRunTime(data.next_run_at)} · ${data.timezone || "local time"}`
      : `Disabled · Times use ${data.timezone || "local time"}`;
  }
  const last = data.last_scheduled_run;
  if (els.schedulerLastRun) {
    if (!last) {
      els.schedulerLastRun.textContent = "No scheduled runs yet.";
    } else {
      const counts = runCounts(last);
      els.schedulerLastRun.innerHTML = `
        <p><strong>Last scheduled run</strong> · ${escapeHtml(formatRunTime(last.started_at || last.created_at))}</p>
        <div class="scheduler-run-counts">
          <span>${counts.analysed}<small>analysed</small></span>
          <span>${counts.drafts}<small>drafts</small></span>
          <span>${counts.tasks}<small>tasks</small></span>
        </div>
        <p>${escapeHtml(last.status || "unknown")}${last.error ? ` · ${escapeHtml(last.error)}` : ""}</p>
      `;
    }
  }
  if (els.schedulerRunLog) {
    const runs = data.runs || [];
    els.schedulerRunLog.innerHTML = runs.length
      ? runs.map((run) => {
          const counts = runCounts(run);
          return `
            <article class="scheduler-run-row">
              <div>
                <strong>${escapeHtml(run.trigger || "manual")} · ${escapeHtml(run.status || "unknown")}</strong>
                <span>${escapeHtml(formatRunTime(run.started_at || run.created_at))}</span>
              </div>
              <div class="scheduler-run-counts">
                <span>${counts.analysed}<small>analysed</small></span>
                <span>${counts.drafts}<small>drafts</small></span>
                <span>${counts.tasks}<small>tasks</small></span>
              </div>
              ${run.error ? `<p>${escapeHtml(run.error)}</p>` : ""}
            </article>
          `;
        }).join("")
      : "No runs yet.";
  }
}

async function loadSchedulerStatus(options = {}) {
  try {
    const data = await api("/api/scheduler");
    renderSchedulerStatus(data, options);
    if (data.active_run && !state.activeRunId) {
      startRunPolling(data.active_run);
    } else if (
      !state.activeRunId
      && Array.isArray(data.runs)
      && data.runs[0]
      && data.runs[0].summary
      && Object.keys(data.runs[0].summary).length
    ) {
      renderAccountAnalysisSummary(data.runs[0].summary);
    }
    return data;
  } catch (err) {
    if (els.schedulerNext) els.schedulerNext.textContent = err.message;
    return null;
  }
}

async function refreshClassificationsAfterRun({ loadTray = false } = {}) {
  try {
    const cached = await api("/api/classifications");
    if (loadTray && Array.isArray(cached.tray)) {
      state.messages = cached.tray;
      state.nextPage = null;
      state.sortedView = true;
      state.categoryFilter = "all";
      state.categories = cached.items || {};
      rememberCategories(state.messages);
    }
    applyReplyItems(cached.items || {});
    renderLabels();
    renderList();
  } catch (_) {}
}

async function pollAnalysisRun(runId) {
  while (state.activeRunId === runId) {
    let run;
    try {
      run = await api(`/api/analysis/runs/${encodeURIComponent(runId)}`);
    } catch (err) {
      state.activeRunId = "";
      state.analysingAccount = false;
      if (els.analyseAccountBtn) {
        els.analyseAccountBtn.disabled = false;
        els.analyseAccountBtn.textContent = "Run Full Analysis";
      }
      if (els.schedulerRunNow) els.schedulerRunNow.disabled = false;
      showToast(err.message, "error");
      return;
    }
    const summary = run.summary || {};
    if (Object.keys(summary).length) {
      renderAccountAnalysisSummary(summary);
      const phase = summary.phase || "analyzing";
      showBanner(
        phase === "fetching"
          ? `Fetching Gmail: ${summary.fetched || 0} of ${summary.total || 0} threads…`
          : `Analyzing new mail: ${summary.processed || 0} of ${summary.analysis_total ?? summary.total ?? 0} threads…`
      );
    }
    if (!["queued", "running", "cancelling"].includes(run.status)) {
      state.activeRunId = "";
      state.analysingAccount = false;
      if (els.analyseAccountBtn) {
        els.analyseAccountBtn.disabled = false;
        els.analyseAccountBtn.textContent = "Run Full Analysis";
      }
      if (els.schedulerRunNow) els.schedulerRunNow.disabled = false;
      await refreshClassificationsAfterRun({ loadTray: true });
      await loadSchedulerStatus();
      if (run.status === "completed") {
        const counts = runCounts(run);
        showToast(
          `Full analysis complete: ${counts.analysed} threads, ${counts.drafts} drafts, ${counts.tasks} tasks. Reloaded ${state.messages.length} emails.`
        );
      } else {
        showToast(run.error || `Analysis ${run.status}.`, "error");
      }
      return;
    }
    await new Promise((resolve) => setTimeout(resolve, 1200));
  }
}

function startRunPolling(run) {
  if (!run || !run.id || state.activeRunId) return;
  state.activeRunId = run.id;
  state.analysingAccount = true;
  if (els.analyseAccountBtn) {
    els.analyseAccountBtn.disabled = false;
    els.analyseAccountBtn.textContent = "Cancel Analysis";
  }
  if (els.schedulerRunNow) els.schedulerRunNow.disabled = true;
  els.analysisSummary.classList.remove("hidden");
  els.analysisSummary.textContent = "Starting full email analysis…";
  pollAnalysisRun(run.id);
}

async function saveSchedulerSettings() {
  if (!els.schedulerSave) return;
  els.schedulerSave.disabled = true;
  try {
    const data = await api("/api/scheduler", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: Boolean(els.schedulerEnabled && els.schedulerEnabled.checked),
        run_time: (els.schedulerTime && els.schedulerTime.value) || "07:00",
        lookback_hours: Number(els.schedulerLookback && els.schedulerLookback.value) || 24,
      }),
    });
    renderSchedulerStatus(data);
    showToast("Daily analysis schedule saved.");
  } catch (err) {
    showToast(err.message, "error");
  } finally {
    els.schedulerSave.disabled = false;
  }
}

async function runConfiguredLookback() {
  if (state.activeRunId) return;
  try {
    const saved = await api("/api/scheduler", {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({
        enabled: Boolean(els.schedulerEnabled && els.schedulerEnabled.checked),
        run_time: (els.schedulerTime && els.schedulerTime.value) || "07:00",
        lookback_hours: Number(els.schedulerLookback && els.schedulerLookback.value) || 24,
      }),
    });
    renderSchedulerStatus(saved);
    const run = await api("/api/scheduler/run-now", { method: "POST" });
    startRunPolling(run);
  } catch (err) {
    showToast(err.message, "error");
  }
}

if (els.bulkArchive) els.bulkArchive.addEventListener("click", () => bulkAct("archive"));
if (els.bulkTrash) els.bulkTrash.addEventListener("click", () => bulkAct("trash"));
if (els.scanRepliesBtn) els.scanRepliesBtn.addEventListener("click", scanReplies);
if (els.scanRepliesHeader) els.scanRepliesHeader.addEventListener("click", scanReplies);
if (els.gmailReconnect) els.gmailReconnect.addEventListener("click", reconnectGmail);
if (els.analyseAccountBtn) els.analyseAccountBtn.addEventListener("click", analyseGmailAccount);
if (els.schedulerSave) els.schedulerSave.addEventListener("click", saveSchedulerSettings);
if (els.schedulerRunNow) els.schedulerRunNow.addEventListener("click", runConfiguredLookback);

document.querySelectorAll("[data-category-shortcut]").forEach((button) => {
  button.addEventListener("click", () => {
    state.categoryFilter = button.dataset.categoryShortcut || "all";
    document.querySelectorAll(".topnav-link").forEach((item) => {
      item.classList.toggle("active", item === button);
    });
    renderList();
    document.querySelector(".workspace")?.scrollIntoView({ behavior: "smooth", block: "start" });
  });
});

document.querySelectorAll("[data-scroll-target]").forEach((button) => {
  button.addEventListener("click", () => {
    document.getElementById(button.dataset.scrollTarget || "")?.scrollIntoView({
      behavior: "smooth",
      block: "center",
    });
  });
});

els.searchForm.addEventListener("submit", (event) => {
  event.preventDefault();
  state.query = els.search.value.trim();
  state.sortedView = false;
  loadMessages({ reset: true });
});

if (els.refresh) {
  els.refresh.addEventListener("click", () => {
    if (state.sortedView) {
      restoreSortedView({ items: state.categories, tray: [], label: state.label, query: state.query });
      return;
    }
    loadMessages({ reset: true });
  });
}
els.loadMore.addEventListener("click", () => loadMessages({ append: true }));

document.addEventListener("keydown", (event) => {
  if (event.target === els.search) return;
  const visible = visibleMessages();
  const index = visible.findIndex((item) => item.id === state.selectedId);
  if (event.key === "j") {
    const next = visible[Math.min(index + 1, visible.length - 1)];
    if (next) openMessage(next.id);
  } else if (event.key === "k") {
    const prev = visible[Math.max(index - 1, 0)];
    if (prev) openMessage(prev.id);
  } else if (event.key === "/") {
    event.preventDefault();
    els.search.focus();
  } else if (event.key === "r") {
    loadMessages({ reset: true });
  }
});

async function boot() {
  if (!state.classifyCategories.length) {
    state.classifyCategories = DEFAULT_CATEGORIES.map((item) => ({ ...item }));
  }
  renderLabels();
  if (els.analysisStartDate && !els.analysisStartDate.value) {
    const start = new Date();
    const year = start.getFullYear();
    const month = String(start.getMonth() + 1).padStart(2, "0");
    const day = String(start.getDate()).padStart(2, "0");
    els.analysisStartDate.value = `${year}-${month}-${day}`;
    els.analysisStartDate.max = `${year}-${month}-${day}`;
  }
  const params = new URLSearchParams(window.location.search);
  if (params.get("auth_error")) {
    showToast("Google returned an OAuth error: " + params.get("auth_error"), "error");
  }
  try {
    try {
      applyAiSettings(await api("/api/settings"));
    } catch (_) {}
    await loadSchedulerStatus();
    await loadStatus();
    try {
      const cached = await api("/api/classifications");
      state.categories = cached.items || {};
    } catch (_) {}
    await loadLabels();
    if (state.connected) {
      let restored = false;
      try {
        const cached = await api("/api/classifications");
        restored = await restoreSortedView(cached);
      } catch (_) {}
      if (!restored) await loadMessages({ reset: true });
    } else {
      renderList();
      idleReader(
        "Connect Gmail.",
        "Adeus asks for Gmail read access, and draft access if you want reply drafts. Google will send you back here after you approve it."
      );
    }
  } catch (err) {
    showToast(err.message, "error");
  }
}

boot();
setInterval(() => loadSchedulerStatus({ preserveInputs: true }), 30000);

const form = document.querySelector("#preview-form");
const button = document.querySelector("#load-messages");
const status = document.querySelector("#result-status");
const results = document.querySelector("#message-results");
const emptyState = document.querySelector("#empty-state");
const guildSelector = document.querySelector("#guild-selector");
const guildSelectorContainer = document.querySelector("#guild-selector-container");
const connectionSuccess = document.querySelector("#connection-success");
const summary = document.querySelector("#summary");
const toolbar = document.querySelector("#message-toolbar");
const searchInput = document.querySelector("#message-search");
const channelFilter = document.querySelector("#channel-filter");
const viewMode = document.querySelector("#view-mode");
const sortOrder = document.querySelector("#sort-order");

let loadedMessages = [];
let loadSummary = null;
let guildReady = false;

function addText(parent, tagName, text, className = "") {
  const element = document.createElement(tagName);
  element.textContent = text;
  if (className) element.className = className;
  parent.appendChild(element);
  return element;
}

function setStatus(text, className = "") {
  status.className = `result-status ${className}`.trim();
  status.textContent = text;
}

function setLoading(isLoading) {
  button.disabled = isLoading || !guildReady;
  button.classList.toggle("loading", isLoading);
  button.lastElementChild.textContent = isLoading ? "Loading messages" : "Load messages";
}

function initializeOAuthSuccessState() {
  const url = new URL(window.location.href);
  if (url.searchParams.get("connected") !== "1") return;
  connectionSuccess.hidden = false;
  url.searchParams.delete("connected");
  history.replaceState({}, "", `${url.pathname}${url.search}${url.hash}`);
}

function renderSummary() {
  if (!loadSummary) {
    summary.hidden = true;
    return;
  }
  summary.hidden = false;
  document.querySelector("#summary-messages").textContent = String(loadedMessages.length);
  document.querySelector("#summary-readable").textContent = String(loadSummary.channels_successfully_read);
  document.querySelector("#summary-skipped").textContent = String(loadSummary.channels_skipped);
  document.querySelector("#summary-discovered").textContent = String(loadSummary.supported_channels_discovered);
}

function renderFilters() {
  const currentChannel = channelFilter.value;
  const channels = [...new Set(loadedMessages.map((message) => message.channel_name || "Unknown channel"))].sort();
  channelFilter.replaceChildren();
  const allChannels = document.createElement("option");
  allChannels.value = "";
  allChannels.textContent = "All channels";
  channelFilter.appendChild(allChannels);
  for (const channel of channels) {
    const option = document.createElement("option");
    option.value = channel;
    option.textContent = channel;
    channelFilter.appendChild(option);
  }
  channelFilter.value = channels.includes(currentChannel) ? currentChannel : "";
  toolbar.hidden = loadedMessages.length === 0;
}

function messageMatches(message, searchTerm, selectedChannel) {
  if (selectedChannel && (message.channel_name || "Unknown channel") !== selectedChannel) return false;
  if (!searchTerm) return true;
  const haystack = [
    message.author_name,
    message.channel_name,
    message.original_content,
    message.cleaned_content,
  ].filter(Boolean).join(" ").toLocaleLowerCase();
  return haystack.includes(searchTerm);
}

function initials(name) {
  const parts = (name || "Discord user").trim().split(/\s+/).filter(Boolean);
  return parts.slice(0, 2).map((part) => part[0]).join("").toUpperCase() || "D";
}

function formatTimestamp(timestamp) {
  const date = new Date(timestamp);
  return Number.isNaN(date.getTime()) ? "Unknown time" : date.toLocaleString();
}

function addPills(parent, values, className) {
  for (const value of values || []) addText(parent, "span", value, `pill ${className}`);
}

function renderContent(card, label, content, truncated) {
  const section = document.createElement("section");
  section.className = "message-content";
  const heading = document.createElement("div");
  heading.className = "content-head";
  addText(heading, "span", label, "content-label");
  section.appendChild(heading);
  addText(section, "div", content, "message-text");
  if (truncated) addText(section, "div", "Preview truncated to 2,000 characters.", "truncated");
  card.appendChild(section);
}

function renderMessageCard(message) {
  const card = document.createElement("article");
  card.className = "message-card";
  const head = document.createElement("header");
  head.className = "message-head";
  const author = document.createElement("div");
  author.className = "author-row";
  addText(author, "div", initials(message.author_name || message.author_id), "avatar");
  const authorCopy = document.createElement("div");
  addText(authorCopy, "div", message.author_name || "Discord user", "author-name");
  addText(authorCopy, "div", formatTimestamp(message.timestamp), "timestamp");
  author.appendChild(authorCopy);
  head.appendChild(author);
  const pills = document.createElement("div");
  pills.className = "meta-pills";
  addText(pills, "span", `#${message.channel_name || "channel"}`, "pill channel-pill");
  if (message.cleaned_content !== message.original_content) addText(pills, "span", "Changed", "pill changed-pill");
  if (message.attachment_count) addText(pills, "span", `${message.attachment_count} attachment${message.attachment_count === 1 ? "" : "s"}`, "pill");
  if (message.reply_to_message_id) addText(pills, "Reply", "pill");
  addPills(pills, message.sticker_names, "sticker-pill");
  addPills(pills, message.mention_names, "mention-pill");
  head.appendChild(pills);
  card.appendChild(head);

  if (viewMode.value === "cleaned" || viewMode.value === "both") renderContent(card, "Cleaned message", message.cleaned_content, message.cleaned_truncated);
  if (viewMode.value === "original" || viewMode.value === "both") renderContent(card, "Original message", message.original_content, message.original_truncated);

  const details = document.createElement("details");
  addText(details, "summary", "Technical details");
  const detailGrid = document.createElement("div");
  detailGrid.className = "details-grid";
  addText(detailGrid, "span", `Message ID: ${message.message_id}`);
  addText(detailGrid, "span", `Author ID: ${message.author_id}`);
  addText(detailGrid, "span", `Channel ID: ${message.channel_id}`);
  if (message.reply_to_message_id) addText(detailGrid, "span", `Reply ID: ${message.reply_to_message_id}`);
  details.appendChild(detailGrid);
  card.appendChild(details);
  return card;
}

function applyFiltersAndSort() {
  const searchTerm = searchInput.value.trim().toLocaleLowerCase();
  const matchingMessages = loadedMessages
    .filter((message) => messageMatches(message, searchTerm, channelFilter.value))
    .sort((left, right) => {
      const difference = new Date(left.timestamp).getTime() - new Date(right.timestamp).getTime();
      return sortOrder.value === "oldest" ? difference : -difference;
    });
  results.replaceChildren();
  emptyState.hidden = matchingMessages.length > 0;
  emptyState.textContent = loadedMessages.length && !matchingMessages.length
    ? "No messages match the current filters."
    : "No messages were returned from readable channels.";
  matchingMessages.forEach((message) => results.appendChild(renderMessageCard(message)));
}

async function loadConnectedGuilds() {
  setLoading(true);
  setStatus("Refreshing connected Discord servers…");
  try {
    const response = await fetch("/integrations/discord/ui/guilds");
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Connected Discord servers could not be loaded.");
    guildSelector.replaceChildren();
    for (const guild of data.guilds) {
      const option = document.createElement("option");
      option.value = guild.guild_id;
      option.textContent = guild.guild_name;
      guildSelector.appendChild(option);
    }
    if (!data.guilds.length) {
      guildReady = false;
      guildSelectorContainer.hidden = true;
      setStatus("Connect Discord to a server before loading message previews.");
      return;
    }
    if (data.guilds.length === 1) {
      guildReady = true;
      guildSelectorContainer.hidden = true;
      setStatus(`Connected server: ${data.guilds[0].guild_name}`, "success");
    } else {
      guildReady = true;
      guildSelectorContainer.hidden = false;
      setStatus("Choose a connected Discord server.", "success");
    }
  } catch (error) {
    guildReady = false;
    setStatus(error instanceof Error ? error.message : "Connected Discord servers could not be loaded.", "error");
  } finally {
    setLoading(false);
  }
}

async function loadMessages(event) {
  event.preventDefault();
  setLoading(true);
  results.replaceChildren();
  emptyState.hidden = false;
  emptyState.textContent = "Loading read-only message preview…";
  setStatus("Loading readable channels…");
  try {
    const response = await fetch("/integrations/discord/ui/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ guild_id: guildSelector.value, max_messages: Number(document.querySelector("#max-messages").value) }),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Discord preview could not be loaded.");
    loadedMessages = data.messages;
    loadSummary = data;
    renderSummary();
    renderFilters();
    applyFiltersAndSort();
    setStatus(`Loaded ${data.messages.length} message${data.messages.length === 1 ? "" : "s"} from ${data.channels_successfully_read} readable channel${data.channels_successfully_read === 1 ? "" : "s"}; ${data.channels_skipped} skipped.`, "success");
  } catch (error) {
    loadedMessages = [];
    loadSummary = null;
    renderSummary();
    renderFilters();
    emptyState.hidden = false;
    emptyState.textContent = "Message preview could not be loaded.";
    setStatus(error instanceof Error ? error.message : "Discord preview could not be loaded.", "error");
  } finally {
    setLoading(false);
  }
}

form.addEventListener("submit", loadMessages);
searchInput.addEventListener("input", applyFiltersAndSort);
channelFilter.addEventListener("change", applyFiltersAndSort);
viewMode.addEventListener("change", applyFiltersAndSort);
sortOrder.addEventListener("change", applyFiltersAndSort);
initializeOAuthSuccessState();
loadConnectedGuilds();

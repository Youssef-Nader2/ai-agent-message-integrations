const form = document.querySelector("#preview-form");
const button = document.querySelector("#load-messages");
const status = document.querySelector("#result-status");
const results = document.querySelector("#message-results");
const guildSelector = document.querySelector("#guild-selector");
const guildSelectorContainer = document.querySelector("#guild-selector-container");

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

function displayMessage(message) {
  const card = document.createElement("article");
  card.className = "message-card";
  const meta = document.createElement("div");
  meta.className = "message-meta";
  addText(meta, "span", `Channel: ${message.channel_name || message.channel_id}`);
  addText(meta, "span", `Author: ${message.author_name || message.author_id}`);
  addText(meta, "span", `Message ID: ${message.message_id}`);
  addText(meta, "span", `Timestamp: ${new Date(message.timestamp).toLocaleString()}`);
  addText(meta, "span", `Attachments: ${message.attachment_count}`);
  if (message.reply_to_message_id) addText(meta, "span", `Reply to: ${message.reply_to_message_id}`);
  card.appendChild(meta);

  const columns = document.createElement("div");
  columns.className = "message-columns";
  for (const [title, content, truncated] of [
    ["Original message", message.original_content, message.original_truncated],
    ["Cleaned message", message.cleaned_content, message.cleaned_truncated],
  ]) {
    const column = document.createElement("section");
    column.className = "message-content";
    addText(column, "h3", title);
    addText(column, "div", content);
    if (truncated) addText(column, "div", "Preview truncated to 2,000 characters.", "truncated");
    columns.appendChild(column);
  }
  card.appendChild(columns);
  results.appendChild(card);
}

async function loadConnectedGuilds() {
  button.disabled = true;
  setStatus("Loading connected Discord servers…");
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
    if (data.guilds.length === 0) {
      guildSelectorContainer.hidden = true;
      setStatus("Connect Discord to a server before loading message previews.");
      return;
    }
    if (data.guilds.length === 1) {
      guildSelectorContainer.hidden = true;
      setStatus(`Connected server: ${data.guilds[0].guild_name}`);
    } else {
      guildSelectorContainer.hidden = false;
      setStatus("Choose a connected Discord server.");
    }
    button.disabled = false;
  } catch (error) {
    setStatus(
      error instanceof Error ? error.message : "Connected Discord servers could not be loaded.",
      "error",
    );
  }
}

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(form);
  const payload = {
    guild_id: guildSelector.value,
    max_messages: Number(formData.get("max_messages")),
  };
  button.disabled = true;
  button.textContent = "Loading…";
  results.replaceChildren();
  setStatus("Loading read-only message preview…");
  try {
    const response = await fetch("/integrations/discord/ui/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Discord preview could not be loaded.");
    setStatus(
      `Loaded ${data.messages.length} message${data.messages.length === 1 ? "" : "s"} from ` +
        `${data.channels_successfully_read} readable channel${data.channels_successfully_read === 1 ? "" : "s"}; ` +
        `${data.channels_skipped} skipped.`,
      "success",
    );
    data.messages.forEach(displayMessage);
  } catch (error) {
    setStatus(
      error instanceof Error ? error.message : "Discord preview could not be loaded.",
      "error",
    );
  } finally {
    button.disabled = false;
    button.textContent = "Load Messages";
  }
});

loadConnectedGuilds();

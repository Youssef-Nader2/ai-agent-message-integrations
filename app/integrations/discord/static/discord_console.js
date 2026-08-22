const form = document.querySelector("#preview-form");
const button = document.querySelector("#load-messages");
const status = document.querySelector("#result-status");
const results = document.querySelector("#message-results");

function addText(parent, tagName, text, className = "") {
  const element = document.createElement(tagName);
  element.textContent = text;
  if (className) element.className = className;
  parent.appendChild(element);
  return element;
}

function displayMessage(message) {
  const card = document.createElement("article");
  card.className = "message-card";
  const meta = document.createElement("div");
  meta.className = "message-meta";
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

form.addEventListener("submit", async (event) => {
  event.preventDefault();
  const formData = new FormData(form);
  const payload = {
    guild_id: formData.get("guild_id"),
    channel_id: formData.get("channel_id"),
    max_messages: Number(formData.get("max_messages")),
  };
  button.disabled = true;
  button.textContent = "Loading…";
  results.replaceChildren();
  status.className = "result-status";
  status.textContent = "Loading read-only message preview…";
  try {
    const response = await fetch("/integrations/discord/ui/load", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });
    const data = await response.json();
    if (!response.ok) throw new Error(data.detail || "Discord preview could not be loaded.");
    status.className = "result-status success";
    status.textContent = `Loaded ${data.messages.length} message${data.messages.length === 1 ? "" : "s"}.`;
    data.messages.forEach(displayMessage);
  } catch (error) {
    status.className = "result-status error";
    status.textContent = error instanceof Error ? error.message : "Discord preview could not be loaded.";
  } finally {
    button.disabled = false;
    button.textContent = "Load Messages";
  }
});

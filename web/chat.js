/**
 * chat.js — shared Pi chat client logic (T-189/T-190).
 * Used by web/index.html and extension/sidepanel.html.
 *
 * Expects in global scope: BRAIN_URL (string, e.g. "http://127.0.0.1:7712")
 * Token is stored in localStorage["pi_token"] and settable via setToken().
 */

function getToken() { return localStorage.getItem("pi_token") || ""; }
function setToken(t) { localStorage.setItem("pi_token", t); }

function authHeaders() {
  const t = getToken();
  return t ? { "Authorization": "Bearer " + t, "Content-Type": "application/json" } : { "Content-Type": "application/json" };
}

async function fetchConversations() {
  const r = await fetch(BRAIN_URL + "/conversations", { headers: authHeaders() });
  if (!r.ok) throw new Error("HTTP " + r.status);
  const d = await r.json();
  return d.conversations || [];
}

async function sendChat(text, conversationId) {
  const body = { text };
  if (conversationId) body.conversation_id = conversationId;
  const r = await fetch(BRAIN_URL + "/chat", {
    method: "POST",
    headers: authHeaders(),
    body: JSON.stringify(body),
  });
  if (!r.ok) throw new Error("HTTP " + r.status);
  return await r.json();
}

function streamChat(text, conversationId, onChunk, onDone, onError) {
  const params = new URLSearchParams({ text });
  if (conversationId) params.set("conversation_id", conversationId);
  const token = getToken();
  if (token) params.set("token", token);
  const es = new EventSource(BRAIN_URL + "/chat/stream?" + params.toString());
  es.onmessage = (e) => {
    if (e.data === "[DONE]") { es.close(); onDone && onDone(); }
    else { onChunk && onChunk(e.data + " "); }
  };
  es.onerror = (e) => { es.close(); onError && onError(e); };
  return es;
}

function buildPageContextPrefix(selection, url, title) {
  let prefix = "";
  if (title) prefix += "[Page: " + title + "]";
  if (url)   prefix += (prefix ? " " : "") + "[URL: " + url + "]";
  if (selection) prefix += "\n\n[Selected text:]\n" + selection;
  return prefix ? prefix + "\n\n" : "";
}

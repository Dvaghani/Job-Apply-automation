// Everything that talks to the jobpipe server happens here.
//
// Not in the content script, for a reason worth remembering: under MV3 a
// content script's fetch is subject to the *page's* CORS policy, so a fetch
// to localhost from inside some employer's careers page would be blocked.
// The service worker fetches with the extension's own origin and its
// host_permissions, which is the only reliable way to reach the local server.

const DEFAULTS = { server: "http://127.0.0.1:5000", token: "" };

async function settings() {
  const stored = await chrome.storage.sync.get(DEFAULTS);
  return { ...DEFAULTS, ...stored };
}

async function call(path, options = {}) {
  const { server, token } = await settings();
  if (!token) {
    throw new Error("No token set. Open the extension's options and paste the one the dashboard shows.");
  }

  let response;
  try {
    response = await fetch(server.replace(/\/+$/, "") + path, {
      ...options,
      headers: { Authorization: "Bearer " + token, ...(options.headers || {}) },
    });
  } catch (e) {
    throw new Error("Cannot reach " + server + " — is `jobpipe dashboard` running?");
  }

  if (response.status === 401) {
    throw new Error("The server rejected the token. Re-copy it from the dashboard.");
  }
  if (!response.ok) {
    let detail = "HTTP " + response.status;
    try {
      const body = await response.json();
      if (body && body.error) detail = body.error;
    } catch (e) { /* not JSON; the status will do */ }
    throw new Error(detail);
  }
  return response;
}

function base64(buffer) {
  // Chunked: String.fromCharCode over a whole resume at once overflows the
  // argument limit on anything but a tiny file.
  const bytes = new Uint8Array(buffer);
  let binary = "";
  for (let i = 0; i < bytes.length; i += 0x8000) {
    binary += String.fromCharCode.apply(null, bytes.subarray(i, i + 0x8000));
  }
  return btoa(binary);
}

async function handle(message) {
  if (message.type === "options") {
    // The panel's own escape hatch: a content script cannot open this page.
    await chrome.runtime.openOptionsPage();
    return { ok: true };
  }
  if (message.type === "ping") {
    return await (await call("/ext/ping")).json();
  }
  if (message.type === "jobs") {
    return await (await call("/ext/jobs")).json();
  }
  if (message.type === "plan") {
    const response = await call("/ext/plan", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(message.body),
    });
    return await response.json();
  }
  if (message.type === "file") {
    const response = await call(message.url);
    return {
      base64: base64(await response.arrayBuffer()),
      contentType: response.headers.get("Content-Type") || "application/octet-stream",
    };
  }
  throw new Error("unknown request: " + message.type);
}

chrome.runtime.onMessage.addListener((message, _sender, respond) => {
  handle(message).then(respond).catch((e) => respond({ error: e.message }));
  return true; // keep the channel open for the async reply
});

// activeTab: the panel is injected only into the tab whose toolbar button you
// click. The extension has no standing access to pages you merely visit.
chrome.action.onClicked.addListener(async (tab) => {
  if (!tab.id || !/^https?:/.test(tab.url || "")) return;
  await chrome.scripting.insertCSS({ target: { tabId: tab.id }, files: ["panel.css"] });
  await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    files: ["extract.js", "content.js"],
  });
});

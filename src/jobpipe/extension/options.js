const DEFAULTS = { server: "http://127.0.0.1:5000", token: "" };

const serverInput = document.getElementById("server");
const tokenInput = document.getElementById("token");
const say = document.getElementById("say");

function report(text, ok) {
  say.textContent = text;
  say.className = ok ? "ok" : "bad";
}

chrome.storage.sync.get(DEFAULTS).then((stored) => {
  serverInput.value = stored.server || DEFAULTS.server;
  tokenInput.value = stored.token || "";
});

document.getElementById("save").addEventListener("click", async () => {
  await chrome.storage.sync.set({
    server: serverInput.value.trim() || DEFAULTS.server,
    token: tokenInput.value.trim(),
  });
  report("Saved.", true);
});

document.getElementById("test").addEventListener("click", async () => {
  // Save first: testing what is on screen rather than what is stored is the
  // difference between a useful test and a confusing one.
  await chrome.storage.sync.set({
    server: serverInput.value.trim() || DEFAULTS.server,
    token: tokenInput.value.trim(),
  });
  report("Checking…", true);
  const result = await chrome.runtime.sendMessage({ type: "ping" });
  if (!result || result.error) {
    report(result ? result.error : "No reply from the extension.", false);
    return;
  }
  report(`Connected. ${result.approved} approved job(s).`, true);
});

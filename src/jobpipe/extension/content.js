// The floating panel, and the only code in this project that writes to a
// page it does not control.
//
// Two invariants, enforced here as well as on the server. They are cheap to
// check and expensive to get wrong, and a plan arriving over HTTP is exactly
// the kind of thing that should not be trusted just because we asked for it.
//
//   1. Nothing is ever written to a password field.
//   2. No button is ever clicked. The only .click() below is on a checkbox
//      or radio, guarded by its own type check — never a submit control.

(() => {
  "use strict";

  const PANEL_ID = "jobpipe-panel";

  // Already open: rebuild rather than stacking a second one. Rebuilding
  // rather than just revealing it matters — clicking the button again after
  // fixing the token or approving a job should show the new state, not the
  // stale panel that told you something was wrong.
  const existing = document.getElementById(PANEL_ID);
  if (existing) existing.remove();

  // --- the two invariants ------------------------------------------------

  const NEVER_WRITE = new Set([
    "password", "submit", "button", "reset", "image", "hidden",
  ]);

  function writable(el) {
    if (!el) return false;
    const tag = el.tagName;
    if (tag !== "INPUT" && tag !== "SELECT" && tag !== "TEXTAREA") return false;
    if (tag === "INPUT" && NEVER_WRITE.has((el.type || "").toLowerCase())) return false;
    return !el.disabled;
  }

  // --- writing to fields -------------------------------------------------

  // React and friends track their own value and ignore a plain assignment;
  // going through the prototype's native setter is what makes the framework
  // notice the change.
  function setValue(el, value) {
    const proto = el instanceof HTMLTextAreaElement
      ? HTMLTextAreaElement.prototype
      : HTMLInputElement.prototype;
    const setter = Object.getOwnPropertyDescriptor(proto, "value").set;
    setter.call(el, value);
    el.dispatchEvent(new Event("input", { bubbles: true }));
    el.dispatchEvent(new Event("change", { bubbles: true }));
  }

  function toFile(base64, contentType, filename) {
    const binary = atob(base64);
    const bytes = new Uint8Array(binary.length);
    for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i);
    return new File([bytes], filename, { type: contentType });
  }

  async function applyOne(action) {
    const el = document.querySelector('[data-jobpipe-id="' + action.id + '"]');
    if (!writable(el)) return { ...action, action: "skipped", detail: "not writable" };

    if (action.how === "file") {
      const got = await send({ type: "file", url: action.url });
      if (got.error) return { ...action, action: "failed", detail: got.error };
      const transfer = new DataTransfer();
      transfer.items.add(toFile(got.base64, got.contentType, action.filename));
      el.files = transfer.files;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return action;
    }

    if (action.how === "select") {
      el.value = action.value;
      el.dispatchEvent(new Event("change", { bubbles: true }));
      return action;
    }

    if (action.how === "check" || action.how === "radio") {
      const type = (el.type || "").toLowerCase();
      if (type !== "checkbox" && type !== "radio") {
        return { ...action, action: "skipped", detail: "not a tick box" };
      }
      // Safe by the type check above: a checkbox does not submit a form.
      if (!el.checked) el.click();
      return action;
    }

    if (action.how === "text") {
      setValue(el, action.value);
      return action;
    }

    return action;
  }

  // --- talking to the server ---------------------------------------------

  function send(message) {
    return new Promise((resolve) => chrome.runtime.sendMessage(message, resolve));
  }

  // --- the panel ---------------------------------------------------------

  const panel = document.createElement("div");
  panel.id = PANEL_ID;
  panel.innerHTML = `
    <div class="jp-bar">
      <span class="jp-name">jobpipe</span>
      <span class="jp-grow"></span>
      <button class="jp-x" title="Close">&times;</button>
    </div>
    <div class="jp-body">
      <select class="jp-job"><option value="">Loading approved jobs…</option></select>
      <button class="jp-fill">Fill this page</button>
      <div class="jp-status"></div>
      <div class="jp-log"></div>
    </div>
    <div class="jp-foot">
      <span>Never submits. Never types a password.</span>
      <button class="jp-opts">Options</button>
    </div>
  `;
  document.body.appendChild(panel);

  const jobSelect = panel.querySelector(".jp-job");
  const fillButton = panel.querySelector(".jp-fill");
  const status = panel.querySelector(".jp-status");
  const logBox = panel.querySelector(".jp-log");

  panel.querySelector(".jp-x").addEventListener("click", () => panel.remove());

  // A content script can't open the options page itself; the worker can.
  panel.querySelector(".jp-opts")
    .addEventListener("click", () => send({ type: "options" }));

  // Draggable by its title bar, so it can be moved off whatever it covers.
  (() => {
    const bar = panel.querySelector(".jp-bar");
    let startX = 0, startY = 0, originX = 0, originY = 0, dragging = false;
    bar.addEventListener("mousedown", (e) => {
      if (e.target.classList.contains("jp-x")) return;
      dragging = true;
      startX = e.clientX; startY = e.clientY;
      const box = panel.getBoundingClientRect();
      originX = box.left; originY = box.top;
      e.preventDefault();
    });
    window.addEventListener("mousemove", (e) => {
      if (!dragging) return;
      panel.style.left = originX + (e.clientX - startX) + "px";
      panel.style.top = originY + (e.clientY - startY) + "px";
      panel.style.right = "auto";
      panel.style.bottom = "auto";
    });
    window.addEventListener("mouseup", () => { dragging = false; });
  })();

  function say(text, kind) {
    status.textContent = text || "";
    status.className = "jp-status" + (kind ? " jp-" + kind : "");
  }

  function render(actions, note) {
    const mark = { filled: "+", skipped: "–", unmatched: "?", failed: "!" };
    logBox.innerHTML = "";
    if (note) {
      const row = document.createElement("div");
      row.className = "jp-row jp-warn";
      row.textContent = "! " + note;
      logBox.appendChild(row);
    }
    actions.forEach((a) => {
      const row = document.createElement("div");
      row.className = "jp-row jp-" + a.action;
      row.textContent = (mark[a.action] || "?") + " " + a.label +
        (a.detail ? "  " + a.detail : "");
      logBox.appendChild(row);
    });
  }

  async function loadJobs() {
    const got = await send({ type: "jobs" });
    if (got.error) {
      jobSelect.innerHTML = '<option value="">—</option>';
      say(got.error, "warn");
      return;
    }
    const here = location.href;
    jobSelect.innerHTML = "";
    const none = document.createElement("option");
    none.value = "";
    none.textContent = "No job — fill text only, attach nothing";
    jobSelect.appendChild(none);

    got.jobs.forEach((job) => {
      const option = document.createElement("option");
      option.value = job.fingerprint;
      const score = job.score === null ? "––" : job.score;
      option.textContent = `${score} · ${job.title} — ${job.company}` +
        (job.tailored ? "" : "  (not tailored)");
      // If this page is the posting itself, preselect it.
      if (job.url && here.startsWith(job.url.split("?")[0])) option.selected = true;
      jobSelect.appendChild(option);
    });
    say(got.jobs.length + " approved job(s)");
  }

  async function fill() {
    fillButton.disabled = true;
    say("Reading the page…");
    logBox.innerHTML = "";
    try {
      const fields = window.jobpipeExtractFields();
      if (!fields.length) {
        say("No form fields on this page.", "warn");
        return;
      }
      say(`Asking jobpipe about ${fields.length} field(s)…`);
      const plan = await send({
        type: "plan",
        body: { job: jobSelect.value || null, url: location.href, fields },
      });
      if (plan.error) { say(plan.error, "warn"); return; }

      const applied = [];
      for (const action of plan.actions) {
        applied.push(action.action === "filled" ? await applyOne(action) : action);
      }
      const filled = applied.filter((a) => a.action === "filled").length;
      say(`${filled} of ${applied.length} fields filled. Check them, then submit yourself.`,
          "ok");
      render(applied, plan.note);
    } catch (e) {
      say(e.message, "warn");
    } finally {
      fillButton.disabled = false;
    }
  }

  fillButton.addEventListener("click", fill);
  loadJobs();
})();

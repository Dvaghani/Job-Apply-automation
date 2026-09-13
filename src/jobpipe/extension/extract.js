// Read every fillable field on the page, with enough context to classify it.
//
// One copy, two callers: Playwright evaluates this file during `jobpipe
// apply`, and the Chrome extension loads it as a content script. Field
// matching is where an autofiller is right or wrong, so the two paths must
// see identical input — a second copy of this would drift within a week.
//
// It only ever reads. Nothing here sets a value or clicks anything.

function jobpipeExtractFields() {
  // Buttons are not in this set by accident: excluding them here is what
  // makes "cannot submit" structural rather than a promise.
  const SKIP = new Set(['hidden', 'submit', 'button', 'reset', 'image']);
  const text = (el) => (el ? (el.innerText || el.textContent || '').trim() : '');
  const out = [];
  let index = 0;

  document.querySelectorAll('input, select, textarea').forEach((el) => {
    if (el.tagName === 'INPUT' && SKIP.has((el.type || '').toLowerCase())) return;
    if (el.disabled) return;
    // A file input is often visually hidden behind a styled drop zone, so it
    // is the one kind that stays in even when it isn't painted.
    if (el.offsetParent === null && el.type !== 'file') return;

    let label = '';
    if (el.id) {
      const l = document.querySelector('label[for="' + CSS.escape(el.id) + '"]');
      label = text(l);
    }
    if (!label) label = text(el.closest('label'));
    if (!label && el.getAttribute('aria-labelledby')) {
      label = el.getAttribute('aria-labelledby').split(/\s+/)
        .map((id) => text(document.getElementById(id))).join(' ').trim();
    }
    if (!label) {
      const fs = el.closest('fieldset');
      if (fs) label = text(fs.querySelector('legend'));
    }
    if (!label) {
      // Common ATS shape: a div wrapper whose first text node is the label.
      const parent = el.parentElement;
      if (parent) {
        const own = Array.from(parent.childNodes)
          .filter((n) => n.nodeType === 3)
          .map((n) => n.textContent.trim())
          .filter(Boolean);
        if (own.length) label = own[0];
      }
    }

    const id = 'jp-' + (index++);
    el.setAttribute('data-jobpipe-id', id);

    out.push({
      id,
      tag: el.tagName.toLowerCase(),
      type: (el.type || '').toLowerCase(),
      name: el.name || '',
      label: label.replace(/\s+/g, ' ').slice(0, 200),
      placeholder: el.placeholder || '',
      ariaLabel: el.getAttribute('aria-label') || '',
      required: !!el.required,
      value: el.value || '',
      options: el.tagName === 'SELECT'
        ? Array.from(el.options).map((o) => ({ text: o.text.trim(), value: o.value }))
        : [],
    });
  });

  return out;
}

// Playwright evaluates this file and then calls the function; the extension
// loads the file and calls it directly. Keep the name stable for both.
if (typeof window !== 'undefined') {
  window.jobpipeExtractFields = jobpipeExtractFields;
}

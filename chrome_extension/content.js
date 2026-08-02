"use strict";

function findLabel(el) {
  if (el.id) {
    try {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return label.textContent.trim();
    } catch (e) {
      // Ignore malformed IDs.
    }
  }
  let parent = el.parentElement;
  for (let i = 0; i < 4 && parent; i++, parent = parent.parentElement) {
    if (parent.tagName.toLowerCase() === "label") return parent.textContent.trim();
  }
  return "";
}

function capturePageData() {
  const fields = [];
  document.querySelectorAll("input, select, textarea, button[type='submit']").forEach((el, index) => {
    const entry = {
      index,
      tag: el.tagName.toLowerCase(),
      type: el.type || el.tagName.toLowerCase(),
      name: el.name || "",
      id: el.id || "",
      class: el.className || "",
      label: findLabel(el),
      placeholder: el.placeholder || "",
      required: el.required || false,
    };
    if (el.tagName.toLowerCase() === "select") {
      entry.options = Array.from(el.options).map((o) => ({
        value: o.value,
        text: o.text.trim(),
      }));
    }
    fields.push(entry);
  });

  return {
    url: window.location.href,
    html: document.documentElement.outerHTML,
    fields,
    metadata: {
      title: document.title,
      userAgent: navigator.userAgent,
      timestamp: new Date().toISOString(),
    },
  };
}

chrome.runtime.onMessage.addListener((request, sender, sendResponse) => {
  if (request.action === "capture") {
    sendResponse(capturePageData());
  }
  return true;
});

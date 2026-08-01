"use strict";

function findLabel(el) {
  if (el.id) {
    const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (label) return label.textContent.trim();
  }
  let parent = el.parentElement;
  for (let i = 0; i < 3 && parent; i++, parent = parent.parentElement) {
    if (parent.tagName.toLowerCase() === "label") return parent.textContent.trim();
  }
  return "";
}

function capturePageData() {
  const fields = [];
  document.querySelectorAll("input, select, textarea").forEach((el, index) => {
    const entry = {
      index,
      tag: el.tagName.toLowerCase(),
      type: el.type || el.tagName.toLowerCase(),
      name: el.name || "",
      id: el.id || "",
      label: findLabel(el),
      placeholder: el.placeholder || "",
      required: el.required || false,
    };
    if (el.tagName.toLowerCase() === "select") {
      entry.options = Array.from(el.options).map((o) => o.text.trim());
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

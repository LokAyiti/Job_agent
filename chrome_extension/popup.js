"use strict";

const SCRAPLING_SERVICE_URL = "http://localhost:8723";

document.getElementById("capture").addEventListener("click", async () => {
  const status = document.getElementById("status");
  status.textContent = "Capturing...";

  try {
    const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
    if (!tab || !tab.id) {
      status.textContent = "No active tab found.";
      return;
    }

    const results = await chrome.scripting.executeScript({
      target: { tabId: tab.id },
      func: capturePageData,
    });

    const payload = results[0]?.result;
    if (!payload) {
      status.textContent = "Could not capture page data.";
      return;
    }

    const resp = await fetch(`${SCRAPLING_SERVICE_URL}/extension/snapshot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    });

    if (!resp.ok) {
      const text = await resp.text();
      status.textContent = `Server error ${resp.status}: ${text.slice(0, 120)}`;
      return;
    }

    const data = await resp.json();
    status.textContent = `Saved snapshot: ${data.platform}\n${data.snapshot_path}`;
  } catch (err) {
    status.textContent = `Error: ${err.message}`;
  }
});

function capturePageData() {
  const fields = [];
  const inputs = document.querySelectorAll("input, select, textarea");
  inputs.forEach((el, index) => {
    const label = findLabel(el);
    const entry = {
      index,
      tag: el.tagName.toLowerCase(),
      type: el.type || el.tagName.toLowerCase(),
      name: el.name || "",
      id: el.id || "",
      label: label || "",
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

function findLabel(el) {
  if (el.id) {
    const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
    if (label) return label.textContent.trim();
  }
  // Walk up a few ancestors looking for a label wrapper.
  let parent = el.parentElement;
  for (let i = 0; i < 3 && parent; i++, parent = parent.parentElement) {
    if (parent.tagName.toLowerCase() === "label") return parent.textContent.trim();
  }
  return "";
}

"use strict";

const SCRAPLING_SERVICE_URL = "http://localhost:8723";

let lastPayload = null;

function setStatus(text, isError = false) {
  const status = document.getElementById("status");
  status.textContent = text;
  status.className = isError ? "error" : "success";
}

function capturePageData() {
  const fields = [];
  const inputs = document.querySelectorAll("input, select, textarea, button[type='submit']");
  inputs.forEach((el, index) => {
    const label = findLabel(el);
    const entry = {
      index,
      tag: el.tagName.toLowerCase(),
      type: el.type || el.tagName.toLowerCase(),
      name: el.name || "",
      id: el.id || "",
      class: el.className || "",
      label: label || "",
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

function findLabel(el) {
  if (el.id) {
    try {
      const label = document.querySelector(`label[for="${CSS.escape(el.id)}"]`);
      if (label) return label.textContent.trim();
    } catch (e) {
      // CSS.escape may fail on unusual IDs; fall through.
    }
  }
  let parent = el.parentElement;
  for (let i = 0; i < 4 && parent; i++, parent = parent.parentElement) {
    if (parent.tagName.toLowerCase() === "label") return parent.textContent.trim();
  }
  return "";
}

async function getActiveTabPayload() {
  const [tab] = await chrome.tabs.query({ active: true, currentWindow: true });
  if (!tab || !tab.id) {
    throw new Error("No active tab found.");
  }

  const results = await chrome.scripting.executeScript({
    target: { tabId: tab.id },
    func: capturePageData,
  });

  const payload = results[0]?.result;
  if (!payload) {
    throw new Error("Could not capture page data.");
  }
  return payload;
}

document.getElementById("capture").addEventListener("click", async () => {
  setStatus("Capturing...");
  lastPayload = null;
  document.getElementById("generate").disabled = true;

  try {
    lastPayload = await getActiveTabPayload();
    const resp = await fetch(`${SCRAPLING_SERVICE_URL}/extension/snapshot`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastPayload),
    });

    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Server error ${resp.status}: ${text.slice(0, 120)}`);
    }

    const data = await resp.json();
    setStatus(`Saved snapshot: ${data.platform}\n${data.snapshot_path}\n\nClick "Generate Adapter Draft" to draft a SiteAdapter.`);
    document.getElementById("generate").disabled = false;
  } catch (err) {
    setStatus(`Error: ${err.message}`, true);
  }
});

document.getElementById("generate").addEventListener("click", async () => {
  if (!lastPayload) {
    setStatus("Capture a page first.", true);
    return;
  }

  setStatus("Generating adapter draft...");
  document.getElementById("generate").disabled = true;

  try {
    const resp = await fetch(`${SCRAPLING_SERVICE_URL}/extension/generate-adapter`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(lastPayload),
    });

    if (!resp.ok) {
      const text = await resp.text();
      throw new Error(`Server error ${resp.status}: ${text.slice(0, 120)}`);
    }

    const data = await resp.json();
    setStatus(
      `Draft generated: ${data.platform}\n` +
      `Snapshot: ${data.snapshot_path}\n` +
      `Draft: ${data.draft_path}\n\n` +
      `Review the draft, run a dry-run against a real posting, then approve with:\n` +
      `python -m job_agent.cli approve-adapter --platform ${data.platform}`
    );
  } catch (err) {
    setStatus(`Error: ${err.message}`, true);
    document.getElementById("generate").disabled = false;
  }
});

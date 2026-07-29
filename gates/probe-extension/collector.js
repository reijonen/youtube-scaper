// Isolated-world probe. Confirms the extension actually loaded in this profile
// and reports the runtime ID, which is what Gate B is checking.

console.log(
  "%c[ytProbe] isolated world alive",
  "color:#06c;font-weight:bold",
  { extensionId: chrome.runtime.id, url: location.href }
);

chrome.runtime.sendMessage({ type: "probe_hello", url: location.href }).catch(() => {});

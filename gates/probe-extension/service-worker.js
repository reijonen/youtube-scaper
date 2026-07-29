// Service-worker probe. Nothing here matters except that it runs, which proves the
// extension was registered in the copied profile with the expected ID.

const EXPECTED_ID = "mpjlhipdjoibcphphiemmnbckofdoimb";

const banner = () => {
  const ok = chrome.runtime.id === EXPECTED_ID;
  console.log(
    `%c[ytProbe] service worker up — id=${chrome.runtime.id} ${
      ok ? "MATCHES pinned key" : "DOES NOT MATCH pinned key"
    }`,
    `color:${ok ? "#0a0" : "#c00"};font-weight:bold`
  );
};

banner();
chrome.runtime.onInstalled.addListener(banner);
chrome.runtime.onStartup.addListener(banner);
chrome.runtime.onMessage.addListener((msg, sender) => {
  console.log("[ytProbe] from tab", sender.tab?.id, msg);
});

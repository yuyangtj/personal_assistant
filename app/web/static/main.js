// Startup: open the page's address, register the service worker, load the lists.

// Push notifications link to /?task=<id>; open that task over the chats view.
const linkedTask = new URLSearchParams(location.search).get("task");
if (linkedTask) history.replaceState(null, "", location.pathname + routeHash({ section: "chats", task: linkedTask }));
window.addEventListener("hashchange", render);
// Installable as an app; the service worker only keeps the page openable offline.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
Promise.all([loadChats(), loadRepositories(), loadDeploymentTargets(), loadItems(), loadSpaces(), loadPasskeys()]);
// With no address, the app opens in Assistant; the other chats are one tap back.
(async () => {
  if (!linkedTask && !location.hash) {
    try {
      state.homeId = (await api("/chat-sessions/home")).id;
      history.replaceState(null, "", location.pathname + routeHash({ section: "chats", id: state.homeId }));
    } catch (_) {
      history.replaceState(null, "", location.pathname + "#/chats");
    }
  }
  render();
})();

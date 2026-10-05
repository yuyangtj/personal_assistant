// Startup: open the page's address, register the service worker, load the lists.

// Push notifications link to /?task=<id>; open that task over the chats view.
const linkedTask = new URLSearchParams(location.search).get("task");
if (linkedTask) history.replaceState(null, "", location.pathname + routeHash({ section: "chats", task: linkedTask }));
else if (!location.hash) history.replaceState(null, "", location.pathname + "#/chats");
window.addEventListener("hashchange", render);
// Installable as an app; the service worker only keeps the page openable offline.
if ("serviceWorker" in navigator) navigator.serviceWorker.register("/sw.js").catch(() => {});
Promise.all([loadChats(), loadRepositories(), loadDeploymentTargets(), loadItems(), loadSpaces(), loadPasskeys()]);
render();

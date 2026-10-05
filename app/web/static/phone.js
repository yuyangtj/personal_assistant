// Inside the Android app, a native bridge adds what only the phone can do; in a browser
// this file does nothing.

// Inside the Android app, a native bridge adds what only the phone can do: voice, spoken
// replies, timers and alarms, and simple answers that stay on the phone. In a browser
// none of this exists and the console works as before.
const phone = { bridge: window.AndroidAssistant || null, caps: null, routes: new Map(), next: 0,
                turns: {}, speakFor: null, listening: false, dictated: false, dictationBase: "", screen: null };

function phoneSend(message) { phone.bridge.postMessage(JSON.stringify(message)); }

function phoneRoute(text) {
  return new Promise((resolve) => {
    const id = `r${++phone.next}`;
    phone.routes.set(id, resolve);
    phoneSend({ type: "route", id, text });
    // On-device models can be slow to wake; the server answers instead after a while.
    setTimeout(() => { if (phone.routes.delete(id)) resolve(null); }, 6000);
  });
}

// "yes", "Start coding" and the like answer the assistant's open choices on the server.
function awaitingChoice() {
  const last = [...state.messages].reverse().find((m) => m.role !== "system");
  return Boolean(last && last.role === "assistant"
    && (last.blocks || []).some((block) => block.type === "choices" && block.state === "open"));
}

const phoneTurns = () => phone.turns[state.chatId || ""] || (phone.turns[state.chatId || ""] = []);

function answerOnPhone(text, route, voice) {
  addPhoneTurn({ role: "user", text });
  const answer = route.route === "reply" ? route.text : `${route.label}?`;
  addPhoneTurn({ role: "assistant", text: answer, action: route.route === "confirm" ? route.action : null });
  if (voice) phoneSend({ type: "speak", text: answer });
  renderTranscript();
  $("transcript").scrollTop = $("transcript").scrollHeight;
}

// Answered on the phone, so not saved in the conversation; they last until the app closes.
// Each sits right after the saved message that was last when it happened (the phone's
// and the server's clocks can disagree, so time can't order them).
function phoneTurnsAfter(messageId) {
  return (phone.turns[state.chatId || ""] || []).filter((turn) => turn.after === messageId).map((turn) => {
    const buttons = turn.action ? `<div class="choices">`
      + `<button type="button" data-phone-action="${esc(turn.action)}" ${turn.settled ? "disabled" : ""}>Do it</button>`
      + `<button type="button" class="quiet" data-phone-cancel="${esc(turn.action)}" ${turn.settled ? "disabled" : ""}>Cancel</button></div>` : "";
    return `<div class="msg ${turn.role}"><div class="who">${turn.role === "user" ? "YOU" : "ON THIS PHONE"}</div>`
      + `<div class="bubble">${esc(turn.text)}${buttons}</div></div>`;
  }).join("");
}

function addPhoneTurn(turn) {
  const last = state.messages[state.messages.length - 1];
  phoneTurns().push({ ...turn, after: last ? last.id : null });
}

function settlePhoneAction(id) {
  const turn = phoneTurns().find((candidate) => candidate.action === id);
  if (turn) turn.settled = true;
  return turn;
}

function runPhoneAction(id) {
  if (settlePhoneAction(id)) { phoneSend({ type: "run_action", id }); renderTranscript(); }
}

function cancelPhoneAction(id) {
  if (settlePhoneAction(id)) { addPhoneTurn({ role: "assistant", text: "OK, cancelled." }); renderTranscript(); }
}

function speakReplyIfWaiting() {
  if (!phone.speakFor) return;
  const index = state.messages.findIndex((m) => m.id === phone.speakFor);
  const reply = index >= 0 && state.messages.slice(index + 1).find((m) => m.role === "assistant");
  if (!reply) return;
  phone.speakFor = null;
  phoneSend({ type: "speak", text: reply.content });
}

const withDictation = (text) =>
  [phone.dictationBase, (text || "").trim()].filter(Boolean).join(" ");

function showListening(listening) {
  phone.listening = listening;
  $("mic").classList.toggle("listening", listening);
  $("mic").setAttribute("aria-label", listening ? "Stop listening" : "Speak");
  $("input").placeholder = listening ? "Listening…"
    : "Say something — mention a work item with #tag. Nothing runs on its own…";
}

// Context handed over from other apps: shared text goes into the box to edit, a shared
// file is uploaded into the chat, and the screen (from the assistant gesture) rides along
// with the next message.
function setScreen(screen) {
  phone.screen = screen;
  const chip = $("screen-chip");
  chip.hidden = !screen;
  chip.innerHTML = screen
    ? `📱 Screen${screen.app ? " from " + esc(screen.app) : ""} · ${esc(screen.text.length.toLocaleString())} characters`
      + ` <button type="button" class="chip-x" id="screen-remove" aria-label="Don't send the screen">✕</button>`
    : "";
  if (screen) $("screen-remove").onclick = () => setScreen(null);
}

// Context arriving with no chat open (phones show the chat list then) starts a new one,
// so the chat box and the chip are on screen.
async function ensureChatOpen() {
  if (state.chatId && parseRoute().id === state.chatId) return;
  const chat = state.chatId || (await api("/chat-sessions", { method: "POST", body: "{}" })).id;
  go({ section: "chats", id: chat });
  await render();
  loadChats();
}

async function shareFile(name, text) {
  try {
    await ensureChatOpen();
    await api(`/chat-sessions/${state.chatId}/files`, {
      method: "POST", body: JSON.stringify({ filename: name, content: text }),
    });
    await refreshTranscript();
  } catch (error) { fail(error); }
}

function renderDeviceAi(caps) {
  const note = $("phone-note");
  if (caps.onDevice === "downloadable") {
    note.innerHTML = `Simple requests could stay on this phone.<button type="button" class="quiet" id="enable-on-device">Download on-device AI</button>`;
    $("enable-on-device").onclick = () => phoneSend({ type: "enable_on_device" });
  } else if (caps.onDevice === "downloading") {
    note.textContent = caps.onDeviceDetail || "Downloading on-device AI…";
  } else {
    note.textContent = "";
  }
  // Until it is the phone's assistant, offer the settings page once (dismissable).
  let dismissed = false;
  try { dismissed = localStorage.getItem("assistantHintDismissed") === "1"; } catch (_) {}
  if (caps.assistant === false && !dismissed && !note.querySelector("#make-assistant")) {
    note.insertAdjacentHTML("beforeend", `<div>Open me with a long press on the power button, and let me`
      + ` read the screen you're on. <button type="button" class="quiet" id="make-assistant">Set as phone assistant</button>`
      + ` <button type="button" class="ghost" id="skip-assistant">Not now</button></div>`);
    $("make-assistant").onclick = () => phoneSend({ type: "open_assistant_settings" });
    $("skip-assistant").onclick = () => {
      try { localStorage.setItem("assistantHintDismissed", "1"); } catch (_) {}
      $("make-assistant").parentElement.remove();
      note.hidden = !note.textContent.trim();
    };
  }
  note.hidden = !note.textContent.trim();
}

function onPhoneMessage(message) {
  if (message.type === "capabilities" || message.type === "device_ai") {
    phone.caps = { ...(phone.caps || {}), ...message };
    if (message.type === "capabilities") $("mic").hidden = !message.voice;
    renderDeviceAi(phone.caps);
  } else if (message.type === "voice") {
    if (message.state === "listening") {
      // Dictation adds to what is already in the box instead of replacing it.
      phone.dictationBase = $("input").value.trimEnd();
      showListening(true);
    } else if (message.state === "partial") $("input").value = withDictation(message.text);
    else if (message.state === "final") {
      showListening(false);
      // Into the box to check or edit first; the reply is still spoken when it's sent.
      $("input").value = withDictation(message.text);
      phone.dictated = phone.dictated || Boolean(message.text);
      $("input").focus();
    } else if (message.state === "error") {
      showListening(false);
      if (message.text) fail(new Error(message.text));
    }
  } else if (message.type === "shared") {
    ensureChatOpen().then(() => {
      phone.dictationBase = $("input").value.trimEnd();
      $("input").value = withDictation(message.text);
      $("input").focus();
    }).catch(fail);
  } else if (message.type === "shared_file") {
    shareFile(message.name || "shared file", message.text || "");
  } else if (message.type === "screen") {
    ensureChatOpen().then(() => {
      setScreen(message.text ? { app: message.app || "", text: message.text } : null);
      // The assistant gesture means "I want to say something": listen right away.
      if (!phone.listening) phoneSend({ type: "listen" });
    }).catch(fail);
  } else if (message.type === "route") {
    const resolve = phone.routes.get(message.id);
    phone.routes.delete(message.id);
    if (resolve) resolve(message);
  } else if (message.type === "action_result") {
    addPhoneTurn({ role: "assistant", text: message.error || "Done." });
    renderTranscript();
  }
}

if (phone.bridge) {
  phone.bridge.onmessage = (event) => onPhoneMessage(JSON.parse(event.data));
  $("mic").onclick = () => phoneSend({ type: phone.listening ? "stop_listening" : "listen" });
  phoneSend({ type: "hello" });
}

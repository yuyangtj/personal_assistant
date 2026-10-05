// Inside the Android app, a native bridge adds what only the phone can do; in a browser
// this file does nothing.

// Inside the Android app, a native bridge adds what only the phone can do: voice, spoken
// replies, timers and alarms, and simple answers that stay on the phone. In a browser
// none of this exists and the console works as before.
const phone = { bridge: window.AndroidAssistant || null, caps: null, routes: new Map(), next: 0,
                turns: {}, speakFor: null, listening: false };

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

function showListening(listening) {
  phone.listening = listening;
  $("mic").classList.toggle("listening", listening);
  $("mic").setAttribute("aria-label", listening ? "Stop listening" : "Speak");
  $("input").placeholder = listening ? "Listening…"
    : "Say something — mention a work item with #tag. Nothing runs on its own…";
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
  note.hidden = !note.textContent;
}

function onPhoneMessage(message) {
  if (message.type === "capabilities" || message.type === "device_ai") {
    phone.caps = { ...(phone.caps || {}), ...message };
    if (message.type === "capabilities") $("mic").hidden = !message.voice;
    renderDeviceAi(phone.caps);
  } else if (message.type === "voice") {
    if (message.state === "listening") showListening(true);
    else if (message.state === "partial") $("input").value = message.text || "";
    else if (message.state === "final") {
      showListening(false);
      $("input").value = message.text || "";
      $("input").focus();
    } else if (message.state === "error") {
      showListening(false);
      if (message.text) fail(new Error(message.text));
    }
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

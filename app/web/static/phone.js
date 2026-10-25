// Inside the Android app, a native bridge adds what only the phone can do: voice, spoken
// replies, and running the timers, alarms and events a reply proposes. It never decides
// what a message means: every message goes to the server. In a browser none of this
// exists and the console works as before.
const phone = { bridge: window.AndroidAssistant || null, caps: null,
                speakFor: null, voiceChat: null, listening: false, dictated: false, dictationBase: "", screen: null,
                // Phone-action cards on replies, by block id: what the app said after
                // checking the action itself ({ action, label } or { error }).
                offers: {} };

// What a proposed phone action does, in words; shown in a browser, where nothing can run.
function describePhoneAction(action) {
  if (action.type === "set_timer") {
    const minutes = Math.round(action.seconds / 60);
    return action.seconds < 120 ? `Set a ${action.seconds}-second timer` : `Set a ${minutes}-minute timer`;
  }
  if (action.type === "set_alarm") {
    return `Set an alarm for ${String(action.hour).padStart(2, "0")}:${String(action.minute).padStart(2, "0")}`;
  }
  return `Add “${action.title}” to your calendar (${action.start.replace("T", " ")})`;
}

// The card on a reply that proposed a phone action. The page only names the block; the
// app fetches the action from the server, checks it and gives the label shown here.
BLOCKS.phone_action = (block, m) => {
  if (block.state === "done") return `<div class="upload-done">✓ ${esc(block.result || "Done")}</div>`;
  if (block.state === "cancelled") return `<div class="phone-action-note">Cancelled</div>`;
  if (!phone.bridge) {
    return `<div class="phone-action-note">📱 ${esc(describePhoneAction(block.action))}:`
      + ` open the assistant on your phone to do it.</div>`;
  }
  const offer = phone.offers[block.id];
  if (!offer) {
    phone.offers[block.id] = { pending: true };
    phoneSend({ type: "offer_message_action", id: block.id, text: `${state.chatId}/${m.id}` });
    return `<div class="phone-action-note">Checking on the phone…</div>`;
  }
  if (offer.pending) return `<div class="phone-action-note">Checking on the phone…</div>`;
  if (offer.error) return `<div class="phone-action-note">${esc(offer.error)}</div>`;
  const busy = offer.running ? "disabled" : "";
  return `<div class="phone-action-card"><div>${esc(offer.label)}?</div><div class="choices">`
    + `<button type="button" data-message-action="${esc(block.id)}" data-message="${esc(m.id)}" ${busy}>Do it</button>`
    + `<button type="button" class="quiet" data-message-cancel="${esc(block.id)}" data-message="${esc(m.id)}" ${busy}>Cancel</button>`
    + `</div></div>`;
};

function settleMessageAction(blockId, messageId, settled, result) {
  return api(`/chat-sessions/${state.chatId}/messages/${messageId}/phone-actions/${blockId}`, {
    method: "POST", body: JSON.stringify({ state: settled, result: result || "" }),
  }).then(() => refreshTranscript()).catch(fail);
}

function runMessageAction(blockId, messageId) {
  const offer = phone.offers[blockId];
  if (!offer || !offer.action || offer.running) return;
  offer.running = true;
  offer.message = messageId;
  phoneSend({ type: "run_action", id: offer.action });
  renderTranscript();
}

function cancelMessageAction(blockId, messageId) {
  delete phone.offers[blockId];
  settleMessageAction(blockId, messageId, "cancelled");
}

// Spoken replies: the phone does not say when it finishes, so "speaking" is an estimate
// from the text length (it speaks at most 2,000 characters). Muting keeps the text only.
const SPEECH_MS_PER_CHARACTER = 60;
let speakingTimer = null;
const readMuted = () => { try { return localStorage.getItem("assistantMuted") === "1"; } catch (_) { return false; } };
phone.muted = readMuted();

function showSpeaking(on) {
  $("stop-speaking").hidden = !on;
  if (!on) { clearTimeout(speakingTimer); speakingTimer = null; window.avatarEvent?.("stop"); }
}

function phoneSpeak(text) {
  if (phone.muted) return;
  phoneSend({ type: "speak", text });
  showSpeaking(true);
  clearTimeout(speakingTimer);
  const ms = Math.min(text.length, 2000) * SPEECH_MS_PER_CHARACTER;
  window.avatarEvent?.("speak", { text, ms });
  speakingTimer = setTimeout(() => showSpeaking(false), ms + 1500);
}

function stopSpeaking() {
  if (!phone.bridge) return;
  phoneSend({ type: "stop_speaking" });
  showSpeaking(false);
}

const SPEAKER_ICON = (waves) => `<svg viewBox="0 0 24 24" width="22" height="22" fill="none"
  stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"
  aria-hidden="true"><path d="M4 10v4h3.5L12 18V6L7.5 10z"/>${waves}</svg>`;
const SPEAKER_ON = SPEAKER_ICON('<path d="M15.5 9a4 4 0 0 1 0 6M18 6.5a7.5 7.5 0 0 1 0 11"/>');
const SPEAKER_OFF = SPEAKER_ICON('<path d="M16 9.5l5 5M21 9.5l-5 5"/>');

function setMuted(muted) {
  phone.muted = muted;
  try { localStorage.setItem("assistantMuted", muted ? "1" : "0"); } catch (_) {}
  const button = $("mute-voice");
  button.innerHTML = muted ? SPEAKER_OFF : SPEAKER_ON;
  button.setAttribute("aria-pressed", String(muted));
  button.setAttribute("aria-label", muted ? "Unmute spoken replies" : "Mute spoken replies");
  button.title = button.getAttribute("aria-label");
  if (muted) { stopSpeaking(); phone.voiceChat = null; }
}

function phoneSend(message) { phone.bridge.postMessage(JSON.stringify(message)); }

function speakReplyIfWaiting() {
  if (!phone.speakFor) return;
  const index = state.messages.findIndex((m) => m.id === phone.speakFor);
  const reply = index >= 0 && state.messages.slice(index + 1).find((m) => m.role === "assistant");
  if (!reply) return;
  phone.speakFor = null;
  phoneSpeak(reply.content);
}

const withDictation = (text) =>
  [phone.dictationBase, (text || "").trim()].filter(Boolean).join(" ");

function showListening(listening) {
  phone.listening = listening;
  window.avatarEvent?.("listening", { on: listening });
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

function renderPhoneHint(caps) {
  const note = $("phone-note");
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
    if (message.type === "capabilities") {
      $("mic").hidden = !message.voice;
      $("mute-voice").hidden = !message.voice;
    }
    renderPhoneHint(phone.caps);
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
  } else if (message.type === "message_action") {
    // The app checked a reply's phone action: show its label with Do it / Cancel.
    phone.offers[message.id] = message.error ? { error: message.error }
      : { action: message.action, label: message.label };
    renderTranscript();
  } else if (message.type === "action_result") {
    const offered = Object.entries(phone.offers).find(([, offer]) => offer.action === message.action);
    if (offered) {
      const [blockId, offer] = offered;
      delete phone.offers[blockId];
      if (message.error) {
        phone.offers[blockId] = { error: message.error };
        renderTranscript();
      } else settleMessageAction(blockId, offer.message, "done", offer.label);
    }
  }
}

if (phone.bridge) {
  phone.bridge.onmessage = (event) => onPhoneMessage(JSON.parse(event.data));
  $("mic").onclick = () => {
    stopSpeaking(); // talking over the assistant: let it be quiet
    phoneSend({ type: phone.listening ? "stop_listening" : "listen" });
  };
  $("stop-speaking").onclick = stopSpeaking;
  $("mute-voice").onclick = () => setMuted(!phone.muted);
  setMuted(phone.muted);
  $("composer").addEventListener("submit", stopSpeaking, true);
  phoneSend({ type: "hello" });
}

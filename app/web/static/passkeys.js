// Passkeys: approve with Touch ID or a fingerprint instead of typing the approval token.
// Each approval is a fresh signature over a challenge the server issued for that one
// request; adding a passkey needs the token once.

state.passkeys = { enabled: false, list: [] };
let usePasskey = false;

const passkeySupported = () => Boolean(window.PublicKeyCredential && navigator.credentials);

function fromB64url(value) {
  const base64 = value.replace(/-/g, "+").replace(/_/g, "/");
  const bytes = atob(base64 + "=".repeat((4 - (base64.length % 4)) % 4));
  return Uint8Array.from(bytes, (c) => c.charCodeAt(0)).buffer;
}
function toB64url(buffer) {
  let text = "";
  for (const byte of new Uint8Array(buffer)) text += String.fromCharCode(byte);
  return btoa(text).replace(/\+/g, "-").replace(/\//g, "_").replace(/=+$/, "");
}
const descriptors = (list) => (list || []).map((item) => ({ ...item, id: fromB64url(item.id) }));

function credentialJson(credential) {
  const response = credential.response;
  const json = { id: credential.id, rawId: toB64url(credential.rawId), type: credential.type,
    response: { clientDataJSON: toB64url(response.clientDataJSON) } };
  if (response.attestationObject) {
    json.response.attestationObject = toB64url(response.attestationObject);
    if (response.getTransports) json.response.transports = response.getTransports();
  } else {
    json.response.authenticatorData = toB64url(response.authenticatorData);
    json.response.signature = toB64url(response.signature);
    if (response.userHandle) json.response.userHandle = toB64url(response.userHandle);
  }
  return json;
}

async function loadPasskeys() {
  try {
    const body = await api("/passkeys");
    state.passkeys = { enabled: body.enabled, list: body.passkeys };
  } catch (_) { state.passkeys = { enabled: false, list: [] }; }
  $("passkeys-open").hidden = !(state.passkeys.enabled && passkeySupported());
}

// Whether the approve dialog asks for a passkey (the default once one exists) or the token.
function setApprovalMode(passkey) {
  usePasskey = passkey && state.passkeys.list.length > 0 && passkeySupported();
  $("approval-token").hidden = usePasskey;
  $("approval-token").required = !usePasskey;
  $("approval-token-label").hidden = usePasskey;
  $("approval-mode").hidden = !(state.passkeys.list.length > 0 && passkeySupported());
  $("approval-mode").textContent = usePasskey ? "Use the approval token instead" : "Use a passkey instead";
}

// The header that authorizes one request: a passkey signature for it, or the token.
async function approvalAuth(method, path) {
  if (!usePasskey) return { "X-Assistant-Approval-Token": $("approval-token").value };
  return passkeyAuth(method, path);
}

async function passkeyAuth(method, path) {
  const options = await api("/passkeys/approval-options", {
    method: "POST", body: JSON.stringify({ method, path }),
  });
  const credential = await navigator.credentials.get({ publicKey: {
    ...options, challenge: fromB64url(options.challenge),
    allowCredentials: descriptors(options.allowCredentials),
  } });
  return { "X-Assistant-Passkey": JSON.stringify(credentialJson(credential)) };
}

function renderPasskeyList() {
  const list = state.passkeys.list;
  $("passkey-list").innerHTML = list.length
    ? list.map((p) => `<div class="row"><span class="grow">${esc(p.name)}
        <span class="sub">added ${esc(String(p.created_at).slice(0, 10))}${p.last_used_at ? ", last used " + esc(String(p.last_used_at).slice(0, 10)) : ""}</span></span>
        <button type="button" class="quiet" data-remove-passkey="${esc(p.id)}">Remove</button></div>`).join("")
    : `<div class="sub">No passkeys yet. Add this device to approve with Touch ID or your fingerprint.</div>`;
}

function openPasskeyDialog() {
  renderPasskeyList();
  $("passkey-name").value = /Android/.test(navigator.userAgent) ? "Phone" : /Mac/.test(navigator.userAgent) ? "Mac" : "This device";
  $("passkey-token").value = "";
  $("passkey-error").textContent = "";
  $("passkey-dialog").showModal();
}

async function addPasskey(event) {
  event.preventDefault();
  $("passkey-error").textContent = "";
  try {
    const options = await api("/passkeys/registration-options", { method: "POST", body: "{}" });
    const credential = await navigator.credentials.create({ publicKey: {
      ...options, challenge: fromB64url(options.challenge),
      user: { ...options.user, id: fromB64url(options.user.id) },
      excludeCredentials: descriptors(options.excludeCredentials),
    } });
    await api("/passkeys", {
      method: "POST",
      headers: { "content-type": "application/json", "X-Assistant-Approval-Token": $("passkey-token").value },
      body: JSON.stringify({ credential: credentialJson(credential), name: $("passkey-name").value }),
    });
    $("passkey-token").value = "";
    await loadPasskeys();
    renderPasskeyList();
  } catch (error) { $("passkey-error").textContent = error.message || String(error); }
}

async function removePasskey(id) {
  $("passkey-error").textContent = "";
  try {
    // The token if typed, otherwise a passkey (possibly the one being removed).
    const path = `/passkeys/${encodeURIComponent(id)}`;
    const token = $("passkey-token").value;
    const headers = token ? { "X-Assistant-Approval-Token": token } : await passkeyAuth("DELETE", path);
    await api(path, { method: "DELETE", headers });
    await loadPasskeys();
    renderPasskeyList();
  } catch (error) { $("passkey-error").textContent = error.message || String(error); }
}

$("passkeys-open").onclick = openPasskeyDialog;
$("passkey-close").onclick = () => $("passkey-dialog").close();
$("passkey-form").onsubmit = addPasskey;
$("passkey-list").onclick = (event) => {
  const id = event.target.dataset && event.target.dataset.removePasskey;
  if (id) removePasskey(id);
};
$("approval-mode").onclick = () => setApprovalMode(!usePasskey);

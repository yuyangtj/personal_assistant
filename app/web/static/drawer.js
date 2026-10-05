// The side panel: a task (progress, pull request gate, deployment) or a coding
// workflow, and the approval dialog.

async function renderWorkflow(id) {
  state.workflowId = id;
  state.taskId = null;
  clearTimeout(state.taskTimer);
  try {
    const run = await api(`/workflow-runs/${id}`);
    if (state.workflowId !== id) return;
    const proposed = run.status === "proposed";
    $("detail-title").textContent = "Coding workflow";
    $("task-detail").innerHTML = `
      <div class="row wrap">${statusPill(proposed ? "waiting_for_approval" : run.status, workflowStatus(run.status))}
        <span class="status-pill">${esc(run.input.repository_id || "")}</span></div>
      <h2 style="margin-top:12px">Request</h2><div style="white-space:pre-wrap">${esc(run.input.request || "")}</div>
      <div class="sub" style="margin-top:12px">${proposed
        ? "Approving starts a coding agent on a new branch. It validates the change and opens a draft pull request; nothing merges without your separate approval."
        : `Stage: ${esc(label(run.current_stage || run.status))}`}</div>
      <div class="row wrap">
        ${proposed ? `<button id="approve-workflow">Approve and start</button>` : ""}
        ${run.status === "approved" && !run.task_id ? `<button id="start-workflow">Start workflow</button>` : ""}
        ${run.task_id ? `<button class="quiet" data-task-nav="${esc(run.task_id)}">View coding run</button>` : ""}</div>`;
    const approve = $("approve-workflow");
    if (approve) approve.onclick = () => openActionDialog("workflow", run.id, run);
    const start = $("start-workflow");
    if (start) start.onclick = () => startCodingWorkflow(run.id).catch(() => {});
    openTaskPanel();
  } catch (e) { fail(e); }
}

function openTaskPanel() {
  const panel = $("task-panel");
  panel.classList.add("open");
  panel.inert = false;
  panel.removeAttribute("aria-hidden");
  $("detail-scrim").classList.add("open");
}

function closeTaskPanel() {
  const panel = $("task-panel");
  panel.classList.remove("open");
  $("detail-scrim").classList.remove("open");
  panel.inert = true;
  panel.setAttribute("aria-hidden", "true");
}

function hideTask() {
  state.taskId = null;
  state.workflowId = null;
  clearTimeout(state.taskTimer);
  state.taskTimer = null;
  state.prPollAttempts = 0;
  closeTaskPanel();
}

// The agent's own plan and its latest steps, while it works and after.
function liveProgress(task, events) {
  const steps = events.filter((event) => event.event_type === "TASK_PROGRESS"
    && event.payload.kind !== "plan").slice(-8);
  if (!task.plan && !steps.length) return "";
  return `<h2>${active(task) ? "Live progress" : "What the agent did"}</h2><div class="panel-card">`
    + (task.plan ? `<div class="plan">${esc(task.plan)}</div>` : "")
    + steps.map((event) => `<div class="ev">${esc(new Date(event.created_at).toLocaleTimeString())} · ${esc(event.payload.text)}</div>`).join("")
    + `</div>`;
}

function codingWorkflow(ctx, task, prStatus) {
  const checkpoint = ctx.coding_checkpoint;
  if (!checkpoint) return "";
  const phase = checkpoint.phase;
  const rank = { preparing: 0, agent_running: 1, validated: 2, validation_failed: 2,
    committed: 3, pushed: 4, pr_created: 5 }[phase] ?? 0;
  const validationFailed = phase === "validation_failed" || ctx.validation === "failed";
  const ciState = prStatus ? prStatus.required_checks_state : "waiting";
  const reviewDone = Boolean(prStatus && !prStatus.draft);
  const merged = task.status === "completed" && Boolean(checkpoint.pull_request_number);
  const steps = [
    { name: "Implement", note: checkpoint.runner_attempts?.length
      ? `Provider: ${checkpoint.runner_attempts.at(-1).provider}` : "Coding agent", state: rank >= 2 ? "done" : task.status === "failed" ? "failed" : "current" },
    { name: "Validate", note: ctx.validation || "Not started", state: validationFailed ? "failed" : rank >= 2 ? "done" : rank === 1 ? "current" : "pending" },
    { name: "Commit", note: shortSha(checkpoint.commit_sha), state: checkpoint.commit_sha ? "done" : rank >= 2 ? "current" : "pending" },
    { name: "Push", note: checkpoint.branch || "Waiting for commit", state: rank >= 4 ? "done" : rank === 3 ? "current" : "pending" },
    { name: "Draft PR", note: checkpoint.pull_request_number ? `#${checkpoint.pull_request_number}` : "Not created", state: checkpoint.pull_request_number ? "done" : rank === 4 ? "current" : "pending" },
    { name: "CI", note: prStatus ? label(ciState) : "Awaiting live PR status", state: ciState === "passed" ? "done" : ciState === "failed" || ciState === "missing" ? "failed" : checkpoint.pull_request_number ? "current" : "pending" },
    { name: "Review", note: prStatus ? `${prStatus.unresolved_thread_count} unresolved thread${prStatus.unresolved_thread_count === 1 ? "" : "s"}` : "Not ready", state: reviewDone ? "done" : prStatus && ciState === "passed" ? "current" : "pending" },
    { name: "Merge", note: merged ? "Task completed" : "Requires explicit approval", state: merged ? "done" : reviewDone && ciState === "passed" ? "current" : "pending" },
  ];
  return `<h2>Coding progress</h2><div class="workflow">${steps.map((step) => `
    <div class="workflow-step ${step.state}"><span class="step-dot"></span>
      <div><div class="step-name">${esc(step.name)}</div><div class="step-note">${esc(step.note)}</div></div>
      <span class="step-state">${esc(step.state)}</span></div>`).join("")}</div>`;
}

function validationPanel(ctx) {
  const checkpoint = ctx.coding_checkpoint;
  if (!checkpoint) return "";
  const validations = checkpoint.validation || [];
  const attempts = checkpoint.runner_attempts || [];
  return `<h2>Execution evidence</h2>
    <div class="panel-card"><h3>Agent attempts</h3>
      ${attempts.map((attempt) => `<div class="attempt">${statusPill(attempt.outcome)}
        ${esc(attempt.provider || "unknown provider")}${attempt.category ? ` · ${esc(label(attempt.category))}` : ""}</div>`).join("") || `<div class="sub">No provider attempts recorded yet.</div>`}
    </div>
    <div class="panel-card"><h3>Local validation</h3>
      ${validations.map((item) => `<details ${item.passed ? "" : "open"}>
        <summary>${statusPill(item.passed ? "passed" : "failed")} ${esc(item.id || "Validation step")}</summary>
        <div class="validation-meta">${item.required ? "Required" : "Optional"} · exit ${esc(item.exit_code ?? "—")} · ${esc(item.duration_ms ?? "—")} ms</div>
        ${item.output ? `<pre class="validation-output">${esc(item.output)}</pre>` : ""}
      </details>`).join("") || `<div class="sub">Validation has not produced results yet.</div>`}
    </div>`;
}

function pullRequestPanel(prStatus, approval) {
  if (!prStatus) return "";
  const checks = prStatus.checks.map((check) => {
    const result = check.conclusion || check.status;
    const name = check.url ? `<a href="${esc(check.url)}" target="_blank" rel="noreferrer">${esc(check.name)}</a>` : esc(check.name);
    return `<div class="check-row"><span class="dot ${tone(result) || "muted"}"></span><span>${name}${check.required ? " · required" : ""}</span><span class="result">${esc(label(result))}</span></div>`;
  }).join("") || `<div class="sub">No GitHub checks reported.</div>`;
  return `<h2>Pull request gate</h2><div class="panel-card">
    <div class="row wrap"><a href="${esc(prStatus.url)}" target="_blank" rel="noreferrer"><strong>${esc(prStatus.repository)} #${esc(prStatus.number)}</strong></a>
      ${statusPill(prStatus.draft ? "draft" : prStatus.state, prStatus.draft ? "draft" : prStatus.state)}
      <button type="button" class="ghost" id="refresh-pr-status">Refresh CI</button></div>
    <div class="summary-grid">
      <div class="summary-card"><div class="key">Required CI</div><div class="value">${statusPill(prStatus.required_checks_state)}</div></div>
      <div class="summary-card"><div class="key">Mergeable</div><div class="value">${statusPill(prStatus.mergeable === true ? "mergeable" : prStatus.mergeable === false ? "failed" : "pending", prStatus.mergeable === true ? "yes" : prStatus.mergeable === false ? "no" : "unknown")}</div></div>
      <div class="summary-card"><div class="key">Expected SHA</div><div class="value">${esc(shortSha(prStatus.expected_head_sha))}</div></div>
      <div class="summary-card"><div class="key">Current SHA</div><div class="value">${esc(shortSha(prStatus.current_head_sha))} ${prStatus.head_matches ? "" : statusPill("stale", "mismatch")}</div></div>
    </div>${checks}
    <div class="sub" style="margin:8px 0 0">${esc(prStatus.unresolved_thread_count)} unresolved review thread${prStatus.unresolved_thread_count === 1 ? "" : "s"}.</div>
  </div>`;
}

function deploymentPanel(task, ctx, run, runEvents) {
  const mergeArtifact = [...(ctx.artifacts || [])].reverse().find((artifact) =>
    artifact.type === "github_pull_request_merge" && artifact.merge_sha);
  if (task.status !== "completed" || !mergeArtifact) return "";
  const repositoryId = task.source_context.repository_id;
  const targets = state.deploymentTargets.filter((target) =>
    target.enabled && target.repository_id === repositoryId);
  if (!targets.length) return `<h2>Deployment</h2><div class="gate-notice">No enabled deployment target is registered for this repository.</div>`;
  if (!run) return `<h2>Deployment</h2><div class="panel-card">
    <div class="sub">Deploy reviewed commit ${esc(shortSha(mergeArtifact.merge_sha))} through a registered target.</div>
    <div class="row wrap"><select id="deployment-target" aria-label="Deployment target">
      ${targets.map((target) => `<option value="${esc(target.id)}">${esc(target.name)} · ${esc(target.environment)}</option>`).join("")}
    </select><button type="button" id="propose-deployment">Propose deployment</button></div>
  </div>`;
  const latestExternal = [...(runEvents || [])].reverse().find((event) => event.payload?.url);
  const latestLog = [...(runEvents || [])].reverse().find((event) => event.payload?.log_tail);
  const rolledBack = (runEvents || []).find((event) => event.event_type === "DEPLOYMENT_ROLLED_BACK");
  const actions = run.status === "proposed"
    ? `<button type="button" id="approve-deployment">Approve deployment</button>`
    : run.status === "approved"
      ? `<button type="button" id="start-deployment">Start deployment</button>` : "";
  return `<h2>Deployment</h2><div class="panel-card">
    <div class="row wrap">${statusPill(run.status)}<strong>${esc(label(run.current_stage || "awaiting approval"))}</strong></div>
    <div class="summary-grid">
      <div class="summary-card"><div class="key">Target</div><div class="value">${esc(run.input.deployment_target_id)}</div></div>
      <div class="summary-card"><div class="key">Commit</div><div class="value">${esc(shortSha(run.input.commit_sha))}</div></div>
    </div>
    ${latestExternal ? `<a href="${esc(latestExternal.payload.url)}" target="_blank" rel="noreferrer">View deployment run</a>` : ""}
    ${rolledBack ? `<div class="gate-notice">The new release failed its health check; the previous release ${esc(shortSha(rolledBack.payload.previous_sha || ""))} is serving again.</div>` : ""}
    ${run.status === "failed" && latestLog ? `<pre>${esc(latestLog.payload.log_tail)}</pre>` : ""}
    ${actions ? `<div class="row wrap" style="margin-top:10px">${actions}</div>` : ""}
  </div>`;
}

function gateReason(prStatus, action) {
  if (!prStatus) return "Live GitHub status is unavailable.";
  if (!prStatus.head_matches) return "The pull request head no longer matches the approved commit.";
  if (prStatus.state !== "open") return `The pull request is ${label(prStatus.state)}, not open.`;
  if (action === "revision") return "";
  if (prStatus.required_checks_state === "missing") return "A required GitHub check is missing.";
  if (prStatus.required_checks_state === "pending") return "Required GitHub checks are still running.";
  if (prStatus.required_checks_state === "failed") return "A required GitHub check failed.";
  if (prStatus.mergeable !== true) return "GitHub does not currently report this pull request as mergeable.";
  return "";
}

function openActionDialog(mode, taskId, approval) {
  const titles = { workflow: "Approve coding workflow", deployment: "Approve production deployment", ready: "Mark pull request ready", revision: "Request a revision", merge: "Approve and merge" };
  state.pendingAction = { mode, taskId, approval };
  $("action-title").textContent = titles[mode];
  $("action-summary").textContent = mode === "workflow"
    ? `Workflow ${approval.id.slice(0, 8)} will start a coding task in ${approval.input.repository_id}. The agent may edit code, validate it, push a branch, and create a draft pull request.`
    : mode === "deployment"
      ? `Deploy commit ${shortSha(approval.input.commit_sha)} to ${approval.input.deployment_target_id}. The server will rebuild the registered service, verify its health, and roll back if it fails.`
    : `PR #${approval.number} at ${shortSha(approval.expected_head_sha)}. This action is explicitly authorized and recorded.`;
  const revision = mode === "revision";
  $("revision-label").hidden = !revision;
  $("revision-instructions").hidden = !revision;
  $("revision-instructions").required = revision;
  $("revision-instructions").value = "";
  $("approval-token").value = "";
  $("action-error").textContent = "";
  $("confirm-action").textContent = mode === "workflow" ? "Approve and start" : mode === "deployment" ? "Approve deployment" : mode === "merge" ? "Approve and merge" : mode === "revision" ? "Request revision" : "Mark ready";
  $("action-dialog").showModal();
  (revision ? $("revision-instructions") : $("approval-token")).focus();
}

function closeActionDialog() {
  state.pendingAction = null;
  $("action-dialog").close();
}

async function renderTask(id) {
  const taskChanged = state.taskId !== id;
  state.taskId = id;
  state.workflowId = null;
  $("detail-title").textContent = "Task";
  clearTimeout(state.taskTimer);
  state.taskTimer = null;
  if (taskChanged) state.prPollAttempts = 0;
  try {
    const [task, ctx, events, approval] = await Promise.all([
      api(`/tasks/${id}`), api(`/tasks/${id}/context`), api(`/tasks/${id}/events`),
      pendingApproval(id),
    ]);
    let prStatus = null;
    let prStatusError = null;
    if (approval) {
      try { prStatus = await pullRequestStatus(id); }
      catch (error) { prStatusError = error.message || String(error); }
    }
    let deploymentRun = await taskDeployment(id);
    if (deploymentRun?.status === "running") {
      deploymentRun = await api(`/workflow-runs/${deploymentRun.id}/sync`, {
        method: "POST", body: "{}",
      });
    }
    const deploymentEvents = deploymentRun
      ? (await api(`/workflow-runs/${deploymentRun.id}/events`)).events : [];
    // The route moved on while this loaded; don't reopen a closed or replaced drawer.
    if (state.taskId !== id) return;
    const ciReady = prStatus && prStatus.head_matches
      && prStatus.state === "open" && prStatus.required_checks_state === "passed"
      && prStatus.mergeable === true;
    const revisionReady = prStatus && prStatus.head_matches && prStatus.state === "open";
    const primaryGate = approval ? gateReason(prStatus, approval.draft ? "ready" : "merge") : "";
    const repository = task.source_context.repository_id || "No repository";
    const codingTask = task.required_capabilities.some((capability) =>
      ["coding", "pull_request_creation", "coding-pull-request"].includes(capability));
    const malformedRepositoryTask = task.status === "created"
      && task.source_context.repository_id && !codingTask;
    const codingQueueNotice = task.status === "created" && codingTask
      ? "Queued for a coding worker. For local testing, start the Compose coding profile if it is not already running."
      : "";
    const lineage = task.parent_task_id || task.superseded_by_task_id ? `<h2>Revision lineage</h2><div class="lineage">
      ${task.parent_task_id ? `<span>Previous</span><button type="button" class="link-button" data-task-nav="${esc(task.parent_task_id)}">${esc(task.parent_task_id.slice(0, 8))}</button>` : `<span>Original task</span>`}
      <span>→</span><strong>${esc(task.id.slice(0, 8))}</strong>
      ${task.superseded_by_task_id ? `<span>→ Active revision</span><button type="button" class="link-button" data-task-nav="${esc(task.superseded_by_task_id)}">${esc(task.superseded_by_task_id.slice(0, 8))}</button>` : ""}
    </div>` : "";
    $("task-detail").innerHTML = `
      <div class="row wrap">${statusPill(task.status)}
        ${task.required_capabilities.map((capability) => `<span class="status-pill">${esc(label(capability))}</span>`).join("")}</div>
      ${malformedRepositoryTask ? `<div class="gate-notice">This older task was created with a repository but without the coding capability, so no worker can safely run it. Cancel it and send the request again to use the coding workflow.</div>` : ""}
      ${codingQueueNotice ? `<div class="gate-notice">${esc(codingQueueNotice)}</div>` : ""}
      <h2 style="margin-top:12px">Request</h2><div>${esc(task.original_request)}</div>
      ${task.current_goal !== task.original_request ? `<div class="sub" style="margin-top:6px">Current goal: ${esc(task.current_goal)}</div>` : ""}
      <div class="summary-grid">
        <div class="summary-card"><div class="key">Repository</div><div class="value">${esc(repository)}</div></div>
        <div class="summary-card"><div class="key">Validation</div><div class="value">${statusPill(ctx.validation)}</div></div>
        <div class="summary-card"><div class="key">Task</div><div class="value">${esc(task.id.slice(0, 8))}</div></div>
        <div class="summary-card"><div class="key">Updated</div><div class="value">${new Date(task.updated_at).toLocaleString()}</div></div>
      </div>
      ${lineage}
      ${liveProgress(task, events.events)}
      ${codingWorkflow(ctx, task, prStatus)}
      ${pullRequestPanel(prStatus, approval)}
      ${deploymentPanel(task, ctx, deploymentRun, deploymentEvents)}
      ${prStatusError ? `<div class="gate-notice">Could not load live GitHub status: ${esc(prStatusError)}</div>` : ""}
      ${approval ? `<div id="gate-reason" class="gate-notice ${primaryGate ? "" : "pass"}">${primaryGate ? `Action blocked: ${esc(primaryGate)}` : "Exact commit and required checks are ready for an authorized action."}</div>` : ""}
      <div class="row wrap" style="margin-top:10px">
        ${approval && approval.draft ? `<button id="mark-ready" ${ciReady ? "" : "disabled"} title="${esc(primaryGate)}">Mark ready for review</button>` : ""}
        ${approval && !approval.draft ? `<button id="approve-merge" ${ciReady ? "" : "disabled"} title="${esc(primaryGate)}">Approve and merge</button>` : ""}
        ${approval && approval.head_branch ? `<button class="quiet" id="request-revision" ${revisionReady ? "" : "disabled"} title="${esc(gateReason(prStatus, "revision"))}">Request revision</button>` : ""}
        ${active(task) ? `<button class="quiet" id="cancel-task">Cancel</button>` : ""}
        <button class="ghost" id="discuss">Discuss in chat</button></div>
      ${ctx.final_answer ? `<h2>Result</h2><div class="panel-card">${esc(ctx.final_answer)}</div>` : ""}
      ${validationPanel(ctx)}
      <h2>Follow-up task</h2>
      <form id="followup-form"><textarea id="followup" rows="2"
        placeholder="Continue this work…"></textarea><button type="submit">Start</button></form>
      <details><summary>Curated context</summary>
        <div class="sub">Exactly what a follow-up prompt is given.</div>
        <pre>${esc(JSON.stringify(ctx, null, 2))}</pre></details>
      <details><summary>Audit events (${events.events.length})</summary>
        ${events.events.map((e) => `<div class="ev">${e.sequence}. ${esc(e.event_type)}</div>`).join("")}</details>`;
    openTaskPanel();
    const cancel = $("cancel-task");
    if (cancel) cancel.onclick = async () => {
      try { await api(`/tasks/${id}/cancel`, { method: "POST" }); await refreshTranscript(); }
      catch (e) { fail(e); }
    };
    const markReady = $("mark-ready");
    if (markReady) markReady.onclick = () => openActionDialog("ready", id, approval);
    const requestRevision = $("request-revision");
    if (requestRevision) requestRevision.onclick = () => openActionDialog("revision", id, approval);
    const approveMerge = $("approve-merge");
    if (approveMerge) approveMerge.onclick = () => openActionDialog("merge", id, approval);
    const refreshPrStatus = $("refresh-pr-status");
    if (refreshPrStatus) refreshPrStatus.onclick = () => {
      state.prPollAttempts = 0;
      renderTask(id);
    };
    const proposeDeployment = $("propose-deployment");
    if (proposeDeployment) proposeDeployment.onclick = async () => {
      try {
        await api(`/tasks/${id}/deployment-workflow`, {
          method: "POST",
          body: JSON.stringify({ deployment_target_id: $("deployment-target").value }),
        });
        await renderTask(id);
      } catch (error) { fail(error); }
    };
    const approveDeployment = $("approve-deployment");
    if (approveDeployment) approveDeployment.onclick = () =>
      openActionDialog("deployment", id, deploymentRun);
    const startDeployment = $("start-deployment");
    if (startDeployment) startDeployment.onclick = async () => {
      try {
        await api(`/workflow-runs/${deploymentRun.id}/start`, { method: "POST", body: "{}" });
        await renderTask(id);
      } catch (error) { fail(error); }
    };
    $("discuss").onclick = async () => {
      try {
        const chat = await api(`/tasks/${id}/chat-session`, { method: "POST" });
        goChat(chat.id);
      } catch (e) { fail(e); }
    };
    $("followup-form").onsubmit = async (ev) => {
      ev.preventDefault();
      const text = $("followup").value.trim();
      if (!text) return;
      try {
        const task = await api(`/tasks/${id}/follow-up`,
          { method: "POST", body: JSON.stringify({ request: text }) });
        $("followup").value = "";
        if (task.chat_session_id) go({ section: "chats", id: task.chat_session_id, task: task.id });
        else goTask(task.id);
      } catch (e) { fail(e); }
    };
    const unsettledPr = approval && task.status === "waiting_for_approval"
      && (!prStatus || ["missing", "pending"].includes(prStatus.required_checks_state)
        || prStatus.mergeable === null);
    if (unsettledPr && state.prPollAttempts < 24) {
      state.prPollAttempts += 1;
      state.taskTimer = setTimeout(() => {
        if (state.taskId === id) renderTask(id);
      }, 5000);
    }
    if (deploymentRun?.status === "running") {
      state.taskTimer = setTimeout(() => {
        if (state.taskId === id) renderTask(id);
      }, 5000);
    }
  } catch (e) { fail(e); }
}

"""The coding agents: Kimi Code (ACP session or one-shot), Claude Code on MiniMax, Codex,
and the fallback chain that tries them in order."""

from __future__ import annotations

import json
import subprocess
import tempfile
import time
from collections.abc import Callable, Sequence
from pathlib import Path

from pydantic import ValidationError

from app.coding.agent_session import (
    AcpCancelled,
    AcpClient,
    AcpError,
    AcpSession,
    AcpTimeout,
    GuidanceSource,
    ProgressCallback,
)
from app.coding.base import (
    CODE_AGENT_OUTPUT_SCHEMA,
    CodeAgentReport,
    CodeAgentRunner,
    CodingAgentError,
    RunnerAttempt,
    safe_agent_environment,
)
from app.coding.decision import CodingDecision, CodingDecisionEngine
from app.coding.git import restore_clean_worktree
from app.coding.process import (
    WorktreeWatcher,
    failure_category,
    optional,
    read_diagnostic,
    run_code_agent_process,
    wait_or_kill,
    with_redirects,
)
from app.coding.reports import coding_prompt, text_report
from app.execution.fake import ExecutionCancelled
from app.providers import InMemoryProviderStateStore, ProviderStateStore


class CodexCliRunner:
    """Runs Codex non-interactively with workspace-only write access."""

    provider = "codex-cli"

    def __init__(self, *, executable: str = "codex", model: str | None = None):
        self.executable = executable
        self.model = model

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
        on_progress: ProgressCallback | None = None,
        next_guidance: GuidanceSource | None = None,
    ) -> CodeAgentReport:
        with tempfile.TemporaryDirectory(prefix="assistant-codex-") as temporary:
            temporary_path = Path(temporary)
            schema_path = temporary_path / "output-schema.json"
            output_path = temporary_path / "last-message.json"
            stdout_path = temporary_path / "stdout.jsonl"
            stderr_path = temporary_path / "stderr.log"
            schema_path.write_text(json.dumps(CODE_AGENT_OUTPUT_SCHEMA), encoding="utf-8")
            command = [
                self.executable,
                "exec",
                "--ephemeral",
                "--ignore-user-config",
                "--sandbox",
                "workspace-write",
                "--color",
                "never",
                "--json",
                "--output-schema",
                str(schema_path),
                "--output-last-message",
                str(output_path),
                "--cd",
                str(worktree),
            ]
            if self.model:
                command.extend(["--model", self.model])
            command.append("-")
            prompt = coding_prompt(request)
            try:
                with stdout_path.open("wb") as stdout, stderr_path.open("wb") as stderr:
                    process = subprocess.Popen(
                        command,
                        stdin=subprocess.PIPE,
                        stdout=stdout,
                        stderr=stderr,
                        env=safe_agent_environment(),
                    )
                    assert process.stdin is not None
                    process.stdin.write(prompt.encode())
                    process.stdin.close()
                    deadline = time.monotonic() + timeout_seconds
                    watcher = WorktreeWatcher(worktree, on_progress)
                    while process.poll() is None:
                        if is_cancelled():
                            process.terminate()
                            wait_or_kill(process)
                            raise ExecutionCancelled("Coding task was cancelled")
                        if time.monotonic() >= deadline:
                            process.terminate()
                            wait_or_kill(process)
                            raise CodingAgentError("Coding agent timed out")
                        watcher.poll()
                        time.sleep(0.2)
            except FileNotFoundError as error:
                raise CodingAgentError("Codex CLI executable was not found") from error
            if process.returncode != 0:
                diagnostic = read_diagnostic(stderr_path, stdout_path)
                raise CodingAgentError(
                    f"Codex CLI exited with status {process.returncode}",
                    category=failure_category(diagnostic),
                )
            try:
                return CodeAgentReport.model_validate_json(output_path.read_text(encoding="utf-8"))
            except (OSError, ValidationError) as error:
                raise CodingAgentError("Codex CLI returned an invalid completion report") from error


class KimiCodeCliRunner:
    """Runs Kimi Code in its documented non-interactive auto-approval mode."""

    provider = "kimi-code"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        executable: str = "kimi",
        model: str = "kimi-for-coding",
    ):
        if not api_key:
            raise ValueError("A Kimi API key is required for Kimi Code")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.executable = executable
        self.model = model

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
        on_progress: ProgressCallback | None = None,
        next_guidance: GuidanceSource | None = None,
    ) -> CodeAgentReport:
        def command(notes: Sequence[str] = ()) -> list[str]:
            prompt = coding_prompt(with_redirects(request, notes))
            return [self.executable, "--prompt", prompt, "--output-format", "text"]

        output = run_code_agent_process(
            command=command(),
            worktree=worktree,
            timeout_seconds=timeout_seconds,
            is_cancelled=is_cancelled,
            environment=self._environment(),
            on_progress=on_progress,
            display_name="Kimi Code CLI",
            next_guidance=next_guidance,
            redirected_command=command,
        )
        return text_report(output, "Kimi Code completed the requested repository change")

    def _environment(self) -> dict[str, str]:
        # Kimi Code only runs a model it has configured. With no config.toml in the
        # agent's throwaway home, the KIMI_MODEL_* variables define the default model.
        environment = safe_agent_environment()
        environment.update(
            {
                "KIMI_MODEL_NAME": self.model,
                "KIMI_MODEL_API_KEY": self.api_key,
                "KIMI_MODEL_BASE_URL": self.base_url,
            }
        )
        return environment


class KimiAcpRunner(KimiCodeCliRunner):
    """Runs Kimi Code as a live ACP session, so its plan and steps stream back."""

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
        on_progress: ProgressCallback | None = None,
        next_guidance: GuidanceSource | None = None,
    ) -> CodeAgentReport:
        try:
            client = AcpClient(
                [self.executable, "acp"], cwd=worktree, environment=self._environment()
            )
        except FileNotFoundError as error:
            raise CodingAgentError(
                "Kimi Code executable was not found", category="unavailable"
            ) from error
        try:
            session = AcpSession(client, cwd=worktree, on_progress=on_progress)
            session.open()
            stop_reason, reply = session.prompt(
                coding_prompt(request),
                deadline=time.monotonic() + timeout_seconds,
                is_cancelled=is_cancelled,
                next_guidance=next_guidance,
            )
        except AcpCancelled as error:
            raise ExecutionCancelled("Coding task was cancelled") from error
        except AcpTimeout as error:
            raise CodingAgentError("Kimi Code timed out", category="timeout") from error
        except AcpError as error:
            raise CodingAgentError(
                f"Kimi Code session failed: {error}",
                category=failure_category(f"{error}\n{error.diagnostic}"),
            ) from error
        finally:
            client.close()
        if stop_reason == "refusal":
            raise CodingAgentError("Kimi Code refused the request", category="refused")
        return text_report(reply, "Kimi Code completed the requested repository change")


class ClaudeCodeMiniMaxRunner:
    """Runs Claude Code against MiniMax's Anthropic-compatible endpoint."""

    provider = "minimax-claude-code"

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        executable: str = "claude",
        model: str | None = None,
    ):
        if not api_key:
            raise ValueError("A MiniMax API key is required for Claude Code")
        self.api_key = api_key
        self.base_url = base_url.rstrip("/")
        self.executable = executable
        self.model = model

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
        on_progress: ProgressCallback | None = None,
        next_guidance: GuidanceSource | None = None,
    ) -> CodeAgentReport:
        command = [
            self.executable,
            "--print",
            "--dangerously-skip-permissions",
            "--output-format",
            "json",
        ]
        if self.model:
            command.extend(["--model", self.model])

        def with_prompt(notes: Sequence[str] = ()) -> list[str]:
            return [*command, coding_prompt(with_redirects(request, notes))]

        environment = safe_agent_environment()
        environment.update(
            {
                "ANTHROPIC_AUTH_TOKEN": self.api_key,
                "ANTHROPIC_BASE_URL": self.base_url,
            }
        )
        output = run_code_agent_process(
            command=with_prompt(),
            worktree=worktree,
            timeout_seconds=timeout_seconds,
            is_cancelled=is_cancelled,
            environment=environment,
            on_progress=on_progress,
            display_name="Claude Code with MiniMax",
            next_guidance=next_guidance,
            redirected_command=with_prompt,
        )
        try:
            body = json.loads(output)
            result = body.get("result") if isinstance(body, dict) else None
        except ValueError:
            result = None
        return text_report(
            result if isinstance(result, str) else output,
            "Claude Code with MiniMax completed the requested repository change",
        )


class FallbackCodeAgentRunner:
    """Tries coding runners in order, cooling down limited providers between tasks."""

    def __init__(
        self,
        runners: Sequence[CodeAgentRunner],
        *,
        rate_limit_cooldown_seconds: int = 300,
        quota_cooldown_seconds: int = 3600,
        state_store: ProviderStateStore | None = None,
        decision_engine: CodingDecisionEngine | None = None,
    ):
        if not runners:
            raise ValueError("At least one coding runner is required")
        self.runners = tuple(runners)
        self.rate_limit_cooldown_seconds = rate_limit_cooldown_seconds
        self.quota_cooldown_seconds = quota_cooldown_seconds
        self.state_store = state_store or InMemoryProviderStateStore()
        self.decision_engine = decision_engine
        self.last_decision: CodingDecision | None = None
        self.attempts: tuple[RunnerAttempt, ...] = ()
        self.provider = "->".join(runner.provider for runner in runners)

    def run(
        self,
        *,
        worktree: Path,
        request: str,
        timeout_seconds: int,
        is_cancelled: Callable[[], bool],
        on_progress: ProgressCallback | None = None,
        next_guidance: GuidanceSource | None = None,
    ) -> CodeAgentReport:
        deadline = time.monotonic() + timeout_seconds
        attempts: list[RunnerAttempt] = []
        failures: list[str] = []
        ordered_runners = self.runners
        if self.decision_engine is not None:
            self.last_decision = self.decision_engine.decide(self.runners, request)
            by_provider = {runner.provider: runner for runner in self.runners}
            ordered_runners = tuple(
                by_provider[provider] for provider in self.last_decision.ordered_providers
            )
        for runner in ordered_runners:
            provider_key = f"coding:{runner.provider}"
            if not self.state_store.is_available(provider_key):
                attempts.append(RunnerAttempt(runner.provider, "skipped", "cooldown"))
                continue
            remaining = int(deadline - time.monotonic())
            if remaining <= 0:
                break
            try:
                if on_progress is not None:
                    on_progress(f"Coding agent started ({runner.provider})", "milestone")
                report = runner.run(
                    worktree=worktree,
                    request=request,
                    timeout_seconds=remaining,
                    is_cancelled=is_cancelled,
                    **optional(on_progress=on_progress, next_guidance=next_guidance),
                )
            except ExecutionCancelled:
                raise
            except CodingAgentError as error:
                attempts.append(RunnerAttempt(runner.provider, "failed", error.category))
                failures.append(f"{runner.provider}: {error.category}")
                if error.category in {"rate_limited", "quota_exhausted"}:
                    cooldown = (
                        self.quota_cooldown_seconds
                        if error.category == "quota_exhausted"
                        else self.rate_limit_cooldown_seconds
                    )
                else:
                    cooldown = 0
                self.state_store.record_failure(
                    provider_key,
                    category=error.category,
                    cooldown_seconds=cooldown,
                )
                restore_clean_worktree(worktree)
                continue
            attempts.append(RunnerAttempt(runner.provider, "succeeded"))
            self.state_store.record_success(provider_key)
            self.attempts = tuple(attempts)
            self.provider = runner.provider
            report.notes.append(
                "Runner attempts: "
                + ", ".join(
                    f"{attempt.provider}={attempt.outcome}"
                    + (f"({attempt.category})" if attempt.category else "")
                    for attempt in attempts
                )
            )
            if self.last_decision is not None:
                report.notes.append(
                    "Decision engine: "
                    f"complexity={self.last_decision.profile.complexity.value}; "
                    "order=" + " -> ".join(self.last_decision.ordered_providers)
                )
            return report
        self.attempts = tuple(attempts)
        detail = ", ".join(failures) or "all providers are cooling down"
        raise CodingAgentError(f"Coding runner chain exhausted: {detail}")

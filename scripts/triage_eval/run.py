"""Score a chat-routing (triage) model on labelled messages: accuracy and latency.

Routing runs before every chat reply, so a candidate model must be both right and fast.
This runs the app's own Triager (same prompt, parsing and rules as production) over
cases.json, in English and Swedish, and prints each miss and a summary.

    python scripts/triage_eval/run.py kimi
    python scripts/triage_eval/run.py minimax --model MiniMax-M3
    python scripts/triage_eval/run.py openai --base-url https://api.deepseek.com/v1 \\
        --model deepseek-v4.1-flash --api-key-env DEEPSEEK_API_KEY

Keys come from the environment (KIMI_API_KEY, MINIMAX_API_KEY, or --api-key-env). On the
server, copy this folder to /service/scripts/triage_eval in the api container and run it there.

Results so far (2026-10-05, 45 cases): Kimi 45/45, median 1.9 s, p90 2.8 s; MiniMax-M3
45/45, median 4.0 s, p90 12 s; Gemini Nano on a Pixel 10 Pro XL 8-15 s per message.
"""

from __future__ import annotations

import argparse
import json
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))

from app.chat.triage import TriageDecision, TriageIntent, Triager  # noqa: E402
from app.integrations.chat import ChatClient  # noqa: E402
from app.repositories import RepositoryRegistry  # noqa: E402

CASES = Path(__file__).with_name("cases.json")
WORK_ITEMS = [
    {"slug": "groceries", "title": "Groceries", "kind": "list"},
    {"slug": "copenhagen-trip", "title": "Copenhagen trip", "kind": "goal"},
]
CLOCK = {"local_time": "2026-10-05T18:00", "timezone": "Europe/Stockholm"}


def route(triager: Triager, message: str) -> TriageDecision:
    """The model's decision with the production rules applied, as Triager.decide does.

    Called step by step rather than through decide(), which would fall back to keyword
    rules on a model failure and hide it.
    """
    suggestion = triager._ask_model(
        message, selected=None, focused=[], recent=[], work_items=WORK_ITEMS, clock=CLOCK
    )
    decision = triager._apply_rules(suggestion, message, selected=None)
    if suggestion.about_work and decision.intent != TriageIntent.DIRECT_ACTION:
        decision = decision.model_copy(update={"about_work": True})
    return triager._groceries(decision, message, bool(suggestion.needs_grocery_tools))


def is_correct(case: dict, decision: TriageDecision) -> bool:
    """Whether the decision sends the message where it belongs."""
    intent = decision.intent.value
    groceries = "groceries" in decision.capabilities
    if case.get("groceries"):
        return groceries
    if case.get("about_work") and case["intent"] == "answer":
        return decision.about_work and decision.intent != TriageIntent.DIRECT_ACTION
    if case["intent"] == "propose_coding":
        return intent == "propose_coding" or decision.about_work
    if case["intent"] == "direct_action":
        return decision.action is not None and decision.action.type == case["action"]
    return intent == case["intent"] and not groceries and not decision.about_work


def evaluate(client: ChatClient, cases: list[dict]) -> dict:
    triager = Triager(
        RepositoryRegistry.from_directory(ROOT / "repositories"), client, groceries_available=True
    )
    correct, invalid, times = 0, 0, []
    for case in cases:
        started = time.monotonic()
        try:
            decision = route(triager, case["m"])
            ok = is_correct(case, decision)
            got = f"{decision.intent.value}{'/' + decision.action.type if decision.action else ''}"
        except Exception as error:  # invalid output after retries, timeout, HTTP error
            invalid += 1
            ok, got = False, f"error: {type(error).__name__}: {str(error)[:80]}"
        times.append(time.monotonic() - started)
        correct += ok
        if not ok:
            want = case["intent"] + (f"/{case['action']}" if case.get("action") else "")
            print(f"  miss {times[-1]:5.1f}s {case['m'][:60]!r}: want {want}, got {got}")
    return {
        "accuracy": f"{correct}/{len(cases)}",
        "invalid_or_failed": invalid,
        "median_s": round(statistics.median(times), 2),
        "p90_s": round(sorted(times)[int(len(times) * 0.9)], 2),
        "max_s": round(max(times), 2),
    }


def client_for(args: argparse.Namespace) -> ChatClient:
    from app.integrations.kimi import KimiChatClient
    from app.integrations.minimax import MiniMaxChatClient

    if args.provider == "kimi":
        return KimiChatClient(
            api_key=os.environ["KIMI_API_KEY"],
            model=args.model or os.environ.get("ASSISTANT_KIMI_MODEL", "kimi-for-coding-highspeed"),
            timeout_seconds=args.timeout,
        )
    if args.provider == "minimax":
        return MiniMaxChatClient(
            api_key=os.environ["MINIMAX_API_KEY"],
            model=args.model or "MiniMax-M3",
            timeout_seconds=args.timeout,
        )
    # Any OpenAI-compatible chat API (DeepSeek, a local llama.cpp or Ollama server, ...).
    if not (args.base_url and args.model):
        raise SystemExit("openai needs --base-url and --model")
    return KimiChatClient(
        api_key=os.environ.get(args.api_key_env or "", "") or "none",
        base_url=args.base_url,
        model=args.model,
        timeout_seconds=args.timeout,
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("provider", choices=["kimi", "minimax", "openai"])
    parser.add_argument("--model")
    parser.add_argument("--base-url")
    parser.add_argument("--api-key-env", help="environment variable holding the API key")
    parser.add_argument("--timeout", type=float, default=60)
    args = parser.parse_args()
    summary = evaluate(client_for(args), json.loads(CASES.read_text()))
    print(json.dumps({"provider": args.provider, "model": args.model, **summary}))


if __name__ == "__main__":
    main()

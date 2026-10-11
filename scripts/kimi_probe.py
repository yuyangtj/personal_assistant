"""Check what a Kimi endpoint offers the main chat agent: model ids, native tool calls, speed.

The main agent calls tools natively (OpenAI-style ``tools`` / ``tool_calls``), so before
building on a model we check that the endpoint really supports it, including several calls
in one reply and a tool result going back in, and how long a turn takes.

    python scripts/kimi_probe.py                 # list the models the key can use
    python scripts/kimi_probe.py --model <id>    # probe that model

The key comes from ASSISTANT_KIMI_API_KEY (or KIMI_API_KEY) and is never printed. On the
server, run it in the api container, where those variables are already set.

Results (2026-10-11, coding endpoint): models kimi-for-coding, kimi-for-coding-highspeed, k3,
k3-256k. k3 and k3-256k both make native tool calls, two in one reply, and use the results
correctly. Every call takes about 4-5.5 s, even "say hi", because the model reasons first
(50-100 reasoning tokens). The prompt is cached (cached_tokens). So a chat reply takes about
5 s and a reply with one round of tools about 9-10 s.
"""

from __future__ import annotations

import argparse
import contextlib
import json
import os
import sys
import time

import httpx

TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "klarna_spending",
            "description": "Total Klarna spending, optionally grouped by week, month or merchant.",
            "parameters": {
                "type": "object",
                "properties": {
                    "group_by": {"type": "string", "enum": ["week", "month", "merchant"]},
                    "from": {"type": "string", "description": "First day, YYYY-MM-DD"},
                },
                "required": [],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "add_reminder",
            "description": "Remind the user at a local time.",
            "parameters": {
                "type": "object",
                "properties": {"text": {"type": "string"}, "at": {"type": "string"}},
                "required": ["text", "at"],
            },
        },
    },
]
SYSTEM = "You are a personal assistant. Use the tools when they help. Today is 2026-10-11."


def _post(client: httpx.Client, body: dict) -> tuple[dict, float]:
    started = time.monotonic()
    response = client.post("/chat/completions", json=body)
    elapsed = time.monotonic() - started
    if response.status_code != 200:
        # The body can echo the request; print only the status and the error's type.
        detail = ""
        with contextlib.suppress(ValueError):
            detail = str(response.json().get("error", {}).get("type", ""))
        raise SystemExit(f"HTTP {response.status_code} {detail}".strip())
    return response.json(), elapsed


def _calls(message: dict) -> list[dict]:
    return [
        {"name": call["function"]["name"], "arguments": call["function"]["arguments"]}
        for call in message.get("tool_calls") or []
    ]


def probe(client: httpx.Client, model: str) -> None:
    plain, seconds = _post(
        client,
        {
            "model": model,
            "max_tokens": 200,
            "messages": [
                {"role": "system", "content": SYSTEM},
                {"role": "user", "content": "Say hi in five words."},
            ],
        },
    )
    print(f"plain reply: {seconds:.1f} s, {plain.get('usage', {})}")

    question = "Show my Klarna spending by week since June, and remind me tomorrow 9:00 to pay."
    messages = [{"role": "system", "content": SYSTEM}, {"role": "user", "content": question}]
    first, seconds = _post(
        client, {"model": model, "max_tokens": 800, "messages": messages, "tools": TOOLS}
    )
    message = first["choices"][0]["message"]
    calls = _calls(message)
    print(f"tool turn: {seconds:.1f} s, finish={first['choices'][0].get('finish_reason')}")
    print(f"  tool calls ({len(calls)}): {json.dumps(calls, ensure_ascii=False)}")
    if not calls:
        print("  NO native tool calls; text was:", (message.get("content") or "")[:300])
        return

    # Send every result back, as the agent will.
    messages.append(message)
    for call in message["tool_calls"]:
        result = (
            {"groups": [{"group": "2026-09-07", "total": "1200.00"}], "currency": "SEK"}
            if call["function"]["name"] == "klarna_spending"
            else {"ok": True}
        )
        messages.append({"role": "tool", "tool_call_id": call["id"], "content": json.dumps(result)})
    final, seconds = _post(
        client, {"model": model, "max_tokens": 800, "messages": messages, "tools": TOOLS}
    )
    answer = final["choices"][0]["message"]
    print(f"after results: {seconds:.1f} s, more calls={len(_calls(answer))}")
    print("  answer:", (answer.get("content") or "")[:400])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--model", help="model id to probe; omit to list the models")
    args = parser.parse_args()
    key = os.getenv("ASSISTANT_KIMI_API_KEY") or os.getenv("KIMI_API_KEY")
    if not key:
        sys.exit("Set ASSISTANT_KIMI_API_KEY (or KIMI_API_KEY).")
    base_url = os.getenv("ASSISTANT_KIMI_BASE_URL", "https://api.kimi.com/coding/v1")
    with httpx.Client(
        base_url=base_url.rstrip("/"), headers={"Authorization": f"Bearer {key}"}, timeout=60
    ) as client:
        if not args.model:
            response = client.get("/models")
            if response.status_code != 200:
                sys.exit(f"GET /models returned HTTP {response.status_code}")
            for item in response.json().get("data", []):
                print(item.get("id"))
            return
        probe(client, args.model)


if __name__ == "__main__":
    main()

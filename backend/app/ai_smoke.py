"""One-request live smoke test for the configured AI Gateway.

Run inside the API container:
    python -m app.ai_smoke --task briefing

This makes one billable provider request and never prints credentials.
"""
from __future__ import annotations

import argparse
import json
import sys
import time

from .ai_gateway import ProviderError, configured_provider


TASKS = (
    "project_proposal",
    "project_clarification",
    "briefing",
    "scenario_event",
    "knowledge_entity",
)

SYSTEM_PROMPT = (
    "You are checking an AI provider connection. Return one JSON object only, "
    'with exactly these keys: "status" (string, must be "ok") and '
    '"message" (short string in Russian). Do not add markdown or other keys.'
)


def run_smoke_test(task: str) -> dict:
    """Call the selected task route and validate a minimal structured response."""
    provider = configured_provider(task)
    result = provider.generate_json(
        SYSTEM_PROMPT,
        {
            "purpose": "Проверка реального соединения AI Gateway",
            "instruction": 'Верни status="ok" и короткое сообщение "Связь работает".',
        },
    )
    if result.get("status") != "ok" or not isinstance(result.get("message"), str):
        raise ProviderError("AI provider responded, but did not satisfy the smoke-test JSON contract.")
    return {"status": "ok", "message": result["message"][:300]}


def main() -> int:
    parser = argparse.ArgumentParser(description="Make one live AI Gateway smoke-test request.")
    parser.add_argument("--task", choices=TASKS, default="briefing")
    args = parser.parse_args()

    started = time.monotonic()
    try:
        result = run_smoke_test(args.task)
    except ProviderError as error:
        print(f"AI_PROVIDER_SMOKE_FAILED: {error}", file=sys.stderr)
        return 1
    except Exception:
        print("AI_PROVIDER_SMOKE_FAILED: unexpected local error; inspect API container logs.", file=sys.stderr)
        return 2

    elapsed = time.monotonic() - started
    print(json.dumps({
        "result": "AI_PROVIDER_SMOKE_OK",
        "task": args.task,
        "elapsed_seconds": round(elapsed, 2),
        "message": result["message"],
    }, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

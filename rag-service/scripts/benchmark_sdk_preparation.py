"""Compare local SDK preparation in fresh processes without model requests."""

import argparse
from contextlib import chdir
from datetime import datetime, timezone
from importlib import import_module, metadata
import json
import os
from pathlib import Path
import statistics
import subprocess
import sys
from tempfile import TemporaryDirectory
from time import perf_counter


SERVICE_ROOT = Path(__file__).resolve().parents[1]


def sample(mode: str, provider: str) -> dict:
    network_attempts = []

    def deny_network(event, _args):
        if event in {"socket.connect", "socket.getaddrinfo", "socket.sendto"}:
            network_attempts.append(event)
            raise AssertionError("offline SDK benchmark attempted network access")

    sys.addaudithook(deny_network)
    for key in tuple(os.environ):
        if key.startswith(("LLM_", "OPENAI_", "DEEPSEEK_", "LANGCHAIN_", "LANGSMITH_")):
            del os.environ[key]
    os.environ.update(LANGSMITH_TRACING="false", LANGCHAIN_TRACING_V2="false")
    sys.path.insert(0, str(SERVICE_ROOT))

    def timed(operation):
        started = perf_counter()
        operation()
        return round((perf_counter() - started) * 1000, 3)

    with TemporaryDirectory(prefix="easyrag-sdk-benchmark-") as directory, chdir(directory):
        # Load the normal composition module without constructing A/B or touching data.
        application_import_ms = timed(lambda: import_module("app.application.runtime"))
        from app.modules.answer_models.public import Models

        models = Models(config_path=Path(directory) / "llm.json")
        prepare_ms = None
        if mode == "prepared":
            prepare_ms = timed(models.prepare)
            assert "openai.resources.chat" in sys.modules
            assert "langchain_core.messages.human" in sys.modules

        # Preparation above runs with no model configuration at all.
        os.environ.update(
            LLM_PROVIDER=provider, LLM_MODEL="offline-benchmark-model",
            LLM_API_KEY="offline-benchmark-key", LLM_BASE_URL="http://127.0.0.1:9/v1",
        )
        first_session_ms = timed(models.open_session)
        warm_session_ms = [timed(models.open_session) for _ in range(4)]
        assert not network_attempts
    return {
        "mode": mode, "provider": provider, "application_import_ms": application_import_ms,
        "prepare_ms": prepare_ms, "first_session_ms": first_session_ms,
        "warm_session_ms": warm_session_ms, "network_attempts": len(network_attempts),
    }


def distribution(values):
    return {
        "median": round(statistics.median(values), 3),
        "min": min(values), "max": max(values),
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--samples", type=int, default=3, help="fresh process pairs per provider")
    parser.add_argument("--output", type=Path, help="optional JSON file including raw measurements")
    parser.add_argument("--child", choices=("cold", "prepared"), help=argparse.SUPPRESS)
    parser.add_argument("--provider", choices=("openai", "deepseek"), help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.samples < 1:
        parser.error("--samples must be positive")
    if args.child:
        if not args.provider:
            parser.error("--child requires --provider")
        print(json.dumps(sample(args.child, args.provider)))
        return

    samples = []
    for number in range(args.samples):
        for provider in ("openai", "deepseek"):
            # Alternate order to avoid always measuring preparation second.
            modes = ("cold", "prepared") if number % 2 == 0 else ("prepared", "cold")
            for mode in modes:
                completed = subprocess.run(
                    [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                     "--child", mode, "--provider", provider],
                    capture_output=True, text=True, encoding="utf-8", check=True,
                )
                row = json.loads(completed.stdout)
                samples.append(row)
                print(f"sample {number + 1}/{args.samples} {provider} {mode}: "
                      f"prepare={row['prepare_ms']} first_session={row['first_session_ms']} ms", flush=True)

    summary = []
    for provider in ("openai", "deepseek"):
        for mode in ("cold", "prepared"):
            rows = [row for row in samples if row["provider"] == provider and row["mode"] == mode]
            summary.append({
                "provider": provider, "mode": mode, "samples": len(rows),
                "application_import_ms": distribution([row["application_import_ms"] for row in rows]),
                "prepare_ms": distribution([row["prepare_ms"] for row in rows]) if mode == "prepared" else None,
                "first_session_ms": distribution([row["first_session_ms"] for row in rows]),
                "warm_session_ms": distribution([value for row in rows for value in row["warm_session_ms"]]),
            })
    record = {
        "time_utc": datetime.now(timezone.utc).isoformat(),
        "python": sys.version.split()[0], "platform": sys.platform,
        "packages": {name: metadata.version(name) for name in ("langchain", "langchain-openai", "langchain-deepseek", "openai")},
        "network_attempts": sum(row["network_attempts"] for row in samples),
        "samples": samples, "summary": summary,
    }
    if args.output:
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(record, indent=2) + "\n", encoding="utf-8")
    print(json.dumps({"network_attempts": record["network_attempts"], "summary": summary}, indent=2))


if __name__ == "__main__":
    main()

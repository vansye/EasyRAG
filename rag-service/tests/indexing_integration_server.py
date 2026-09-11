"""仅供 Java 联调使用的独立 Python 进程；生产入口不会加载这些测试路由。"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
import re
import socket
import sys
from time import perf_counter_ns
from typing import Literal

import httpx
from fastapi import Request, Response
from pydantic import BaseModel
import uvicorn


SERVICE_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SERVICE_DIR))


class ProviderControl(BaseModel):
    mode: Literal["normal", "fail", "hold", "release", "fail-shutdown"]


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--run-dir", type=Path, required=True)
    arguments = parser.parse_args()
    run_dir = arguments.run_dir.resolve(strict=True)
    target_dir = (SERVICE_DIR.parent / "server/target").resolve(strict=True)
    if run_dir.parent != target_dir or not re.fullmatch(r"indexing-it-[0-9a-f]{32}", run_dir.name):
        raise ValueError("integration data must stay in its unique server/target directory")
    if any(run_dir.iterdir()):
        raise ValueError("integration data directory must be new and empty")

    for variable in list(os.environ):
        if variable.lower().endswith("_proxy"):
            del os.environ[variable]

    from app import config

    config.DATA_DIR = run_dir
    from app import main as service

    listener = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    settings = config.Settings(
        _env_file=None,
        embedding_provider="ollama",
        embedding_model="bge-m3",
        embedding_dim=1024,
        embedding_base_url=f"http://127.0.0.1:{port}/__it__/provider",
        embedding_api_key=None,
        embedding_timeout_seconds=60.0,
        chunk_max_tokens=512,
        chunk_min_tokens=64,
        chunk_tokenizer_path=SERVICE_DIR / "data/tokenizers/bge-m3-5617a9f61b028005a4858fdac845db406aefb181.json",
        embed_batch_size=64,
    )
    if not settings.chunk_tokenizer_path.is_file():
        raise FileNotFoundError("integration test requires the local bge-m3 tokenizer")
    service.get_settings = lambda: settings
    release_provider = asyncio.Event()
    state = {"mode": "normal", "held_calls": 0, "active_provider_calls": 0,
             "requests": [], "provider_requests": [], "model_metrics": []}

    @service.app.middleware("http")
    async def observe_pipeline(request: Request, call_next):
        path = request.url.path
        observed = path in {"/chunk", "/embed", "/reset"} or path.startswith("/index/")
        event = None
        if observed:
            event = {"method": request.method, "path": path, "status": None}
            state["requests"].append(event)
        response = await call_next(request)
        if event is not None:
            event["status"] = response.status_code
        return response

    @service.app.get("/__it__/state")
    def inspect_state():
        return {**state, "run_id": run_dir.name, "pid": os.getpid(),
                "chroma_dir": str(settings.chroma_dir.resolve()),
                "chroma": service.app.state.index.probe()}

    @service.app.get("/__it__/index/{document_id}")
    def inspect_document(document_id: int):
        result = service.app.state.index._collection.get(
            where={"document_id": document_id}, include=["documents", "metadatas", "embeddings"],
        )
        return {"chunks": [
            {"id": chunk_id, "document": result["documents"][position],
             "metadata": result["metadatas"][position],
             "embedding": result["embeddings"][position].tolist()}
            for position, chunk_id in enumerate(result["ids"])
        ]}

    @service.app.post("/__it__/control")
    async def control_provider(payload: ProviderControl):
        if payload.mode == "release":
            release_provider.set()
            state["mode"] = "normal"
        else:
            if payload.mode == "hold":
                release_provider.clear()
            state["mode"] = payload.mode
        return {"mode": state["mode"]}

    @service.app.get("/__it__/provider/api/tags")
    async def model_list():
        async with httpx.AsyncClient(timeout=3.0, trust_env=False) as client:
            response = await client.get("http://127.0.0.1:11434/api/tags")
        return Response(response.content, status_code=response.status_code, media_type="application/json")

    @service.app.post("/__it__/provider/api/embed")
    async def model_embeddings(payload: dict):
        state["provider_requests"].append(payload)
        state["active_provider_calls"] += 1
        try:
            mode = state["mode"]
            if mode == "fail":
                return Response("injected-provider-private-detail", status_code=503)
            if mode == "hold":
                state["held_calls"] += 1
                try:
                    await asyncio.wait_for(release_provider.wait(), timeout=40.0)
                finally:
                    state["held_calls"] -= 1
            started = perf_counter_ns()
            async with httpx.AsyncClient(timeout=60.0, trust_env=False) as client:
                response = await client.post("http://127.0.0.1:11434/api/embed", json=payload)
            elapsed = perf_counter_ns() - started
            if response.is_success:
                body = response.json()
                state["model_metrics"].append({
                    "model": payload["model"], "inputs": len(payload["input"]),
                    "http_duration_ns": elapsed, "total_duration_ns": body.get("total_duration"),
                    "load_duration_ns": body.get("load_duration"),
                    "prompt_eval_count": body.get("prompt_eval_count"),
                })
            return Response(response.content, status_code=response.status_code, media_type="application/json")
        finally:
            state["active_provider_calls"] -= 1

    server = uvicorn.Server(uvicorn.Config(service.app, host="127.0.0.1", port=port,
                                         log_level="warning", access_log=False))

    @service.app.post("/__it__/shutdown")
    async def shutdown():
        if state["mode"] == "fail-shutdown":
            return Response("injected shutdown failure", status_code=503)
        release_provider.set()
        server.should_exit = True
        return {"stopping": True}

    ready = run_dir / "ready.json"
    temporary_ready = run_dir / "ready.tmp"
    temporary_ready.write_text(json.dumps({"port": port, "pid": os.getpid()}), encoding="utf-8")
    temporary_ready.replace(ready)
    try:
        server.run(sockets=[listener])
    finally:
        listener.close()


if __name__ == "__main__":
    main()

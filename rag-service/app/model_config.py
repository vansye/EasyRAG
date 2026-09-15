"""Local answer-model settings. Credentials are write-only at the HTTP boundary."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Literal

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import AnyHttpUrl, BaseModel, ConfigDict, Field, SecretStr, ValidationError, field_validator
from starlette.concurrency import run_in_threadpool

from app.config import LlmSettings, SERVICE_DIR

router = APIRouter()
CONFIG_PATH = SERVICE_DIR / "config" / "llm.json"
DEFAULT_URLS = {"openai": "https://api.openai.com/v1", "deepseek": "https://api.deepseek.com/v1"}
_lock = RLock()


class ModelConfigUnavailable(RuntimeError):
    """The saved settings could not be read; no credentials in the error text."""


class ModelConfigUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, hide_input_in_errors=True)

    provider: Literal["openai", "deepseek"]
    model: str = Field(min_length=1, max_length=200, strict=True)
    base_url: AnyHttpUrl = Field(max_length=2048)
    api_key: SecretStr | None = Field(default=None, max_length=4096)

    @field_validator("base_url")
    @classmethod
    def address_has_no_credentials(cls, value: AnyHttpUrl) -> AnyHttpUrl:
        if value.username or value.password or value.query or value.fragment:
            raise ValueError("use the separate API key field")
        return value


def _config_path() -> Path:
    return Path(os.environ.get("LLM_CONFIG_FILE", CONFIG_PATH))


def load_llm_settings() -> LlmSettings:
    """Read one complete snapshot; an existing model instance keeps its settings."""
    with _lock:
        try:
            raw = _config_path().read_text(encoding="utf-8")
        except FileNotFoundError:
            return LlmSettings()
        except OSError as failure:
            raise ModelConfigUnavailable("saved model settings are unavailable") from failure
        try:
            data = json.loads(raw)
            if not isinstance(data, dict):
                raise ValueError("expected settings object")
            return LlmSettings(**data)
        except (ValueError, TypeError) as failure:
            raise ModelConfigUnavailable("saved model settings are invalid") from failure


def resolve_llm_base_url(settings: LlmSettings) -> str:
    """Use the SDK's endpoint precedence for both display and actual requests."""
    address = settings.base_url
    if address is None:
        if settings.provider == "deepseek":
            address = os.getenv("DEEPSEEK_API_BASE", DEFAULT_URLS["deepseek"])
        else:
            address = os.getenv("OPENAI_API_BASE") or None
            if address is None:
                gateway = os.getenv("LANGSMITH_GATEWAY", "")
                if gateway.lower() not in ("", "false", "0", "no"):
                    base = "https://gateway.smith.langchain.com" if gateway.lower() in ("true", "1", "yes") else gateway.rstrip("/")
                    address = f"{base}/openai/v1"
            if address is None:
                address = os.getenv("OPENAI_BASE_URL", DEFAULT_URLS["openai"])
    try:
        validated = ModelConfigUpdate.address_has_no_credentials(AnyHttpUrl(address))
    except ValueError:
        # Legacy environment URLs may contain credentials too; never echo them.
        raise ModelConfigUnavailable("model endpoint is invalid") from None
    return str(validated).rstrip("/")


def _public_config() -> dict:
    try:
        settings = load_llm_settings()
    except ValidationError:
        return {
            "configured": False, "provider": "openai", "model": "",
            "base_url": DEFAULT_URLS["openai"], "api_key_configured": False,
            "source": "environment",
        }
    return {
        "configured": True, "provider": settings.provider, "model": settings.model,
        "base_url": resolve_llm_base_url(settings), "api_key_configured": True,
        "source": "local" if _config_path().is_file() else "environment",
    }


def _save(payload: object) -> dict:
    try:
        update = ModelConfigUpdate.model_validate(payload)
    except ValidationError as failure:
        raise HTTPException(400, detail={"error": "INVALID_CONFIG"}) from failure
    with _lock:
        key = update.api_key.get_secret_value().strip() if update.api_key else ""
        if not key:
            try:
                current = load_llm_settings()
            except ValidationError:
                current = None
            if current is None or current.provider != update.provider or resolve_llm_base_url(current) != str(update.base_url).rstrip("/"):
                raise HTTPException(400, detail={"error": "API_KEY_REQUIRED"})
            key = current.api_key.get_secret_value()
        try:
            settings = LlmSettings(provider=update.provider, model=update.model, base_url=update.base_url, api_key=key)
        except ValidationError as failure:
            raise HTTPException(400, detail={"error": "INVALID_CONFIG"}) from failure
        path = _config_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = None
        try:
            with NamedTemporaryFile(mode="w", encoding="utf-8", dir=path.parent, prefix=".llm-", suffix=".tmp", delete=False) as file:
                temporary = Path(file.name)
                json.dump({
                    "provider": settings.provider, "model": settings.model,
                    "base_url": resolve_llm_base_url(settings), "api_key": settings.api_key.get_secret_value(),
                }, file, ensure_ascii=False)
                file.flush()
                os.fsync(file.fileno())
            os.replace(temporary, path)
        finally:
            if temporary is not None:
                temporary.unlink(missing_ok=True)
        return _public_config()


def _response(payload: dict) -> JSONResponse:
    return JSONResponse(payload, headers={"Cache-Control": "no-store"})


def _unavailable() -> HTTPException:
    return HTTPException(503, detail={"error": "MODEL_CONFIG_UNAVAILABLE"})


@router.get("/model-config")
def get_model_config() -> JSONResponse:
    try:
        with _lock:
            return _response(_public_config())
    except (OSError, ModelConfigUnavailable) as failure:
        raise _unavailable() from failure


@router.put("/model-config")
async def put_model_config(request: Request) -> JSONResponse:
    try:
        payload = await request.json()
    except (ValueError, UnicodeError) as failure:
        raise HTTPException(400, detail={"error": "INVALID_CONFIG"}) from failure
    try:
        return _response(await run_in_threadpool(_save, payload))
    except (OSError, ModelConfigUnavailable) as failure:
        raise _unavailable() from failure


@router.delete("/model-config")
def reset_model_config() -> JSONResponse:
    try:
        with _lock:
            _config_path().unlink(missing_ok=True)
            return _response(_public_config())
    except (OSError, ModelConfigUnavailable) as failure:
        raise _unavailable() from failure

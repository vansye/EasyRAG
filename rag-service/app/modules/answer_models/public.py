"""Public boundary for local answer-model configuration and opaque model sessions."""

from __future__ import annotations

import json
import os
from pathlib import Path
from tempfile import NamedTemporaryFile
from threading import RLock
from typing import Literal, Protocol

from pydantic import ValidationError as _ValidationError

from ._settings import _DEFAULT_CONFIG_PATH, _DEFAULT_URLS, _Settings, _Update, _resolve_base_url


__all__ = ["ChatSession", "ConfigRejected", "ModelUnavailable", "Models"]

_lock = RLock()


class ConfigRejected(ValueError):
    """An invalid update; code is safe to expose without the submitted payload."""

    def __init__(self, code: Literal["INVALID_CONFIG", "API_KEY_REQUIRED"] = "INVALID_CONFIG") -> None:
        self.code = code
        super().__init__({
            "INVALID_CONFIG": "model configuration is invalid",
            "API_KEY_REQUIRED": "an API key is required for this destination",
        }[code])


class ModelUnavailable(RuntimeError):
    """A local configuration or SDK failure with no upstream exception details."""

    def __init__(self, code: Literal[
        "MODEL_CONFIG_UNAVAILABLE", "LLM_NOT_CONFIGURED", "LLM_UNAVAILABLE",
    ] = "LLM_UNAVAILABLE") -> None:
        self.code = code
        super().__init__({
            "MODEL_CONFIG_UNAVAILABLE": "saved model settings or endpoint are unavailable",
            "LLM_NOT_CONFIGURED": "answer model is not configured",
            "LLM_UNAVAILABLE": "answer model is unavailable",
        }[code])


class ChatSession(Protocol):
    """An opaque session obtained from Models.open_session for one question."""

    @property
    def model_info(self) -> dict[str, str]:
        """Return a fresh frozen provider/model snapshot without credentials or endpoint."""
        ...

    def complete(self, prompt: str) -> str:
        """Complete one prompt using the same frozen model for this session."""
        ...


class _Session:
    __slots__ = ("__client", "__model_info")

    def __init__(self, settings: _Settings, base_url: str) -> None:
        self.__model_info = {"provider": settings.provider, "model": settings.model}
        try:
            from langchain.chat_models import init_chat_model

            self.__client = init_chat_model(
                settings.model, model_provider=settings.provider,
                api_key=settings.api_key.get_secret_value(), base_url=base_url,
                timeout=settings.timeout_seconds, max_retries=0,
            )
        except Exception:
            raise ModelUnavailable() from None

    @property
    def model_info(self) -> dict[str, str]:
        return self.__model_info.copy()

    def complete(self, prompt: str) -> str:
        try:
            from langchain_core.messages import HumanMessage

            response = self.__client.invoke([HumanMessage(content=prompt)])
            content = response.content
        except Exception:
            raise ModelUnavailable() from None
        if not isinstance(content, str) or not content.strip():
            raise ModelUnavailable()
        return content


def _read_settings(path: Path) -> tuple[_Settings, str]:
    try:
        raw = path.read_text(encoding="utf-8")
    except FileNotFoundError:
        try:
            return _Settings(), "environment"
        except _ValidationError:
            raise ModelUnavailable("LLM_NOT_CONFIGURED") from None
        except (OSError, UnicodeError):
            raise ModelUnavailable("MODEL_CONFIG_UNAVAILABLE") from None
    except (OSError, UnicodeError):
        raise ModelUnavailable("MODEL_CONFIG_UNAVAILABLE") from None
    try:
        data = json.loads(raw)
        if not isinstance(data, dict):
            raise ValueError("expected settings object")
        return _Settings(**data), "local"
    except (ValueError, TypeError, OSError):
        raise ModelUnavailable("MODEL_CONFIG_UNAVAILABLE") from None


def _base_url(settings: _Settings) -> str:
    try:
        return _resolve_base_url(settings)
    except ValueError:
        raise ModelUnavailable("MODEL_CONFIG_UNAVAILABLE") from None


def _snapshot(settings: _Settings, source: str) -> dict:
    return {
        "configured": True, "provider": settings.provider, "model": settings.model,
        "base_url": _base_url(settings), "api_key_configured": True, "source": source,
    }


def _write_settings(path: Path, settings: _Settings) -> None:
    temporary = None
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile(
            mode="w", encoding="utf-8", dir=path.parent, prefix=".llm-", suffix=".tmp", delete=False,
        ) as file:
            temporary = Path(file.name)
            json.dump({
                "provider": settings.provider, "model": settings.model,
                "base_url": _base_url(settings), "api_key": settings.api_key.get_secret_value(),
            }, file, ensure_ascii=False)
            file.flush()
            os.fsync(file.fileno())
        os.replace(temporary, path)
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


class Models:
    """Own saved credentials and lazily load settings independently of other modules.

    get/save/reset return fresh, credential-free Vue configuration snapshots.
    A supplied path wins over LLM_CONFIG_FILE and the service-root config/llm.json.
    """

    def __init__(self, config_path: Path | None = None) -> None:
        self._path = Path(config_path) if config_path is not None else None

    def _config_path(self) -> Path:
        return self._path if self._path is not None else Path(os.getenv("LLM_CONFIG_FILE", _DEFAULT_CONFIG_PATH))

    def get(self) -> dict:
        """Read public metadata without constructing a client or generating text."""
        with _lock:
            try:
                settings, source = _read_settings(self._config_path())
            except ModelUnavailable as failure:
                if failure.code != "LLM_NOT_CONFIGURED":
                    raise
                return {
                    "configured": False, "provider": "openai", "model": "",
                    "base_url": _DEFAULT_URLS["openai"], "api_key_configured": False,
                    "source": "environment",
                }
            return _snapshot(settings, source)

    def save(self, update: object) -> dict:
        """Persist an update; a blank/omitted key keeps only the same destination's key."""
        try:
            payload = _Update.model_validate(update)
        except _ValidationError:
            raise ConfigRejected() from None
        with _lock:
            path = self._config_path()
            key = payload.api_key.get_secret_value().strip() if payload.api_key else ""
            if not key:
                try:
                    current, _source = _read_settings(path)
                except ModelUnavailable as failure:
                    if failure.code != "LLM_NOT_CONFIGURED":
                        raise
                    current = None
                if (
                    current is None or current.provider != payload.provider
                    or _base_url(current) != str(payload.base_url).rstrip("/")
                ):
                    raise ConfigRejected("API_KEY_REQUIRED")
                key = current.api_key.get_secret_value()
            try:
                settings = _Settings(
                    provider=payload.provider, model=payload.model, base_url=payload.base_url, api_key=key,
                )
            except _ValidationError:
                raise ConfigRejected() from None
            except (OSError, UnicodeError):
                raise ModelUnavailable("MODEL_CONFIG_UNAVAILABLE") from None
            try:
                _write_settings(path, settings)
            except (OSError, UnicodeError):
                raise ModelUnavailable("MODEL_CONFIG_UNAVAILABLE") from None
            return _snapshot(settings, "local")

    def reset(self) -> dict:
        """Remove only the local override, restoring the current environment settings."""
        with _lock:
            try:
                self._config_path().unlink(missing_ok=True)
            except OSError:
                raise ModelUnavailable("MODEL_CONFIG_UNAVAILABLE") from None
            return self.get()

    def open_session(self) -> ChatSession:
        """Freeze settings and one client; later saves apply only to new sessions."""
        with _lock:
            settings, _source = _read_settings(self._config_path())
            base_url = _base_url(settings)
        return _Session(settings, base_url)

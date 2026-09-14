"""模块 C 的模型创建入口，不执行检索或 Agent 循环。"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.config import LlmSettings
from app.model_config import load_llm_settings, resolve_llm_base_url


def create_chat_model(settings: LlmSettings | None = None) -> BaseChatModel:
    resolved_settings = settings if settings is not None else load_llm_settings()
    model_options = {
        "api_key": resolved_settings.api_key.get_secret_value(),
        "base_url": resolve_llm_base_url(resolved_settings),
        "timeout": resolved_settings.timeout_seconds,
        "max_retries": 0,
    }
    return init_chat_model(
        resolved_settings.model,
        model_provider=resolved_settings.provider,
        **model_options,
    )

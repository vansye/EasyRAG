"""模块 C 的模型创建入口，不执行检索或 Agent 循环。"""

from langchain.chat_models import init_chat_model
from langchain_core.language_models import BaseChatModel

from app.config import LlmSettings


def create_chat_model(settings: LlmSettings | None = None) -> BaseChatModel:
    resolved_settings = settings if settings is not None else LlmSettings()
    model_options = {
        "api_key": resolved_settings.api_key.get_secret_value(),
        "timeout": resolved_settings.timeout_seconds,
        "max_retries": 0,
    }
    if resolved_settings.base_url is not None:
        model_options["base_url"] = str(resolved_settings.base_url)

    return init_chat_model(
        resolved_settings.model,
        model_provider=resolved_settings.provider,
        **model_options,
    )

"""Public model metadata for the knowledge workspace; credentials stay server-side."""

from fastapi import APIRouter, Request
from pydantic import ValidationError

from app.config import Settings
from app.model_config import ModelConfigUnavailable, load_llm_settings, router as model_config_router

router = APIRouter()
router.include_router(model_config_router)


@router.get('/runtime')
def runtime_info(request: Request) -> dict:
    settings: Settings = request.app.state.settings
    try:
        llm = load_llm_settings()
        model = {'configured': True, 'provider': llm.provider, 'model': llm.model}
    except (ValidationError, ModelConfigUnavailable):
        model = {'configured': False, 'provider': None, 'model': None}
    return {
        'llm': model,
        'embedding': {
            'provider': settings.embedding_provider,
            'model': settings.embedding_model,
            'dim': settings.embedding_dim,
        },
    }

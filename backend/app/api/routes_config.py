from fastapi import APIRouter
from sqlalchemy.engine import make_url

from app.core.config import settings
from app.adapters import list_adapters

router = APIRouter(prefix="/config", tags=["config"])


@router.get("/")
def get_config():
    """
    Diagnostics-only endpoint.
    - Does NOT expose secrets (no DB URL, no API keys).
    - Lets you confirm that .env has been loaded and the app is wired correctly.
    """
    db_dialect = None
    db_configured = False

    try:
        if settings.DATABASE_URL:
            url_obj = make_url(settings.DATABASE_URL)
            db_dialect = url_obj.get_backend_name()
            db_configured = True
    except Exception:
        db_dialect = "unknown"
        db_configured = False

    return {
        "project_name": settings.PROJECT_NAME,
        "api_v1_str": settings.API_V1_STR,
        "database": {
            "configured": db_configured,
            "dialect": db_dialect,
        },
        "runtime": {
            "container_runtime": settings.CONTAINER_RUNTIME,
            "container_network": settings.CONTAINER_NETWORK,
        },
        "ai": {
            "provider": settings.AI_PROVIDER.value,
            "google_model_name": settings.GOOGLE_MODEL_NAME,
            "openai_model_name": settings.OPENAI_MODEL_NAME,
            # keys intentionally omitted
        },
        "adapters": list_adapters(),  # {language: image}
    }

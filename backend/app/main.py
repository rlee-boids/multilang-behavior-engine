from fastapi import FastAPI

from app.core.config import settings
from app.db.session import engine
from app.db.base import Base
from app.api import (
    routes_behaviors,
    routes_contracts,
    routes_implementations,
    routes_ai,
    routes_config,
    routes_conversion,
    routes_runtime,
    routes_analyzer  
)
from app.adapters import *  # noqa: F401,F403


def create_app() -> FastAPI:
    Base.metadata.create_all(bind=engine)

    app = FastAPI(
        title=settings.PROJECT_NAME,
        openapi_url=f"{settings.API_V1_STR}/openapi.json",
    )

    app.include_router(routes_behaviors.router, prefix=settings.API_V1_STR)
    app.include_router(routes_contracts.router, prefix=settings.API_V1_STR)
    app.include_router(routes_implementations.router, prefix=settings.API_V1_STR)
    app.include_router(routes_ai.router, prefix=settings.API_V1_STR)
    app.include_router(routes_config.router, prefix=settings.API_V1_STR)
    app.include_router(routes_conversion.router, prefix=settings.API_V1_STR)
    app.include_router(routes_analyzer.router, prefix=settings.API_V1_STR)
    app.include_router(routes_runtime.router, prefix=settings.API_V1_STR) 

    return app


app = create_app()

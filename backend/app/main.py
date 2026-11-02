from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.routes import router
from app.config import settings
from app.db import Base, engine
from app.seed import seed

app = FastAPI(title=settings.app_name)
app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origin_list or ["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.include_router(router)


@app.on_event("startup")
def on_startup() -> None:
    # Alembic is the migration path in Docker. create_all lets a fresh local
    # database boot, and it does not alter tables that already exist.
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    seed()
    from app.firebase_store import start_firebase

    start_firebase()

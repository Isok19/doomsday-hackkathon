"""Запуск: python -m uvicorn app.main:app --reload"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware

from app.models import Channel, DatasetStats, DialogDetail, DialogPage, Language
from app.storage import DialogStore, read_dataset

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app(dataset_path: Path | None = None, database_path: Path | None = None) -> FastAPI:
    source_path = dataset_path or Path(os.getenv("CQA_DATASET_PATH", str(PROJECT_ROOT / "dialogs.json")))
    db_path = database_path or Path(os.getenv("CQA_DATABASE_PATH", str(PROJECT_ROOT / "data/cqa.sqlite3")))
    store = DialogStore(db_path)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        source = read_dataset(source_path)
        store.initialize()
        application.state.dataset_group_id = store.import_dataset(source)
        yield

    application = FastAPI(
        title="CQA — API диалогов", version="0.1.0", lifespan=lifespan,
        description="Локальный этап: импорт JSON в SQLite и просмотр диалогов. Оценка и апелляции ещё не подключены.",
    )
    origins = [value.strip() for value in os.getenv("CQA_CORS_ORIGINS", "").split(",") if value.strip()]
    if origins:
        application.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["GET"], allow_headers=["*"]
        )

    @application.get("/health", tags=["Состояние"])
    def health():
        return {"status": "ok"}

    @application.get("/api/dataset", response_model=DatasetStats, tags=["Диалоги"])
    def dataset():
        return store.stats(application.state.dataset_group_id)

    @application.get("/api/dialogs", response_model=DialogPage, tags=["Диалоги"])
    def list_dialogs(
        language: Language | None = None,
        channel: Channel | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
    ):
        return store.list_dialogs(application.state.dataset_group_id, language, channel, limit, offset)

    @application.get("/api/dialogs/{conversation_id}", response_model=DialogDetail, tags=["Диалоги"])
    def get_dialog(conversation_id: str):
        result = store.get_dialog(application.state.dataset_group_id, conversation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        return result

    return application


app = create_app()

"""Запуск: python -m uvicorn app.main:app --reload"""

import os
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Annotated

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from app.models import Channel, DatasetStats, DialogDetail, DialogPage, Language
from app.audio import stream_audio
from app.storage import DialogStore, read_dataset
from app.routes import workflow_router
from app.workflows import WorkflowError, WorkflowStore
from participant_3.evaluator import configured_evaluator

PROJECT_ROOT = Path(__file__).resolve().parent.parent


def create_app(dataset_path: Path | None = None, database_path: Path | None = None, evaluator=None) -> FastAPI:
    source_path = dataset_path or Path(os.getenv("CQA_DATASET_PATH", str(PROJECT_ROOT / "dialogs.json")))
    db_path = database_path or Path(os.getenv("CQA_DATABASE_PATH", str(PROJECT_ROOT / "data/cqa.sqlite3")))
    store = DialogStore(db_path)
    workflows = WorkflowStore(store)

    @asynccontextmanager
    async def lifespan(application: FastAPI):
        source = read_dataset(source_path) if source_path.exists() or dataset_path is not None else None
        store.initialize()
        application.state.dataset_group_id = store.import_dataset(source) if source else None
        workflows.initialize()
        yield

    application = FastAPI(
        title="CQA — система анализа качества диалогов", version="0.2.0", lifespan=lifespan,
        description="Диалоги, версии чек-листов, результаты и апелляции. ИИ-оценщик подключается отдельно.",
    )
    application.state.evaluator = evaluator
    application.include_router(workflow_router(application, store, workflows))

    @application.middleware('http')
    async def frontend_cache(request, call_next):
        response = await call_next(request)
        if request.url.path == '/' or request.url.path.startswith('/frontend/'):
            response.headers['Cache-Control'] = 'no-store'
        return response

    @application.exception_handler(WorkflowError)
    async def workflow_error(request, exc):
        return JSONResponse(status_code=exc.status, content={"detail": str(exc)})

    def selected_group(group):
        groups = store.groups()
        result = group or application.state.dataset_group_id or (groups[0] if groups else "")
        if result and result not in groups:
            raise HTTPException(404, "Набор не найден")
        return result
    origins = [value.strip() for value in os.getenv("CQA_CORS_ORIGINS", "").split(",") if value.strip()]
    if origins:
        application.add_middleware(
            CORSMiddleware, allow_origins=origins, allow_methods=["GET", "POST", "PUT"], allow_headers=["*"]
        )

    @application.get("/health", tags=["Состояние"])
    def health():
        return {"status": "ok"}

    @application.get("/api/dataset", response_model=DatasetStats, tags=["Диалоги"])
    def dataset(dataset_group_id: str | None = None):
        return store.stats(selected_group(dataset_group_id))

    @application.get("/api/dialogs", response_model=DialogPage, tags=["Диалоги"])
    def list_dialogs(
        language: Language | None = None,
        channel: Channel | None = None,
        limit: Annotated[int, Query(ge=1, le=100)] = 100,
        offset: Annotated[int, Query(ge=0)] = 0,
        dataset_group_id: str | None = None,
        q: str | None = None,
    ):
        return store.list_dialogs(selected_group(dataset_group_id), language, channel, limit, offset, q)

    @application.get("/api/dialogs/{conversation_id}", response_model=DialogDetail, tags=["Диалоги"])
    def get_dialog(conversation_id: str, dataset_group_id: str | None = None):
        result = store.get_dialog(selected_group(dataset_group_id), conversation_id)
        if result is None:
            raise HTTPException(status_code=404, detail="Диалог не найден")
        return result

    @application.get("/api/dialogs/{conversation_id}/audio", tags=["Диалоги"])
    def dialog_audio(conversation_id: str, request: Request, dataset_group_id: str | None = None):
        dialog = get_dialog(conversation_id, dataset_group_id)
        return stream_audio(dialog["audio_url"], request.headers.get("Range"))

    frontend = PROJECT_ROOT / "frontend"
    if frontend.exists():
        application.mount("/frontend", StaticFiles(directory=frontend), name="frontend")

        @application.get("/", include_in_schema=False)
        def home():
            return FileResponse(frontend / "index.html")

    return application


app = create_app(evaluator=configured_evaluator())

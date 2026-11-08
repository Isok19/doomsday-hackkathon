"""API двух интерфейсов. Автоматический оценщик подключается отдельно участником 3."""

import json
import os
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit
from urllib.request import urlopen

from fastapi import APIRouter, HTTPException, Request
from pydantic import ValidationError

from app.models import SourceEnvelope
from app.storage import DatasetImportError
from app.workflow_models import (
    AppealInput, ChecklistInput, ChecklistUpdate, DecisionInput, EvaluationInput, EvaluationRequest,
)
from app.workflows import WorkflowError
from participant_3.submission import build_submission

MAX_DATASET_BYTES = 10 * 1024 * 1024


def workflow_router(application, dialogs, workflows):
    router = APIRouter(prefix="/api")

    def ingest(content):
        try:
            payload = SourceEnvelope.model_validate_json(content)
            group = dialogs.import_dataset(payload)
        except ValidationError:
            raise HTTPException(422, "JSON не соответствует формату набора организаторов. Проверьте records, total_records и роли сообщений.") from None
        except DatasetImportError as exc:
            raise HTTPException(409, str(exc)) from None
        return dialogs.stats(group)

    @router.get("/capabilities", tags=["Состояние"])
    def capabilities():
        return {"evaluation_available": application.state.evaluator is not None,
                "evaluation_model": getattr(application.state.evaluator, 'model_name', None),
                "organizer_import_available": bool(os.getenv("CQA_ORGANIZER_URL"))}

    @router.get("/datasets", tags=["Диалоги"])
    def datasets():
        return [dialogs.stats(group) for group in dialogs.groups()]

    @router.get('/submissions', tags=['Сдача результатов'])
    def submission(dataset_group_id: str, split: str, checklist_id: str = 'cqa-default', version: int = 1):
        return build_submission(workflows, dataset_group_id, split, os.getenv('CQA_TEAM_NAME', 'zenoxxk'), checklist_id, version)

    @router.post("/datasets/import", status_code=201, tags=["Диалоги"])
    async def import_file(request: Request):
        content = bytearray()
        async for chunk in request.stream():
            content.extend(chunk)
            if len(content) > MAX_DATASET_BYTES:
                raise HTTPException(413, "Максимальный размер JSON — 10 МБ")
        return ingest(bytes(content).removeprefix(b"\xef\xbb\xbf"))

    @router.post("/datasets/fetch", status_code=201, tags=["Диалоги"])
    def fetch_dataset(group_id: str):
        source_url = os.getenv("CQA_ORGANIZER_URL")
        if not source_url:
            raise HTTPException(503, "Подключение API организаторов ещё не настроено. Загрузите выданный JSON.")
        parts = urlsplit(source_url)
        if parts.scheme != "https" or not parts.hostname or parts.username or parts.password:
            raise HTTPException(503, "Адрес API организаторов должен использовать HTTPS")
        query = [(key, value) for key, value in parse_qsl(parts.query, keep_blank_values=True) if key != "group_id"]
        query.append(("group_id", group_id))
        url = urlunsplit((parts.scheme, parts.netloc, parts.path, urlencode(query), ""))
        try:
            with urlopen(url, timeout=30) as response:
                content = response.read(MAX_DATASET_BYTES + 1)
        except (HTTPError, URLError, TimeoutError):
            raise HTTPException(502, "Не удалось получить набор от организаторов. Проверьте подключение и group_id.") from None
        if len(content) > MAX_DATASET_BYTES:
            raise HTTPException(413, "Набор превышает 10 МБ")
        try:
            returned_group = json.loads(content)["data"]["dataset_group_id"]
        except (ValueError, KeyError, TypeError):
            raise HTTPException(502, "API организаторов вернул неожиданный формат") from None
        if returned_group != group_id:
            raise HTTPException(502, "API организаторов вернул другой group_id")
        return ingest(content)

    @router.get("/checklists", tags=["Чек-листы"])
    def checklists():
        return workflows.checklists()

    @router.get("/checklists/{id}", tags=["Чек-листы"])
    def checklist(id: str, version: int | None = None):
        return workflows.checklist(id, version)

    @router.post("/checklists", status_code=201, tags=["Чек-листы"])
    def create_checklist(payload: ChecklistInput):
        return workflows.save_checklist(payload)

    @router.put("/checklists/{id}", tags=["Чек-листы"])
    def update_checklist(id: str, payload: ChecklistUpdate):
        return workflows.save_checklist(ChecklistInput(**payload.model_dump(exclude={"base_version"})), id, payload.base_version)

    @router.get("/evaluations", tags=["Оценки"])
    def evaluations(dataset_group_id: str, conversation_id: str):
        return workflows.evaluations(dataset_group_id, conversation_id)

    @router.get("/evaluations/{id}", tags=["Оценки"])
    def evaluation(id: str):
        return workflows.evaluation(id)

    @router.post("/evaluations", status_code=201, tags=["Оценки"])
    def receive_evaluation(payload: EvaluationInput):
        """Приём настоящего результата модуля участника 3; не генерирует ответы."""
        return workflows.create_evaluation(payload)

    @router.post("/evaluations/run", status_code=201, tags=["Оценки"])
    def run_evaluation(payload: EvaluationRequest):
        checklist = workflows.checklist(payload.checklist_id, payload.checklist_version)
        dialog = dialogs.get_dialog(payload.dataset_group_id, payload.conversation_id)
        if dialog is None:
            raise WorkflowError("Диалог не найден", 404)
        evaluator = application.state.evaluator
        if evaluator is None:
            raise HTTPException(503, "Автоматическая оценка ещё не подключена. Это следующий этап участника 3.")
        try:
            results = evaluator(dialog, checklist)
            data = EvaluationInput(**payload.model_dump(), results=results,
                                   model=getattr(evaluator, 'metadata', None))
            return workflows.create_evaluation(data)
        except WorkflowError as exc:
            workflows.record_failure(payload.dataset_group_id, payload.conversation_id, payload.checklist_id,
                                     payload.checklist_version, getattr(evaluator, 'metadata', None), str(exc))
            raise
        except Exception:
            workflows.record_failure(payload.dataset_group_id, payload.conversation_id, payload.checklist_id,
                                     payload.checklist_version, getattr(evaluator, 'metadata', None),
                                     'Модуль оценки не вернул корректный результат.')
            raise HTTPException(502, "Модуль оценки не вернул корректный результат. Повторите попытку позже.") from None

    @router.get("/appeals", tags=["Апелляции"])
    def appeals():
        return workflows.appeals()

    @router.get("/appeals/{id}", tags=["Апелляции"])
    def appeal(id: str):
        return workflows.appeal(id)

    @router.post("/appeals", status_code=201, tags=["Апелляции"])
    def create_appeal(payload: AppealInput):
        return workflows.create_appeal(payload)

    @router.post("/appeals/{id}/decision", tags=["Апелляции"])
    def decide(id: str, payload: DecisionInput):
        return workflows.decide(id, payload)

    return router

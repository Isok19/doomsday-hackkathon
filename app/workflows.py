"""Версии чек-листа, неизменяемые исходные оценки и решения по апелляциям."""

import json
from datetime import datetime, timezone
from decimal import Decimal, ROUND_HALF_UP
from uuid import uuid4

from app.checklist_seed import initial_checklist
from app.storage import DialogStore
from app.workflow_models import Answer, ChecklistInput


class WorkflowError(ValueError):
    def __init__(self, message, status=422):
        super().__init__(message)
        self.status = status


def now():
    return datetime.now(timezone.utc).isoformat()


def rounded(value):
    return float(value.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP))


def validate_answer(criterion, answer, message_ids):
    value = answer["value"]
    if not set(answer["evidence_message_ids"]).issubset(message_ids):
        raise WorkflowError("Доказательство ссылается на сообщение другого диалога или отсутствующее сообщение")
    if value is None:
        if not criterion["allow_na"] or not answer["na_reason"].strip():
            raise WorkflowError("N/A запрещён или отсутствует причина неприменимости")
    elif criterion["answer_type"] in ("binary", "flag"):
        if type(value) is not bool:
            raise WorkflowError("Критерий требует ответ true или false")
    elif type(value) not in (int, float) or value not in [step["value"] for step in criterion["scale"]]:
        raise WorkflowError("Оценка не является ступенью шкалы критерия")
    if value is not None and answer["na_reason"]:
        raise WorkflowError("Причина N/A разрешена только при value = null")


def calculate(checklist, results):
    """Только арифметика PDF. Содержательные ответы здесь не генерируются."""
    answers = {answer["criterion_id"]: answer for answer in results}
    applicable = sum((Decimal(str(c["weight"])) for c in checklist["criteria"]
                      if answers[c["id"]]["value"] is not None and c["answer_type"] != "flag"), Decimal(0))
    total, sections, rows, flags = Decimal(0), {}, [], []
    for criterion in checklist["criteria"]:
        answer = answers[criterion["id"]]
        value, weight = answer["value"], Decimal(str(criterion["weight"]))
        effective, points = Decimal(0), Decimal(0)
        if criterion["answer_type"] == "flag":
            if value is True:
                flags.append(criterion["id"])
        elif value is not None and applicable:
            effective = weight * 100 / applicable
            if criterion["answer_type"] == "numeric":
                maximum = max(step["value"] for step in criterion["scale"])
                ratio = Decimal(str(value)) / Decimal(str(maximum))
            else:
                ratio = Decimal(int(value))
            points = effective * ratio
            total += points
            if criterion["section"]:
                entry = sections.setdefault(criterion["section"], [Decimal(0), Decimal(0)])
                entry[0] += points
                entry[1] += effective
        rows.append({**answer, "criterion": criterion, "effective_weight": rounded(effective),
                     "points": None if value is None or criterion["answer_type"] == "flag" else rounded(points)})
    return {"total": rounded(total) if applicable else None, "results": rows,
            "critical_errors": flags, "sections": [
                {"name": name, "points": rounded(values[0]), "max_points": rounded(values[1])}
                for name, values in sections.items()]}


class WorkflowStore:
    def __init__(self, dialogs: DialogStore):
        self.dialogs = dialogs

    def initialize(self):
        with self.dialogs.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS checklists (
                    id TEXT NOT NULL, version INTEGER NOT NULL, content TEXT NOT NULL,
                    created_at TEXT NOT NULL, PRIMARY KEY(id, version)
                );
                CREATE TABLE IF NOT EXISTS evaluations (
                    id TEXT PRIMARY KEY, dataset_group_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
                    checklist_id TEXT NOT NULL, checklist_version INTEGER NOT NULL,
                    original_results TEXT NOT NULL, created_at TEXT NOT NULL,
                    FOREIGN KEY(checklist_id, checklist_version) REFERENCES checklists(id, version),
                    FOREIGN KEY(dataset_group_id, conversation_id) REFERENCES dialogs(dataset_group_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS appeals (
                    id TEXT PRIMARY KEY, evaluation_id TEXT NOT NULL REFERENCES evaluations(id),
                    criterion_id TEXT NOT NULL, comment TEXT NOT NULL, original_answer TEXT NOT NULL,
                    created_at TEXT NOT NULL, decision TEXT, resolved_at TEXT,
                    UNIQUE(evaluation_id, criterion_id)
                );
            """)
            db.execute("INSERT OR IGNORE INTO checklists VALUES (?, ?, ?, ?)",
                       ("cqa-default", 1, initial_checklist().model_dump_json(), now()))

    def checklist(self, checklist_id, version=None):
        with self.dialogs.connect() as db:
            if version is None:
                row = db.execute("SELECT * FROM checklists WHERE id=? ORDER BY version DESC LIMIT 1", (checklist_id,)).fetchone()
            else:
                row = db.execute("SELECT * FROM checklists WHERE id=? AND version=?", (checklist_id, version)).fetchone()
        if not row:
            raise WorkflowError("Чек-лист или его версия не найдены", 404)
        return {**json.loads(row["content"]), "id": row["id"], "version": row["version"], "created_at": row["created_at"]}

    def checklists(self):
        with self.dialogs.connect() as db:
            ids = [row[0] for row in db.execute("SELECT DISTINCT id FROM checklists ORDER BY created_at")]
        return [self.checklist(id) for id in ids]

    def save_checklist(self, payload, checklist_id=None, base_version=None):
        checklist_id = checklist_id or str(uuid4())
        with self.dialogs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            version = db.execute("SELECT MAX(version) FROM checklists WHERE id=?", (checklist_id,)).fetchone()[0]
            if base_version is not None and version is None:
                raise WorkflowError("Чек-лист не найден", 404)
            if version is not None and base_version != version:
                raise WorkflowError("Чек-лист уже изменён. Откройте последнюю версию перед сохранением.", 409)
            next_version = (version or 0) + 1
            db.execute("INSERT INTO checklists VALUES (?, ?, ?, ?)",
                       (checklist_id, next_version, payload.model_dump_json(), now()))
        return self.checklist(checklist_id, next_version)

    def create_evaluation(self, payload):
        checklist = self.checklist(payload.checklist_id, payload.checklist_version)
        dialog = self.dialogs.get_dialog(payload.dataset_group_id, payload.conversation_id)
        if dialog is None:
            raise WorkflowError("Диалог не найден", 404)
        results = [answer.model_dump() for answer in payload.results]
        ids = [answer["criterion_id"] for answer in results]
        if len(ids) != len(set(ids)) or set(ids) != {c["id"] for c in checklist["criteria"]}:
            raise WorkflowError("Нужен ровно один ответ на каждый критерий выбранной версии")
        answers = {answer["criterion_id"]: answer for answer in results}
        for criterion in checklist["criteria"]:
            validate_answer(criterion, answers[criterion["id"]], {m["message_id"] for m in dialog["transcript"]})
        id = str(uuid4())
        with self.dialogs.connect() as db:
            db.execute("INSERT INTO evaluations VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (id, payload.dataset_group_id, payload.conversation_id, payload.checklist_id,
                        payload.checklist_version, json.dumps(results, ensure_ascii=False), now()))
        return self.evaluation(id)

    def evaluation(self, id):
        with self.dialogs.connect() as db:
            row = db.execute("SELECT * FROM evaluations WHERE id=?", (id,)).fetchone()
            appeals = db.execute("SELECT * FROM appeals WHERE evaluation_id=? ORDER BY created_at", (id,)).fetchall()
        if row is None:
            raise WorkflowError("Оценка не найдена", 404)
        checklist = self.checklist(row["checklist_id"], row["checklist_version"])
        original = json.loads(row["original_results"])
        current = {answer["criterion_id"]: answer for answer in original}
        for appeal in appeals:
            decision = json.loads(appeal["decision"]) if appeal["decision"] else None
            if decision and decision["action"] == "change":
                current[appeal["criterion_id"]] = decision["replacement"]
        return {"id": id, "dataset_group_id": row["dataset_group_id"], "conversation_id": row["conversation_id"],
                "created_at": row["created_at"], "checklist": checklist,
                "original": calculate(checklist, original), "final": calculate(checklist, list(current.values())),
                "appeals": [self.appeal_row(appeal) for appeal in appeals]}

    def evaluations(self, group, conversation_id):
        with self.dialogs.connect() as db:
            rows = db.execute("SELECT id, checklist_id, checklist_version, created_at FROM evaluations WHERE dataset_group_id=? AND conversation_id=? ORDER BY created_at DESC",
                              (group, conversation_id)).fetchall()
        return [dict(row) for row in rows]

    @staticmethod
    def appeal_row(row):
        result = dict(row)
        result["original_answer"] = json.loads(result["original_answer"])
        result["decision"] = json.loads(result["decision"]) if result["decision"] else None
        result["status"] = "resolved" if result["decision"] else "open"
        return result

    def appeals(self):
        with self.dialogs.connect() as db:
            rows = db.execute("""SELECT a.*, e.conversation_id, e.dataset_group_id FROM appeals a
                JOIN evaluations e ON e.id=a.evaluation_id ORDER BY a.created_at DESC""").fetchall()
        return [self.appeal_row(row) for row in rows]

    def appeal(self, id):
        with self.dialogs.connect() as db:
            row = db.execute("SELECT * FROM appeals WHERE id=?", (id,)).fetchone()
        if row is None:
            raise WorkflowError("Апелляция не найдена", 404)
        result = self.appeal_row(row)
        result["evaluation"] = self.evaluation(row["evaluation_id"])
        return result

    def create_appeal(self, payload):
        evaluation = self.evaluation(payload.evaluation_id)
        answer = next((r for r in evaluation["final"]["results"] if r["criterion_id"] == payload.criterion_id), None)
        if answer is None:
            raise WorkflowError("Критерий не найден в оценке", 404)
        id = str(uuid4())
        with self.dialogs.connect() as db:
            db.execute("BEGIN IMMEDIATE")
            if db.execute("SELECT 1 FROM appeals WHERE evaluation_id=? AND criterion_id=?", (payload.evaluation_id, payload.criterion_id)).fetchone():
                raise WorkflowError("Апелляция по этому критерию уже существует", 409)
            db.execute("INSERT INTO appeals VALUES (?, ?, ?, ?, ?, ?, NULL, NULL)",
                       (id, payload.evaluation_id, payload.criterion_id, payload.comment,
                        json.dumps(answer, ensure_ascii=False), now()))
        return self.appeal(id)

    def decide(self, id, payload):
        appeal = self.appeal(id)
        if payload.replacement:
            criterion = appeal["original_answer"]["criterion"]
            if payload.replacement.criterion_id != criterion["id"]:
                raise WorkflowError("Решение относится к другому критерию")
            evaluation = appeal["evaluation"]
            dialog = self.dialogs.get_dialog(evaluation["dataset_group_id"], evaluation["conversation_id"])
            validate_answer(criterion, payload.replacement.model_dump(), {m["message_id"] for m in dialog["transcript"]})
        with self.dialogs.connect() as db:
            result = db.execute("UPDATE appeals SET decision=?, resolved_at=? WHERE id=? AND decision IS NULL",
                                (payload.model_dump_json(), now(), id))
            if result.rowcount != 1:
                raise WorkflowError("Решение уже сохранено", 409)
        return self.appeal(id)

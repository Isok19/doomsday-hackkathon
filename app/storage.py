"""Локальная SQLite: атомарный импорт и чтение без изменения оригинального JSON."""

import hashlib
import json
import sqlite3
from contextlib import contextmanager
from pathlib import Path
from uuid import NAMESPACE_URL, uuid5

from pydantic import ValidationError

from app.models import SourceEnvelope


class DatasetImportError(ValueError):
    """Ошибка файла данных, безопасная для вывода без содержимого диалогов."""


def read_dataset(path: Path) -> SourceEnvelope:
    try:
        payload = json.loads(path.read_text(encoding="utf-8-sig"))
        return SourceEnvelope.model_validate(payload)
    except OSError as exc:
        raise DatasetImportError(f"Не удалось прочитать файл данных: {path}") from exc
    except (UnicodeError, json.JSONDecodeError) as exc:
        raise DatasetImportError("Файл должен содержать корректный JSON в UTF-8") from exc
    except ValidationError as exc:
        # Не выводим input_value: там могут быть транскрипты и подписанные URL.
        locations = [".".join(map(str, e["loc"])) or "root" for e in exc.errors()[:5]]
        raise DatasetImportError("Неверная структура набора: " + ", ".join(locations)) from None


class DialogStore:
    def __init__(self, path: Path):
        self.path = path

    @contextmanager
    def connect(self):
        connection = sqlite3.connect(self.path, timeout=10)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        try:
            with connection:
                yield connection
        finally:
            connection.close()

    def initialize(self):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS datasets (
                    dataset_group_id TEXT PRIMARY KEY,
                    content_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS dialogs (
                    dataset_group_id TEXT NOT NULL REFERENCES datasets(dataset_group_id),
                    conversation_id TEXT NOT NULL,
                    channel TEXT NOT NULL,
                    language TEXT NOT NULL,
                    audio_url TEXT,
                    source_order INTEGER NOT NULL,
                    PRIMARY KEY (dataset_group_id, conversation_id)
                );
                CREATE TABLE IF NOT EXISTS messages (
                    message_id TEXT PRIMARY KEY,
                    dataset_group_id TEXT NOT NULL,
                    conversation_id TEXT NOT NULL,
                    position INTEGER NOT NULL,
                    timestamp TEXT NOT NULL,
                    speaker TEXT NOT NULL,
                    text TEXT NOT NULL,
                    UNIQUE (dataset_group_id, conversation_id, position),
                    FOREIGN KEY (dataset_group_id, conversation_id)
                        REFERENCES dialogs(dataset_group_id, conversation_id)
                );
            """)

    def import_dataset(self, source: SourceEnvelope) -> str:
        dataset = source.data
        group = dataset.dataset_group_id
        digest = hashlib.sha256(dataset.model_dump_json().encode()).hexdigest()
        with self.connect() as db:
            # Один процесс может импортировать набор, остальные ждут завершения.
            db.execute("BEGIN IMMEDIATE")
            existing = db.execute(
                "SELECT content_hash FROM datasets WHERE dataset_group_id = ?", (group,)
            ).fetchone()
            if existing:
                if existing["content_hash"] == digest:
                    return group
                # Не затираем данные, на которые в дальнейшем будут ссылаться оценки.
                raise DatasetImportError(
                    f"Набор {group} уже импортирован с другим содержимым. "
                    "Используйте отдельную БД; обновление набора требует согласования."
                )
            db.execute("INSERT INTO datasets VALUES (?, ?)", (group, digest))
            for order, dialog in enumerate(dataset.records):
                db.execute(
                    "INSERT INTO dialogs VALUES (?, ?, ?, ?, ?, ?)",
                    (group, dialog.conversation_id, dialog.channel, dialog.language,
                     dialog.audio_url, order),
                )
                for position, message in enumerate(dialog.transcript, start=1):
                    identity = json.dumps([group, dialog.conversation_id, position])
                    message_id = str(uuid5(NAMESPACE_URL, identity))
                    db.execute(
                        "INSERT INTO messages VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (message_id, group, dialog.conversation_id, position,
                         message.timestamp, message.speaker, message.text),
                    )
        return group

    def groups(self):
        with self.connect() as db:
            return [row[0] for row in db.execute("SELECT dataset_group_id FROM datasets ORDER BY dataset_group_id")]

    def list_dialogs(self, group, language=None, channel=None, limit=100, offset=0, q=None):
        conditions = ["d.dataset_group_id = ?"]
        values = [group]
        for field, value in (("language", language), ("channel", channel)):
            if value is not None:
                conditions.append(f"d.{field} = ?")
                values.append(value)
        if q:
            conditions.append("instr(lower(d.conversation_id), lower(?)) > 0")
            values.append(q)
        where = " AND ".join(conditions)
        with self.connect() as db:
            total = db.execute(
                f"SELECT COUNT(*) FROM dialogs d WHERE {where}", values
            ).fetchone()[0]
            rows = db.execute(
                f"""SELECT d.dataset_group_id, d.conversation_id, d.channel, d.language,
                    CASE WHEN d.audio_url IS NOT NULL AND d.audio_url != '' THEN 1 ELSE 0 END AS has_audio,
                    (SELECT COUNT(*) FROM messages m WHERE
                        m.dataset_group_id = d.dataset_group_id AND
                        m.conversation_id = d.conversation_id) AS message_count
                    FROM dialogs d WHERE {where} ORDER BY d.source_order
                    LIMIT ? OFFSET ?""", [*values, limit, offset]
            ).fetchall()
        return {"total": total, "limit": limit, "offset": offset,
                "items": [dict(row) for row in rows]}

    def get_dialog(self, group, conversation_id):
        with self.connect() as db:
            row = db.execute(
                "SELECT * FROM dialogs WHERE dataset_group_id = ? AND conversation_id = ?",
                (group, conversation_id),
            ).fetchone()
            if row is None:
                return None
            messages = db.execute(
                """SELECT message_id, position, timestamp, speaker, text FROM messages
                   WHERE dataset_group_id = ? AND conversation_id = ? ORDER BY position""",
                (group, conversation_id),
            ).fetchall()
        result = dict(row)
        result.pop("source_order")
        result.update(transcript=[dict(m) for m in messages], message_count=len(messages),
                      has_audio=bool(result["audio_url"]))
        return result

    def stats(self, group):
        with self.connect() as db:
            total = db.execute(
                "SELECT COUNT(*) FROM dialogs WHERE dataset_group_id = ?", (group,)
            ).fetchone()[0]
            messages = db.execute(
                "SELECT COUNT(*) FROM messages WHERE dataset_group_id = ?", (group,)
            ).fetchone()[0]
            audio = db.execute(
                """SELECT COUNT(*) FROM dialogs WHERE dataset_group_id = ?
                   AND audio_url IS NOT NULL AND audio_url != ''""", (group,)
            ).fetchone()[0]
            counts = {}
            for field in ("language", "channel"):
                counts[f"by_{field}"] = dict(db.execute(
                    f"SELECT {field}, COUNT(*) FROM dialogs WHERE dataset_group_id = ? GROUP BY {field}",
                    (group,),
                ).fetchall())
        return {"dataset_group_id": group, "total_records": total,
                "total_messages": messages, "audio_links": audio, **counts}

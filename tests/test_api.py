"""Проверки на выданном DEV, без вымышленных диалогов и оценок."""

import json
import sqlite3
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.main import create_app
from app.storage import DatasetImportError, DialogStore, read_dataset

SOURCE = Path(__file__).resolve().parent.parent / "dialogs.json"


@pytest.fixture
def client(tmp_path):
    with TestClient(create_app(SOURCE, tmp_path / "test.sqlite3")) as connection:
        yield connection


def test_complete_dev_import_and_exact_transcripts(client):
    original = json.loads(SOURCE.read_text())
    assert client.get("/health").json() == {"status": "ok"}
    stats = client.get("/api/dataset").json()
    assert stats == {
        "dataset_group_id": "DEV", "total_records": 65, "total_messages": 1268,
        "by_language": {"RU": 35, "KZ": 15, "EN": 15},
        "by_channel": {"agent chat": 60, "voice call": 5}, "audio_links": 5,
    }
    ids = set()
    for dialog in original["data"]["records"]:
        response = client.get(f"/api/dialogs/{dialog['conversation_id']}")
        assert response.status_code == 200
        result = response.json()
        assert result["audio_url"] == dialog["audio_url"]
        assert result["message_count"] == len(dialog["transcript"])
        for index, (actual, expected) in enumerate(zip(result["transcript"], dialog["transcript"]), 1):
            assert {key: actual[key] for key in expected} == expected
            assert actual["position"] == index
            assert actual["message_id"] not in ids
            ids.add(actual["message_id"])
    assert len(ids) == 1268


def test_filters_pagination_and_errors(client):
    result = client.get("/api/dialogs", params={"channel": "voice call", "language": "RU", "limit": 2}).json()
    assert result["total"] == 5
    assert len(result["items"]) == 2
    assert all(row["has_audio"] for row in result["items"])
    page = client.get("/api/dialogs", params={"channel": "voice call", "offset": 2}).json()
    assert len(page["items"]) == 3
    assert not ({row["conversation_id"] for row in page["items"]} &
                {row["conversation_id"] for row in result["items"]})
    assert client.get("/api/dialogs", params={"offset": 1000}).json()["items"] == []
    for params in ({"language": "INVALID"}, {"limit": 0}, {"offset": -1}, {"channel": "invalid"}):
        assert client.get("/api/dialogs", params=params).status_code == 422
    assert client.get("/api/dialogs/not-a-real-id").status_code == 404


def test_restart_is_idempotent_and_ids_are_stable(tmp_path):
    database = tmp_path / "test.sqlite3"
    first_id = json.loads(SOURCE.read_text())["data"]["records"][0]["conversation_id"]
    with TestClient(create_app(SOURCE, database)) as client:
        before = client.get(f"/api/dialogs/{first_id}").json()
    with TestClient(create_app(SOURCE, database)) as client:
        assert client.get(f"/api/dialogs/{first_id}").json() == before
        assert client.get("/api/dataset").json()["total_messages"] == 1268
    with sqlite3.connect(database) as connection:
        assert connection.execute("PRAGMA foreign_key_check").fetchall() == []


@pytest.mark.parametrize("change", ["duplicate", "count", "speaker", "failure", "json"])
def test_bad_input_does_not_create_database(tmp_path, change):
    payload = json.loads(SOURCE.read_text())
    if change == "duplicate":
        payload["data"]["records"][1]["conversation_id"] = payload["data"]["records"][0]["conversation_id"]
    elif change == "count":
        payload["data"]["total_records"] = 0
    elif change == "speaker":
        payload["data"]["records"][0]["transcript"][0]["speaker"] = "invalid"
    elif change == "failure":
        payload["isSuccess"] = False
    path = tmp_path / "invalid.json"
    path.write_text("{" if change == "json" else json.dumps(payload))
    database = tmp_path / "not-created.sqlite3"
    with pytest.raises(DatasetImportError):
        with TestClient(create_app(path, database)):
            pass
    assert not database.exists()


def test_changed_source_does_not_overwrite_imported_data(tmp_path):
    store = DialogStore(tmp_path / "test.sqlite3")
    store.initialize()
    source = read_dataset(SOURCE)
    store.import_dataset(source)
    before = store.stats("DEV")
    changed = source.model_copy(deep=True)
    changed.data.records.reverse()
    with pytest.raises(DatasetImportError):
        store.import_dataset(changed)
    assert store.stats("DEV") == before


def test_missing_file_has_clear_error(tmp_path):
    with pytest.raises(DatasetImportError, match="Не удалось прочитать"):
        read_dataset(tmp_path / "missing.json")

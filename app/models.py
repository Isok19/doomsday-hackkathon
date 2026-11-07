"""Формат организаторов и контракт API для команды."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, model_validator

Language = Literal["RU", "KZ", "EN"]
Channel = Literal["agent chat", "voice call"]
Speaker = Literal["client", "operator", "bot", "system"]


class SourceModel(BaseModel):
    model_config = ConfigDict(strict=True)


class SourceMessage(SourceModel):
    # Строка сохраняет точность и часовой пояс исходного времени.
    timestamp: str = Field(min_length=1)
    speaker: Speaker
    text: str


class SourceDialog(SourceModel):
    conversation_id: str = Field(min_length=1)
    channel: Channel
    language: Language
    transcript: list[SourceMessage] = Field(min_length=1)
    audio_url: str | None


class Dataset(SourceModel):
    dataset_group_id: str = Field(min_length=1)
    total_records: int = Field(ge=0)
    records: list[SourceDialog]

    @model_validator(mode="after")
    def check_records(self):
        if self.total_records != len(self.records):
            raise ValueError("total_records не совпадает с числом диалогов")
        ids = [record.conversation_id for record in self.records]
        if len(ids) != len(set(ids)):
            raise ValueError("conversation_id повторяется внутри набора")
        return self


class SourceEnvelope(SourceModel):
    error: str
    isSuccess: bool
    data: Dataset

    @model_validator(mode="after")
    def check_success(self):
        if not self.isSuccess or self.error:
            raise ValueError("Источник сообщил об ошибке получения данных")
        return self


class Message(SourceMessage):
    message_id: str
    position: int = Field(ge=1)


class DialogSummary(BaseModel):
    conversation_id: str
    dataset_group_id: str
    channel: Channel
    language: Language
    message_count: int
    has_audio: bool


class DialogDetail(DialogSummary):
    audio_url: str | None
    transcript: list[Message]


class DialogPage(BaseModel):
    total: int
    limit: int
    offset: int
    items: list[DialogSummary]


class DatasetStats(BaseModel):
    dataset_group_id: str
    total_records: int
    total_messages: int
    by_language: dict[str, int]
    by_channel: dict[str, int]
    audio_links: int

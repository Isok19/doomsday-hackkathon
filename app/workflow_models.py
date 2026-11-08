"""Контракт редактора, результатов оценки и апелляций."""

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictFloat, StrictInt, model_validator


class InputModel(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True, allow_inf_nan=False)


class ScaleStep(InputModel):
    value: float = Field(ge=0)
    description: str = Field(min_length=1, max_length=2000)


class Criterion(InputModel):
    id: str = Field(min_length=1, max_length=100, pattern=r"^[\w-]+$")
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(min_length=1, max_length=5000)
    section: str = Field(default="", max_length=200)
    weight: float = Field(ge=0, le=1000)
    answer_type: Literal["binary", "numeric", "flag"]
    allow_na: bool = False
    na_condition: str = Field(default="", max_length=2000)
    scale: list[ScaleStep] = Field(default_factory=list, max_length=50)

    @model_validator(mode="after")
    def consistent_rules(self):
        if self.allow_na and not self.na_condition:
            raise ValueError("Для N/A необходимо указать условие неприменимости")
        if not self.allow_na and self.na_condition:
            raise ValueError("Условие N/A задано, но N/A запрещён")
        if self.answer_type == "numeric":
            values = [step.value for step in self.scale]
            if len(values) < 2 or min(values) != 0 or max(values) <= 0 or len(set(values)) != len(values):
                raise ValueError("Шкала должна содержать 0, положительный максимум и уникальные ступени")
        elif self.scale:
            raise ValueError("Шкала разрешена только для числового критерия")
        if self.answer_type == "flag" and (self.weight != 0 or self.allow_na):
            raise ValueError("Флаг нарушения имеет вес 0 и не допускает N/A")
        return self


class ChecklistInput(InputModel):
    name: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=5000)
    criteria: list[Criterion] = Field(min_length=1, max_length=100)

    @model_validator(mode="after")
    def unique_ids(self):
        ids = [criterion.id for criterion in self.criteria]
        if len(ids) != len(set(ids)):
            raise ValueError("ID критериев должны быть уникальны")
        return self


class ChecklistUpdate(ChecklistInput):
    base_version: int = Field(ge=1)


class Answer(InputModel):
    criterion_id: str
    value: StrictBool | StrictInt | StrictFloat | None
    explanation: str = Field(min_length=1, max_length=10000)
    evidence_message_ids: list[str] = Field(min_length=1, max_length=1000)
    na_reason: str = Field(default="", max_length=3000)


class EvaluationRequest(InputModel):
    dataset_group_id: str = Field(min_length=1)
    conversation_id: str = Field(min_length=1)
    checklist_id: str = Field(min_length=1)
    checklist_version: int = Field(ge=1)


class EvaluationInput(EvaluationRequest):
    results: list[Answer] = Field(min_length=1, max_length=100)
    model: "ModelInfo | None" = None


class ModelInfo(InputModel):
    mode: Literal["local", "external_api", "hybrid"]
    name: str = Field(min_length=1, max_length=300)
    prompt_version: str = Field(default="", max_length=100)
    knowledge_sha256: str = Field(default="", max_length=64)


EvaluationInput.model_rebuild()


class AppealInput(InputModel):
    evaluation_id: str
    criterion_id: str
    comment: str = Field(min_length=1, max_length=5000)


class DecisionInput(InputModel):
    action: Literal["confirm", "change"]
    reason: str = Field(min_length=1, max_length=5000)
    replacement: Answer | None = None

    @model_validator(mode="after")
    def replacement_required(self):
        if (self.action == "change") != (self.replacement is not None):
            raise ValueError("Новая оценка нужна только для действия change")
        return self

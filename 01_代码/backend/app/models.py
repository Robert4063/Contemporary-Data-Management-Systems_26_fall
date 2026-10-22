import math
import re
from datetime import datetime, timezone
from typing import Annotated, Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictFloat, model_validator, field_validator

Identifier = Annotated[str, Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,39}$')]
Number = Annotated[StrictInt | StrictFloat, Field(ge=-9_000_000_000_000_000, le=9_000_000_000_000_000)]


class Model(BaseModel):
    model_config = ConfigDict(extra='forbid', allow_inf_nan=False)


class Credentials(Model):
    username: Annotated[str, Field(pattern=r'^[A-Za-z0-9_]{3,32}$')]
    password: Annotated[str, Field(min_length=8, max_length=128)]


class Option(Model):
    id: Identifier
    label: Annotated[str, Field(min_length=1, max_length=200)]

    @field_validator('label')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('选项内容不能全部为空白')
        return value.strip()


class Validation(Model):
    min_choices: Annotated[int, Field(strict=True, ge=0, le=100)] = 0
    max_choices: Annotated[int, Field(strict=True, ge=1, le=100)] | None = None
    min_length: Annotated[int, Field(strict=True, ge=0, le=10000)] = 0
    max_length: Annotated[int, Field(strict=True, ge=1, le=10000)] = 10000
    minimum: Number | None = None
    maximum: Number | None = None
    integer_only: StrictBool = False

    @model_validator(mode='after')
    def bounds(self):
        if self.max_choices is not None and self.min_choices > self.max_choices:
            raise ValueError('最少选择数不能超过最多选择数')
        if self.min_length > self.max_length:
            raise ValueError('最小文本长度不能超过最大长度')
        if self.minimum is not None and self.maximum is not None and self.minimum > self.maximum:
            raise ValueError('数字最小值不能超过最大值')
        return self


class Rule(Model):
    op: Literal['eq', 'ne', 'contains_any', 'contains_all', 'contains_none', 'gt', 'gte', 'lt', 'lte', 'between']
    value: Any
    target: Identifier  # END is reserved for finishing the questionnaire.


class Question(Model):
    id: Identifier
    title: Annotated[str, Field(min_length=1, max_length=500)]
    type: Literal['single', 'multiple', 'text', 'number']
    required: StrictBool = True
    options: list[Option] = Field(default_factory=list, max_length=100)
    validation: Validation = Field(default_factory=Validation)
    jump_enabled: StrictBool = False
    rules: list[Rule] = Field(default_factory=list, max_length=100)

    @model_validator(mode='before')
    @classmethod
    def legacy_jump_setting(cls, value):
        # Older clients/documents used the presence of rules as the enable flag.
        if isinstance(value, dict) and 'jump_enabled' not in value:
            return {**value, 'jump_enabled': bool(value.get('rules'))}
        return value

    @field_validator('title')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('题目标题不能全部为空白')
        return value.strip()

    @model_validator(mode='after')
    def configuration(self):
        if self.type == 'text' and self.jump_enabled:
            raise ValueError('文本题不能启用条件跳转')
        if self.type in ('multiple', 'number') and len(self.rules) > 1:
            raise ValueError('每道多选题或数字题最多只能配置一条跳转规则，请先删除旧规则')
        if self.type == 'single':
            option_rules = [rule.value for rule in self.rules if rule.op == 'eq' and isinstance(rule.value, str)]
            if len(option_rules) != len(set(option_rules)):
                raise ValueError('同一个单选选项只能配置一个跳转目标')
        if self.id == 'END':
            raise ValueError('END 是保留标识')
        ids = [o.id for o in self.options]
        if len(ids) != len(set(ids)):
            raise ValueError('选项 ID 不能重复')
        if self.type in ('single', 'multiple'):
            if len(ids) < 2:
                raise ValueError('选择题至少需要两个选项')
        elif ids:
            raise ValueError('填空题不能配置选项')
        v = self.validation
        if self.type == 'multiple' and (v.min_choices > len(ids) or (v.max_choices or len(ids)) > len(ids)):
            raise ValueError('选择数量限制不能超过选项总数')
        defaults = Validation().model_dump()
        allowed = {'single': set(), 'multiple': {'min_choices', 'max_choices'},
                   'text': {'min_length', 'max_length'}, 'number': {'minimum', 'maximum', 'integer_only'}}[self.type]
        if any(value != defaults[key] for key, value in v.model_dump().items() if key not in allowed):
            raise ValueError('校验规则与题型不匹配')
        if self.type == 'number' and v.integer_only and v.minimum is not None and v.maximum is not None:
            if math.ceil(v.minimum) > math.floor(v.maximum):
                raise ValueError('数字范围内不存在整数')
        for r in self.rules:
            value = r.value
            if self.type == 'single':
                valid = r.op in ('eq', 'ne') and isinstance(value, str) and value in ids
            elif self.type == 'multiple':
                valid = (r.op in ('contains_any', 'contains_all', 'contains_none') and isinstance(value, list)
                         and bool(value) and all(isinstance(x, str) and x in ids for x in value)
                         and len(value) == len(set(value)))
            elif self.type == 'number':
                def numeric(x):
                    return type(x) in (int, float) and abs(x) <= 9e15 and math.isfinite(x)
                valid = ((r.op in ('eq', 'ne', 'gt', 'gte', 'lt', 'lte') and numeric(value))
                         or (r.op == 'between' and isinstance(value, list) and len(value) == 2
                             and all(numeric(x) for x in value) and value[0] <= value[1]))
            else:
                valid = False
            if not valid:
                raise ValueError('跳转操作符或比较值与题型不匹配')
        return self


class SurveyInput(Model):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=4000)] = ''
    allow_anonymous: StrictBool = False
    allow_multiple: StrictBool = False
    deadline: datetime
    questions: list[Question] = Field(default_factory=list, max_length=200)

    @field_validator('title')
    @classmethod
    def nonblank(cls, value):
        if not value.strip():
            raise ValueError('问卷标题不能全部为空白')
        return value.strip()

    @field_validator('deadline', mode='before')
    @classmethod
    def deadline_format(cls, value):
        if value is None or value == '':
            raise ValueError('截止时间必须填写')
        if isinstance(value, datetime):
            return value
        if not isinstance(value, str) or not re.fullmatch(
            r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d{1,6})?(?:Z|[+-]\d{2}:\d{2})', value):
            raise ValueError('截止时间必须为带时区的ISO8601日期时间，例如2026-12-31T23:59:00+08:00')
        return value

    @field_validator('deadline')
    @classmethod
    def timezone_required(cls, value):
        if value is not None and value.utcoffset() is None:
            raise ValueError('截止时间必须包含时区')
        if value is None:
            return value
        value = value.astimezone(timezone.utc)
        return value.replace(microsecond=value.microsecond // 1000 * 1000)

    @model_validator(mode='after')
    def graph(self):
        positions = {q.id: i for i, q in enumerate(self.questions)}
        if len(positions) != len(self.questions):
            raise ValueError('题目 ID 不能重复')
        for i, q in enumerate(self.questions):
            for r in q.rules:
                if r.target != 'END' and (r.target not in positions or positions[r.target] <= i):
                    raise ValueError('跳转目标必须是后续题目或 END，禁止循环')
        return self


class SurveyUpdate(SurveyInput):
    revision: Annotated[int, Field(strict=True, ge=1)]


class DeadlineInput(Model):
    deadline: datetime

    _timezone = field_validator('deadline')(SurveyInput.timezone_required.__func__)
    _format = field_validator('deadline', mode='before')(SurveyInput.deadline_format.__func__)


class Submission(Model):
    answers: dict[Identifier, Any] = Field(default_factory=dict, max_length=200)
    anonymous: StrictBool = False


class ResponseSubmission(Submission):
    submission_id: UUID


class MetadataUpdate(Model):
    title: Annotated[str, Field(min_length=1, max_length=200)]
    description: Annotated[str, Field(max_length=4000)] = ''
    revision: Annotated[int, Field(strict=True, ge=1)]

    _title = field_validator('title')(SurveyInput.nonblank.__func__)

"""Persist completed answers and source snapshots, independently of live documents."""

from dataclasses import asdict, dataclass
from datetime import datetime

from sqlalchemy import delete, func, insert, select

from ._schema import question_history
from ._types import Source


class HistoryNotFound(LookupError):
    def __init__(self):
        super().__init__('问答历史不存在或已删除')


@dataclass(frozen=True)
class HistoryWrite:
    question: str
    answer: str
    status: str
    elapsed_ms: int
    model: dict[str, str]
    sources: tuple[Source, ...]
    trace: tuple[dict, ...]


@dataclass(frozen=True)
class HistoryRecord(HistoryWrite):
    id: int
    created_at: datetime


@dataclass(frozen=True)
class HistorySummary:
    id: int
    question: str
    status: str
    created_at: datetime
    model: dict[str, str]
    elapsed_ms: int


@dataclass(frozen=True)
class HistoryPage:
    total: int
    items: tuple[HistorySummary, ...]


def get(connection, history_id: int) -> HistoryRecord:
    row = connection.execute(select(question_history).where(question_history.c.id == history_id)).mappings().first()
    if row is None:
        raise HistoryNotFound()
    return HistoryRecord(
        **{**row, 'sources': tuple(Source(**source) for source in row['sources']), 'trace': tuple(row['trace'])},
    )


def save(connection, entry: HistoryWrite) -> HistoryRecord:
    result = connection.execute(insert(question_history).values(**asdict(entry)))
    return get(connection, result.inserted_primary_key[0])


def list_records(connection, page: int, size: int) -> HistoryPage:
    total = connection.execute(select(func.count()).select_from(question_history)).scalar_one()
    rows = connection.execute(select(
        question_history.c.id, question_history.c.question, question_history.c.status,
        question_history.c.created_at, question_history.c.model, question_history.c.elapsed_ms,
    ).order_by(question_history.c.created_at.desc(), question_history.c.id.desc())
      .offset(page * size).limit(size)).mappings()
    return HistoryPage(total, tuple(HistorySummary(**row) for row in rows))


def delete_record(connection, history_id: int) -> None:
    result = connection.execute(delete(question_history).where(question_history.c.id == history_id))
    if result.rowcount == 0:
        raise HistoryNotFound()

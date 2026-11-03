from collections.abc import Iterator
from contextlib import contextmanager
from functools import lru_cache

from sqlalchemy import event
from sqlmodel import Session, SQLModel, create_engine

from .config import get_settings


@lru_cache
def get_engine():
    url = get_settings().database_url
    kwargs = {}
    if url.startswith("sqlite"):
        kwargs["connect_args"] = {"check_same_thread": False}
        if ":///" in url and ":memory:" not in url:
            from pathlib import Path
            Path(url.split(":///", 1)[1]).parent.mkdir(parents=True, exist_ok=True)
    engine = create_engine(url, **kwargs)
    if url.startswith("sqlite"):
        @event.listens_for(engine, "connect")
        def _pragmas(conn, _):
            cur = conn.cursor()
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA foreign_keys=ON")
            cur.close()
    return engine


def init_db() -> None:
    from . import models  # noqa: F401  registra as tabelas
    SQLModel.metadata.create_all(get_engine())
    _add_missing_columns()


def _add_missing_columns() -> None:
    """Migração mínima: colunas novas dos modelos entram em tabelas que já existem.

    Só adiciona (nunca remove ou altera). Colunas JSON obrigatórias recebem '{}' ou '[]' como padrão.
    """
    from sqlalchemy import JSON, inspect, text
    engine = get_engine()
    insp = inspect(engine)
    with engine.begin() as conn:
        for table in SQLModel.metadata.sorted_tables:
            if not insp.has_table(table.name):
                continue
            existing = {c["name"] for c in insp.get_columns(table.name)}
            for col in table.columns:
                if col.name in existing:
                    continue
                ddl = f'ALTER TABLE "{table.name}" ADD COLUMN "{col.name}" {col.type.compile(engine.dialect)}'
                if not col.nullable:
                    default = None
                    if isinstance(col.type, JSON):
                        default = "'[]'" if col.name.endswith(("assets", "tags", "outputs")) else "'{}'"
                    if default is None:
                        continue  # coluna obrigatória sem padrão conhecido: exige migração manual
                    ddl += f" NOT NULL DEFAULT {default}"
                conn.execute(text(ddl))


@contextmanager
def session_scope() -> Iterator[Session]:
    with Session(get_engine()) as s:
        yield s


def get_session() -> Iterator[Session]:
    with Session(get_engine()) as s:
        yield s

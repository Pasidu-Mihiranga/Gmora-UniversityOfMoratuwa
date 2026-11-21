"""Database engine, sessions and the declarative base (DB-1 to DB-7)."""

from __future__ import annotations

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

NAMING_CONVENTION = {
    "ix": "ix_%(table_name)s_%(column_0_N_name)s",
    "uq": "uq_%(table_name)s_%(column_0_N_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING_CONVENTION)


class Database:
    """One engine per process; sessions are short-lived and owned by a UnitOfWork."""

    def __init__(self, url: str, *, pool_size: int = 10, echo: bool = False) -> None:
        self.engine: AsyncEngine = create_async_engine(
            url, pool_size=pool_size, max_overflow=pool_size, pool_pre_ping=True, echo=echo
        )
        self.sessions = async_sessionmaker(self.engine, expire_on_commit=False, class_=AsyncSession)

    async def dispose(self) -> None:
        await self.engine.dispose()

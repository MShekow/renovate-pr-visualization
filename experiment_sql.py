from datetime import datetime

from sqlalchemy import create_engine, select

from typing import List
from typing import Optional
from sqlalchemy import ForeignKey
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase, Session
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship


class Base(DeclarativeBase):
    pass


class PullRequest(Base):
    __tablename__ = "pull_request"
    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String(200))
    created_date: Mapped[datetime]
    closed_date: Mapped[Optional[datetime]]
    number: Mapped[int]
    url: Mapped[str] = mapped_column(String(200))
    dependency_updates: Mapped[List["DependencyUpdate"]] = relationship(
        back_populates="prs", cascade="all, delete-orphan"
    )


class DependencyUpdate(Base):
    __tablename__ = "dependency_update"
    id: Mapped[int] = mapped_column(primary_key=True)
    pr_id: Mapped[int] = mapped_column(ForeignKey("pull_request.id"))
    pull_request: Mapped["PullRequest"] = relationship(back_populates="dependency_updates")
    dependency_name: Mapped[str] = mapped_column(String(200))
    old_version: Mapped[str] = mapped_column(String(200))
    new_version: Mapped[str] = mapped_column(String(200))
    update_type: Mapped[str] = mapped_column(String(200))


pr = PullRequest(pr_id=123)

engine = create_engine("postgresql+psycopg2://postgres:password@localhost/postgres")
Base.metadata.drop_all(engine)
Base.metadata.create_all(engine)

session = Session(engine)

stmt = select(User).where(User.name.in_(["spongebob", "sandy"]))
i = 2

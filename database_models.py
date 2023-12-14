from datetime import datetime
from enum import Enum
from typing import List
from typing import Optional

from sqlalchemy import ForeignKey
from sqlalchemy import String
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.orm import Mapped
from sqlalchemy.orm import mapped_column
from sqlalchemy.orm import relationship


class Base(DeclarativeBase):
    pass


class PrCloseType(Enum):
    merge = "merge"
    close = "close"


class PullRequest(Base):
    __tablename__ = "pull_request"
    id: Mapped[int] = mapped_column(primary_key=True)
    repo: Mapped[str] = mapped_column(String(200))
    created_date: Mapped[datetime]
    closed_date: Mapped[Optional[datetime]]
    close_type: Mapped[Optional[PrCloseType]]
    number: Mapped[int]
    url: Mapped[str] = mapped_column(String(200))
    dependency_updates: Mapped[List["DependencyUpdate"]] = relationship(
        back_populates="pull_request", cascade="all, delete-orphan"
    )

    def __repr__(self) -> str:
        return f"PullRequest(id={self.id!r}, repo={self.repo!r}, number={self.number!r}," \
               f"created_date={self.created_date!r}, " \
               f"closed_date={self.closed_date!r}, close_type={self.close_type!r}, url={self.url!r})"


class DependencyUpdate(Base):
    __tablename__ = "dependency_update"
    id: Mapped[int] = mapped_column(primary_key=True)
    pr_id: Mapped[int] = mapped_column(ForeignKey("pull_request.id"))
    pull_request: Mapped["PullRequest"] = relationship(back_populates="dependency_updates")
    dependency_name: Mapped[str] = mapped_column(String(200))
    old_version: Mapped[str] = mapped_column(String(50))
    new_version: Mapped[str] = mapped_column(String(50))
    update_type: Mapped[str] = mapped_column(String(10))

    def __repr__(self) -> str:
        return f"DependencyUpdate(id={self.id!r}, pr={self.pull_request.number!r}," \
               f"dependency_name={self.dependency_name!r}, " \
               f"old_version={self.old_version!r}, new_version={self.new_version!r}, " \
               f"update_type={self.update_type!r})"

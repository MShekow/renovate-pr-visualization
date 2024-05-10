"""
Contains abstract definitions for client classes related to fetching PRs from an SCM.
"""

from abc import ABC, abstractmethod
from dataclasses import dataclass
from datetime import datetime
from typing import Optional


class GitCommit(ABC):
    """
    Represents a Git commit.
    """

    def __init__(self, sha: str, author_or_commit_date: datetime):
        self._sha = sha
        self._author_or_commit_date = author_or_commit_date

    @property
    def sha(self) -> str:
        return self._sha

    @property
    def author_or_commit_date(self) -> datetime:
        """
        Returns the author or commit date of this commit, whichever is available and comes later.
        """
        return self._author_or_commit_date

    @abstractmethod
    def get_tree(self) -> list[str]:
        """
        Retrieves the list of relative file names at the root-level of this commit.
        The implementation should cache the results, as this method may be called multiple times.
        """


@dataclass
class PullRequest:
    title: str
    description: str
    labels: list[str]
    created_date: datetime
    closed_date: Optional[datetime]
    merged_date: Optional[datetime]
    repo: "GitRepository"
    pr_number: int
    url: str


class NoCommitsFoundError(Exception):
    pass


class GitRepository(ABC):
    """
    Represents a Git repository.
    """

    def __init__(self, owner_and_name: str):
        self._owner_and_name = owner_and_name

    @property
    def owner_and_name(self) -> str:
        return self._owner_and_name

    @abstractmethod
    def get_commits(self, since: datetime) -> list[GitCommit]:
        """
        Retrieves the list of commits for this repository in chronological order.
        May raise a NoCommitsFoundError if no commits are found.
        """

    @abstractmethod
    def get_pull_requests(self, pr_author_username: Optional[str] = None,
                          renovate_pr_label: Optional[str] = None) -> list[PullRequest]:
        """
        Retrieves the list of ALL pull requests (including closed ones) for this repository.
        If pr_author_username is provided, only PRs created by that user are returned.
        If renovate_pr_label is provided, only PRs with that label are returned.
        At least one of pr_author_username or renovate_pr_label must be provided.
        """


class ScmClient(ABC):
    """
    A client for a Source Code Management (SCM) system, such as GitHub or GitLab.
    """

    def __init__(self, pat: str, api_base_url: Optional[str] = None):
        self._pat = pat
        self._api_base_url = api_base_url

    @abstractmethod
    def get_username(self) -> str:
        """
        Retrieves the username for the provided PAT, verifying that the PAT is valid.
        """

    @abstractmethod
    def get_repository(self, owner_and_name: str) -> GitRepository:
        """
        Retrieves the repository for the provided owner_and_name. The owner_and_name must have the format "owner/name".
        """

    @abstractmethod
    def get_repositories(self, owner_or_username: str) -> list[GitRepository]:
        """
        Retrieves the list of repositories for the provided username. owner_or_username must either be an actual
        username (format: "user:<username>") or an organization (format: "someorgname").
        """

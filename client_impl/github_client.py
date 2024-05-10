from datetime import datetime
from typing import Optional

import github
import github.AuthenticatedUser
import github.Issue
import github.PullRequest
import github.Repository
from dateutil import parser
from github.Consts import DEFAULT_BASE_URL

from abstractions import ScmClient, GitRepository, GitCommit, PullRequest


class GitHubClient(ScmClient):

    def __init__(self, pat: str, api_base_url: Optional[str] = None):
        super().__init__(pat, api_base_url)
        self._github_client = github.Github(auth=github.Auth.Token(pat), base_url=api_base_url or DEFAULT_BASE_URL)
        self._authenticated_user: Optional[github.AuthenticatedUser.AuthenticatedUser] = None

    def get_username(self) -> str:
        if not self._authenticated_user:
            self._authenticated_user = self._github_client.get_user()

        return self._authenticated_user.login

    def get_repository(self, owner_and_name: str) -> GitRepository:
        gh_repo = self._github_client.get_repo(owner_and_name)
        return GitHubRepository(owner_and_name, self._github_client, gh_repo)

    def get_repositories(self, owner_or_username: str) -> list[GitRepository]:
        if owner_or_username.startswith("user:"):
            owner_or_username = owner_or_username[5:]
            if self.get_username().lower() == owner_or_username.lower():
                sdk_function = self._authenticated_user.get_repos
                kwargs = {"type": "owner"}
            else:
                sdk_function = self._github_client.get_user(owner_or_username).get_repos
                kwargs = {}
        else:
            sdk_function = self._github_client.get_organization(owner_or_username).get_repos
            kwargs = {}

        repos: list[GitRepository] = []

        for repo in sdk_function(**kwargs):
            repos.append(GitHubRepository(repo.full_name, self._github_client, repo))

        return repos


class GitHubCommit(GitCommit):

    def __init__(self, sha: str, author_or_commit_date: datetime, gh_repo: github.Repository.Repository):
        super().__init__(sha, author_or_commit_date)
        self._gh_repo = gh_repo
        self._cached_tree: Optional[list[str]] = None

    def get_tree(self) -> list[str]:
        if self._cached_tree is not None:
            return self._cached_tree

        self._cached_tree = [tree_entry.path for tree_entry in self._gh_repo.get_git_tree(self.sha).tree]
        return self._cached_tree


class GitHubRepository(GitRepository):
    def __init__(self, owner_and_name: str, github_client: github.Github, gh_repo: github.Repository.Repository):
        super().__init__(owner_and_name)
        self._gh_repo = gh_repo
        self._github_client = github_client

    def get_commits(self, since: datetime) -> list[GitCommit]:
        gh_commits = [commit for commit in
                      self._gh_repo.get_commits(sha=self._gh_repo.default_branch, since=since)]
        gh_commits.reverse()
        commits: list[GitHubCommit] = []
        for commit in gh_commits:
            author_or_commit_date = commit.commit.author.date
            if commit.commit.committer:
                author_or_commit_date = commit.commit.committer.date
            commits.append(
                GitHubCommit(sha=commit.sha, author_or_commit_date=author_or_commit_date, gh_repo=self._gh_repo))

        return commits

    def get_pull_requests(self, pr_author_username: Optional[str] = None,
                          renovate_pr_label: Optional[str] = None) -> list[PullRequest]:
        prs: list[PullRequest] = []

        query = f"repo:{self._gh_repo.full_name} is:pr"
        if renovate_pr_label:
            query += f' label:"{renovate_pr_label}"'
        if pr_author_username:
            query += f" author:{pr_author_username}"

        for issue_pr in  self._github_client.search_issues(query=query):
            merged_date = None
            # contains something like '2024-02-18T11:33:42Z'
            merged_date_raw_str = issue_pr.pull_request.raw_data.get("merged_at")
            if merged_date_raw_str:
                merged_date = parser.parse(merged_date_raw_str)

            prs.append(
                PullRequest(title=issue_pr.title,
                            description=issue_pr.body,
                            labels=[label.name for label in issue_pr.labels],
                            created_date=issue_pr.created_at,
                            closed_date=issue_pr.closed_at,
                            merged_date=merged_date,
                            repo=self,
                            pr_number=issue_pr.number,
                            url=issue_pr.html_url)
            )

        return prs

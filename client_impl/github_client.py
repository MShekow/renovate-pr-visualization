from datetime import datetime
from typing import Optional

import github
import github.Repository
import github.AuthenticatedUser
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

        # Note: the pulls API does not seem to be affected by GitHub rate limiting. While we could use the more
        # efficient issues search API, it takes approximately as long as using the pulls API, because the issues
        # search API is rate-limited. See Git commit df4f89062cfe7af5715beebcf20a4029836dc6c3 for the variant using
        # the search API.

        for pr in self._gh_repo.get_pulls(state="all"):
            if pr_author_username is None or pr.user.login == pr_author_username:
                if renovate_pr_label is None or any(label.name == renovate_pr_label for label in pr.labels):
                    prs.append(
                        PullRequest(title=pr.title,
                                    description=pr.body,
                                    labels=[label.name for label in pr.labels],
                                    created_date=pr.created_at,
                                    closed_date=pr.closed_at,
                                    merged_date=pr.merged_at,
                                    repo=self,
                                    pr_number=pr.number,
                                    url=pr.html_url)
                    )

        return prs

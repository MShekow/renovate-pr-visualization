from datetime import datetime
from typing import Optional, List

import gitlab
import gitlab.v4.objects.projects
from gitlab.v4.objects import CurrentUser, ProjectCommit

from abstractions import ScmClient, GitRepository, GitCommit, PullRequest


class GitLabClient(ScmClient):

    def __init__(self, pat: str, api_base_url: Optional[str] = None):
        super().__init__(pat, api_base_url)
        self._gitlab_client = gitlab.Gitlab(api_base_url, private_token=pat) if api_base_url else gitlab.Gitlab(
            private_token=pat)
        self._authenticated_user: Optional[CurrentUser] = None

    def get_username(self) -> str:
        self._gitlab_client.auth()
        if not self._authenticated_user:
            self._authenticated_user = self._gitlab_client.user

        return self._authenticated_user.username

    def is_group(self, owner_or_username: str) -> bool:
        if owner_or_username.startswith("user:"):
            return True

        try:
            self._gitlab_client.groups.get(owner_or_username)
            return True
        except gitlab.exceptions.GitlabGetError as e:
            if e.response_code == 404:
                return False
            raise e

    def get_repository(self, owner_and_name: str) -> GitRepository:
        project = self._gitlab_client.projects.get(owner_and_name)
        return GitLabRepository(owner_and_name, self._gitlab_client, project)

    def get_repositories(self, owner_or_username: str) -> List[GitRepository]:
        if owner_or_username.startswith("user:"):
            owner_or_username = owner_or_username[5:]
            if self.get_username().lower() == owner_or_username.lower():
                projects = self._gitlab_client.projects.list(owned=True, get_all=True)
            else:
                user = self._gitlab_client.users.list(username=owner_or_username, get_all=True)[0]
                projects = user.projects.list(owned=True, get_all=True)
        else:
            group = self._gitlab_client.groups.get(owner_or_username)
            projects = self._get_projects_from_groups_recursive(group)

        repos: List[GitRepository] = [GitLabRepository(project.id, self._gitlab_client, project) for project in
                                      projects]

        return repos

    def _get_projects_from_groups_recursive(self, group: gitlab.v4.objects.Group) -> List[gitlab.v4.objects.Project]:
        # Convert subproject objects (stored in group.projects) to "full" GitLab Project objects, which also have
        # the mergerequests attribute
        subprojects = group.projects.list(get_all=True)
        projects: List[gitlab.v4.objects.Project] = []
        for sub_project in subprojects:
            projects.append(self._gitlab_client.projects.get(sub_project.id))

        # Recursively get projects from subgroups
        subgroups: List[gitlab.v4.objects.GroupSubgroup] = group.subgroups.list(get_all=True)
        for subgroup in subgroups:
            full_subgroup = self._gitlab_client.groups.get(subgroup.id)
            projects.extend(self._get_projects_from_groups_recursive(full_subgroup))

        return projects


class GitLabCommit(GitCommit):

    def __init__(self, sha: str, author_or_commit_date: datetime, gl_project: gitlab.v4.objects.Project):
        super().__init__(sha, author_or_commit_date)
        self._gl_project = gl_project
        self._cached_tree: Optional[List[str]] = None

    def get_tree(self) -> List[str]:
        if self._cached_tree is not None:
            return self._cached_tree

        tree = self._gl_project.repository_tree(ref=self.sha, get_all=True)
        self._cached_tree = [entry['path'] for entry in tree]
        return self._cached_tree


class GitLabRepository(GitRepository):
    def __init__(self, owner_and_name: str, gitlab_client: gitlab.Gitlab, gl_project: gitlab.v4.objects.Project):
        super().__init__(owner_and_name)
        self._gl_project = gl_project
        self._gitlab_client = gitlab_client

    def get_commits(self, since: datetime) -> List[GitCommit]:
        gl_commits: List[ProjectCommit] = self._gl_project.commits.list(since=since.isoformat(), get_all=True)
        gl_commits.reverse()
        commits: List[GitLabCommit] = [
            GitLabCommit(sha=commit.attributes['id'],
                         author_or_commit_date=datetime.fromisoformat(commit.attributes['committed_date']),
                         gl_project=self._gl_project)
            for commit in gl_commits
        ]
        return commits

    def get_pull_requests(self, pr_author_username: Optional[str] = None,
                          renovate_pr_label: Optional[str] = None,
                          ignore_pr_labels: Optional[list[str]] = None) -> List[PullRequest]:
        prs: List[PullRequest] = []
        mr_params = {'state': 'all', 'get_all': True}
        if pr_author_username:
            mr_params['author_username'] = pr_author_username

        for mr in self._gl_project.mergerequests.list(**mr_params):
            if renovate_pr_label is None or renovate_pr_label in [label for label in mr.labels]:
                if ignore_pr_labels is None or not any(label in ignore_pr_labels for label in mr.labels):
                    prs.append(
                        PullRequest(
                            title=mr.title,
                            description=mr.description,
                            labels=mr.labels,
                            created_date=datetime.fromisoformat(mr.created_at),
                            closed_date=datetime.fromisoformat(mr.closed_at) if mr.closed_at else None,
                            merged_date=datetime.fromisoformat(mr.merged_at) if mr.merged_at else None,
                            repo=self,
                            pr_number=mr.iid,
                            url=mr.web_url
                        )
                    )

        return prs

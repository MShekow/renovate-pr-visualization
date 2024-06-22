from enum import StrEnum
from typing import Optional

from abstractions import ScmClient
from client_impl.github_client import GitHubClient
from client_impl.gitlab_client import GitLabClient


class ScmClientImpl(StrEnum):
    GitHub = "github"
    GitLab = "gitlab"


def scm_client_factory(scm_provider: ScmClientImpl, pat: str, api_base_url: Optional[str] = None) -> ScmClient:
    if scm_provider == ScmClientImpl.GitHub:
        return GitHubClient(pat, api_base_url)
    elif scm_provider == ScmClientImpl.GitLab:
        return GitLabClient(pat, api_base_url)
    else:
        raise ValueError(f"Unknown SCM provider: {scm_provider}")

from enum import StrEnum
from typing import Optional

from abstractions import ScmClient
from client_impl.github_client import GitHubClient


class ScmClientImpl(StrEnum):
    GitHub = "github"


def scm_client_factory(scm_provider: ScmClientImpl, pat: str, api_base_url: Optional[str] = None) -> ScmClient:
    if scm_provider == ScmClientImpl.GitHub:
        return GitHubClient(pat, api_base_url)
    else:
        raise ValueError(f"Unknown SCM provider: {scm_provider}")

import os
from dataclasses import dataclass
from typing import Optional, Tuple

from github import Github, Auth
from sqlalchemy import create_engine


@dataclass
class Configuration:
    database_with_credentials: str
    github_base_url: Optional[str]
    github_pat: str
    github_repos: list[Tuple[str, str]]  # list of (owner, repo) tuples, after dynamically expanding repos
    renovate_pr_label: str
    renovate_pr_security_label: str
    renovate_github_user: Optional[str]
    detect_multiple_major_updates: bool
    renovate_onboarding_pr_regex: Optional[str]
    renovate_onboarding_sampling_max_weeks: int
    renovate_onboarding_sampling_interval_weeks: int


def load_and_verify_configuration() -> Configuration:
    """
    Checks whether all required environment variables are present and checks the PostgreSQL database connection as
    well as the GitHub API connection.

    See the .env.example file for the documentation of the environment variables.
    """
    configuration = Configuration(
        database_with_credentials=os.getenv("DATABASE_WITH_CREDS"),
        github_base_url=os.getenv("GITHUB_BASE_URL", "https://api.github.com"),
        github_pat=os.getenv("GITHUB_PAT"),
        github_repos=[],  # will be filled below
        renovate_pr_label=os.getenv("RENOVATE_PR_LABEL"),
        renovate_pr_security_label=os.getenv("RENOVATE_PR_SECURITY_LABEL"),
        renovate_github_user=os.getenv("RENOVATE_USER"),
        detect_multiple_major_updates=os.getenv("RENOVATE_DETECT_MULTIPLE_MAJOR", "false") == "true",
        renovate_onboarding_pr_regex=os.getenv("RENOVATE_ONBOARDING_PR_REGEX", r"^Configure Renovate"),
        renovate_onboarding_sampling_max_weeks=int(os.getenv("RENOVATE_ONBOARDING_STATUS_SAMPLING_MAX_PAST_WEEKS")),
        renovate_onboarding_sampling_interval_weeks=int(
            os.getenv("RENOVATE_ONBOARDING_STATUS_SAMPLING_INTERVAL_IN_WEEKS")),
    )

    if not configuration.database_with_credentials:
        raise ValueError("Environment variable DATABASE_WITH_CREDS must be set "
                         "to '<username>:<password>@<host>[:<port>]/<database-name>'")
    if not configuration.github_pat:
        raise ValueError("Environment variable GITHUB_PAT must be set to a valid GitHub personal access token")
    github_repos_and_owners = os.getenv("GITHUB_REPOS").split(",")
    if not github_repos_and_owners:
        raise ValueError("Environment variable GITHUB_REPOS must be set to a comma-separated list of GitHub "
                         "repositories/owners, where each entry has the form '<owner>/<repo>' or '<owner>'")
    if not configuration.renovate_pr_label and not configuration.renovate_github_user:
        raise ValueError("At least one of the environment variables RENOVATE_PR_LABEL or RENOVATE_USER must be set")
    if not configuration.renovate_pr_security_label:
        raise ValueError("Environment variable RENOVATE_PR_SECURITY_LABEL must be set to the label that Renovate "
                         "uses to mark security PRs (e.g. 'security')")
    if (configuration.renovate_onboarding_sampling_max_weeks <= 0
            or configuration.renovate_onboarding_sampling_max_weeks <= 0):
        raise ValueError("Environment variables RENOVATE_ONBOARDING_STATUS_SAMPLING_MAX_PAST_WEEKS and "
                         "RENOVATE_ONBOARDING_STATUS_SAMPLING_INTERVAL_IN_WEEKS must be set to positive numbers")

    # Check the PostgreSQL configuration
    engine = create_engine(f"postgresql+psycopg2://{configuration.database_with_credentials}")
    connection = engine.connect()
    connection.close()

    # Check the GitHub API configuration
    github = Github(auth=Auth.Token(configuration.github_pat), base_url=configuration.github_base_url)
    authenticated_user = github.get_user()

    # Verify that all specified repositories exist, and also expand the repositories of organizations
    for owner_or_repo in github_repos_and_owners:
        if '/' in owner_or_repo:
            owner, repo = owner_or_repo.split("/")
            try:
                github.get_repo(owner_or_repo)
            except Exception as e:
                raise ValueError(f"Unable to find repository {owner_or_repo}, aborting: {e}")
            else:
                configuration.github_repos.append((owner, repo))
        else:
            if owner_or_repo.startswith("user:"):
                owner_or_repo = owner_or_repo[5:]
                if authenticated_user.login.lower() == owner_or_repo:
                    sdk_function = authenticated_user.get_repos
                    kwargs = {"type": "owner"}
                else:
                    sdk_function = github.get_user(owner_or_repo).get_repos
                    kwargs = {}
            else:
                sdk_function = github.get_organization(owner_or_repo).get_repos
                kwargs = {}

            for repo in sdk_function(**kwargs):
                configuration.github_repos.append((owner_or_repo, repo.name))

    # Verify that there are no duplicates in configuration.github_repos (could happen if the user provides both
    # "some-owner" AND "some-owner/some-repo" in the environment variable GITHUB_REPOS)
    if len(configuration.github_repos) != len(set(configuration.github_repos)):
        raise ValueError(f"There are duplicate repositories in the configuration, "
                         f"aborting: {configuration.github_repos}")

    return configuration

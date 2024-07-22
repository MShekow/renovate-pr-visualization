import os
from dataclasses import dataclass
from typing import Optional

from sqlalchemy import create_engine

from abstractions import GitRepository
from client_impl import ScmClientImpl, scm_client_factory


@dataclass
class Configuration:
    scm_client_impl: ScmClientImpl
    database_with_credentials: str
    api_base_url: Optional[str]
    pat: str
    repos: list[GitRepository]
    renovate_pr_label: str
    renovate_pr_security_label: str
    renovate_scm_user: Optional[str]
    detect_multiple_major_updates: bool
    renovate_onboarding_pr_regex: Optional[str]
    renovate_onboarding_sampling_max_weeks: int
    renovate_onboarding_sampling_interval_weeks: int


def load_and_verify_configuration() -> Configuration:
    """
    Checks whether all required environment variables are present and checks the PostgreSQL database connection as
    well as the SCM API connection.

    See the .env.example file for the documentation of the environment variables.
    """
    configuration = Configuration(
        scm_client_impl=ScmClientImpl(os.getenv("SCM_PROVIDER")),
        database_with_credentials=os.getenv("DATABASE_WITH_CREDS"),
        api_base_url=os.getenv("API_BASE_URL", None),
        pat=os.getenv("PAT"),
        repos=[],  # will be filled below
        renovate_pr_label=os.getenv("RENOVATE_PR_LABEL"),
        renovate_pr_security_label=os.getenv("RENOVATE_PR_SECURITY_LABEL"),
        renovate_scm_user=os.getenv("RENOVATE_USER"),
        detect_multiple_major_updates=os.getenv("RENOVATE_DETECT_MULTIPLE_MAJOR", "false") == "true",
        renovate_onboarding_pr_regex=os.getenv("RENOVATE_ONBOARDING_PR_REGEX") or r"^Configure Renovate",
        renovate_onboarding_sampling_max_weeks=int(
            os.getenv("RENOVATE_ONBOARDING_STATUS_SAMPLING_INTERVAL_MAX_PAST_WEEKS")),
        renovate_onboarding_sampling_interval_weeks=int(
            os.getenv("RENOVATE_ONBOARDING_STATUS_SAMPLING_INTERVAL_IN_WEEKS")),
    )

    if not configuration.database_with_credentials:
        raise ValueError("Environment variable DATABASE_WITH_CREDS must be set "
                         "to '<username>:<password>@<host>[:<port>]/<database-name>'")
    if not configuration.pat:
        raise ValueError("Environment variable PAT must be set to a valid personal access token")
    repos_and_owners = os.getenv("REPOS").split(",")
    if not repos_and_owners:
        raise ValueError("Environment variable REPOS must be set to a comma-separated list of "
                         "repositories/owners, where each entry has the form '<owner>/<repo>' or '<owner>'")
    if not configuration.renovate_pr_label and not configuration.renovate_scm_user:
        raise ValueError("At least one of the environment variables RENOVATE_PR_LABEL or RENOVATE_USER must be set")
    if not configuration.renovate_pr_security_label:
        raise ValueError("Environment variable RENOVATE_PR_SECURITY_LABEL must be set to the label that Renovate "
                         "uses to mark security PRs (e.g. 'security')")
    if (configuration.renovate_onboarding_sampling_max_weeks <= 0
            or configuration.renovate_onboarding_sampling_max_weeks <= 0):
        raise ValueError("Environment variables RENOVATE_ONBOARDING_STATUS_SAMPLING_INTERVAL_MAX_PAST_WEEKS and "
                         "RENOVATE_ONBOARDING_STATUS_SAMPLING_INTERVAL_IN_WEEKS must be set to positive numbers")

    # Check the PostgreSQL configuration
    engine = create_engine(f"postgresql+psycopg2://{configuration.database_with_credentials}")
    connection = engine.connect()
    connection.close()

    # Check the API configuration
    scm_client = scm_client_factory(configuration.scm_client_impl, configuration.pat, configuration.api_base_url)
    scm_client.get_username()  # only called to verify that the PAT is valid, we don't actually need the username

    # Verify that all specified repositories exist, and also expand the repositories of organizations or users
    for owner_or_repo in repos_and_owners:
        if scm_client.is_group(owner_or_repo):
            try:
                configuration.repos.extend(scm_client.get_repositories(owner_or_repo))
            except Exception as e:
                raise ValueError(f"Unable to find group/organization {owner_or_repo}, aborting: {e}")
        else:
            try:
                configuration.repos.append(scm_client.get_repository(owner_or_repo))
            except Exception as e:
                raise ValueError(f"Unable to find repository {owner_or_repo}, aborting: {e}")

    # Verify that there are no duplicates in configuration.repos (could happen if the user provides both
    # "some-owner" AND "some-owner/some-repo" in the environment variable REPOS)
    if len(configuration.repos) != len(set(configuration.repos)):
        raise ValueError(f"There are duplicate repositories in the configuration, "
                         f"aborting: {configuration.repos}")

    return configuration

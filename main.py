"""
This script fills the provided PostgreSQL database with information about Renovate PRs and repository onboarding
statuses.
"""
import os
import re
from dataclasses import dataclass
from typing import Tuple, Optional

from githubkit import GitHub
from githubkit.rest import PullRequestSimple
from marko.ext.gfm import gfm
from marko.ext.gfm.elements import Table
from marko.inline import Link, InlineElement, CodeSpan, RawText
from packaging.version import Version, InvalidVersion
from sqlalchemy import create_engine
from sqlalchemy.orm import Session

from database_models import PullRequest, PrCloseType, DependencyUpdate, Base, RepositoryOnboardingStatus, OnboardingType


@dataclass
class Configuration:
    database_with_credentials: str
    github_base_url: Optional[str]
    github_pat: str
    github_repos: list[Tuple[str, str]]  # list of (owner, repo) tuples, after dynamically expanding repos
    renovate_pr_label: str
    renovate_pr_security_label: str
    renovate_github_user: Optional[str]
    renovate_onboarding_pr_regex: Optional[str]


def system_check() -> Configuration:
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
        renovate_onboarding_pr_regex=os.getenv("RENOVATE_ONBOARDING_PR_REGEX", r"^Configure Renovate")
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

    # Check the PostgreSQL configuration
    engine = create_engine(f"postgresql+psycopg2://{configuration.database_with_credentials}")
    connection = engine.connect()
    connection.close()

    # Check the GitHub API configuration
    github = GitHub(configuration.github_pat, base_url=configuration.github_base_url)
    github.rest.users.get_authenticated()

    # Verify that all specified repositories exist, and also expand the repositories of organizations
    for owner_or_repo in github_repos_and_owners:
        if '/' in owner_or_repo:
            owner, repo = owner_or_repo.split("/")
            try:
                github.rest.repos.get(owner=owner, repo=repo)
            except Exception as e:
                raise ValueError(f"Unable to find repository {owner_or_repo}, aborting: {e}")
            else:
                configuration.github_repos.append((owner, repo))
        else:
            repos = github.rest.repos.list_for_org(org=owner_or_repo).parsed_data
            for repo in repos:
                configuration.github_repos.append((owner_or_repo, repo.name))

    # Verify that there are no duplicates in configuration.github_repos (could happen if the user provides both
    # "some-owner" AND "some-owner/some-repo" in the environment variable GITHUB_REPOS)
    if len(configuration.github_repos) != len(set(configuration.github_repos)):
        raise ValueError(f"There are duplicate repositories in the configuration, "
                         f"aborting: {configuration.github_repos}")

    return configuration


def has_relevant_pr_title(renovate_pr_title: str) -> bool:
    """
    For the given title of a PR created by Renovate, return whether this PR should really be considered.
    Returns False for those PRs that someone from the dev team manually "deleted" (without actually deleting it),
    which involved renaming the title.

    A Renovate PR is relevant if its title matches one of the following patterns (ignoring an possibly-existing
    " - autoclosed" postfix):

    Update dependency <dependency> to v<version>
        Used when there is only one dependency that is updated, e.g. "Update dependency @types/node to v14.14.3"
    Update <group name with possible spaces>
        Used when there are multiple dependencies with minor upgrades, e.g one from 8.1->8.2 and one from 5.1->5.2
    Update <group name with possible spaces> (major)
        Used when there are multiple dependencies with major upgrades, e.g one from 8->9 and one from 5->6
    Update <group name with possible spaces> to v<version>
        Used when there are multiple dependencies which ALL have the SAME updates (old -> new), on minor/patch level
    Update <group name with possible spaces> to v<version> (major)
        Used when there are multiple dependencies which ALL have the SAME updates (old -> new), on major level

    where <version> is either a single number indicating major updates (e.g. "2" or "2023") or a full version number
    (e.g. "1.2.3" or "2023.1.2"). <version> may also have a " [SECURITY]" postfix.

    Because "Update <group name with possible spaces>" is the most generic pattern of all papterns,
    the implementation is very simple.
    """
    return renovate_pr_title.startswith("Update ")


def has_label(pr: PullRequestSimple, label: str) -> bool:
    """
    Returns whether the given PR has the specified label.
    """
    for pr_label in pr.labels:
        if pr_label.name == label:
            return True
    return False


def get_renovate_prs(config: Configuration) -> list[PullRequestSimple]:
    """
    Returns all those PRs from the specified repositories that were created by Renovate and that have a relevant title.
    """
    github = GitHub(config.github_pat, base_url=config.github_base_url)

    renovate_prs = []
    for owner, repo in config.github_repos:
        for pr in github.paginate(github.rest.pulls.list, owner=owner, repo=repo, state="all"):
            if config.renovate_pr_label is None or has_label(pr, config.renovate_pr_label):
                if config.renovate_github_user is None or pr.user.login == config.renovate_github_user:
                    if has_relevant_pr_title(pr.title):
                        renovate_prs.append(pr)

    return renovate_prs


MAJOR_MINOR_PATCH_REGEX = re.compile(r"\d+(?:\.\d+){0,2}")


def clean_version(version: str) -> str:
    """
    Cleans the given version string, removing any unexpected characters, so that Python's packaging.Version class can
    parse it successfully.

    Examples for patterns that can successfully be parsed:
    - "^1.2.3" -> "1.2.3"
    - "~1.2.3" -> "1.2.3"
    - "1.2.3-alpha.1" -> "1.2.3"
    - "stable-v1.2.3" -> "1.2.3"
    - "1.x" -> "1.0" (to handle version updates such as "1.x -> 2.x")
    """
    version = version.replace("x", "0")
    if match := MAJOR_MINOR_PATCH_REGEX.search(version):
        version = match.group()
        return version
    else:
        raise ValueError(f"Unable to parse version {version!r}, regex for major/minor/patch did not find any matches")


def add_dependency_updates(database_pr: PullRequest, renovate_pr: PullRequestSimple, config: Configuration) -> None:
    """
    Parses the MarkDown body of the given Renovate PR and adds all dependency updates to the given database PR.
    """
    # Note: use the GitHub-flavored Markdown parser, which supports parsing tables
    markdown_document = gfm.parse(renovate_pr.body)

    dependencies_table: Optional[Table] = None
    for child in markdown_document.children[:4]:
        if isinstance(child, Table):
            dependencies_table = child
            break
    if not dependencies_table:
        raise ValueError(f"Could not find dependencies table in PR {renovate_pr.html_url}")

    # Determine the columns, because they are not always at the same position:
    # The first column is always the "Package" column which contains the package name.
    # The other relevant column is called "Change" and it may appear as second column, or e.g. as fourth column, having
    # the content "<old version> -> <new version>"

    # Verify that the table has the "Package" column
    if dependencies_table.head.children[0].children[0].children != "Package":
        raise ValueError(f"Package column is missing in dependencies table in PR {renovate_pr.html_url}")

    # Find the "Change" column that contains the old and new version
    change_column_index = -1
    for i, column in enumerate(dependencies_table.head.children):
        if column.children[0].children == "Change":
            change_column_index = i
            break

    if change_column_index == -1:
        raise ValueError(f"Change column is missing in dependencies table in PR {renovate_pr.html_url}")

    # Parse the dependency updates table
    for row in dependencies_table.children[1:]:  # note: row 0 is the header row
        # The cell content of the "Package" column may contain the package name directly (either as string, or wrapped
        # in a link)
        if len(row.children[0].children) == 1:
            if isinstance(row.children[0].children[0], Link):
                dependency_name = row.children[0].children[0].children[0].children
            else:
                dependency_name = row.children[0].children[0].children
        # Alternatively, the "Package" column contains a list of items, the first one being the dependency wrapped
        # in a Link, followed by other elements: "(", "<link to source>", ")"
        else:
            if not isinstance(row.children[0].children[0], Link):
                raise ValueError(f"Unable to parse dependency table: expected Link, but got {row.children[0]!r} "
                                 f"for PR {renovate_pr.html_url}")
            dependency_name = row.children[0].children[0].children[0].children

        if type(dependency_name) != str:
            raise ValueError(f"Unable to parse dependency table: dependency name is not a string: "
                             f"{dependency_name!r}")

        # The cell content of the "Change" column might contain a Link or not, so we either have
        # Link(CodeSpan(oldversion), RawText(" -> "), CodeSpan(newversion)), or
        # CodeSpan(oldversion), RawText(" -> "), CodeSpan(newversion)
        old_and_new_version_sequence: list[InlineElement] = row.children[change_column_index].children
        if isinstance(row.children[change_column_index].children[0], Link):
            old_and_new_version_sequence = row.children[change_column_index].children[0].children
        is_valid_version_sequence = len(old_and_new_version_sequence) == 3 \
                                    and isinstance(old_and_new_version_sequence[0], CodeSpan) \
                                    and isinstance(old_and_new_version_sequence[1], RawText) \
                                    and isinstance(old_and_new_version_sequence[2], CodeSpan) \
                                    and old_and_new_version_sequence[1].children == " -> "

        if not is_valid_version_sequence:
            raise ValueError(f"Unable to parse dependency table: expected "
                             f"Link(CodeSpan(oldversion), RawText(\" -> \"), CodeSpan(newversion)), or "
                             f"CodeSpan(oldversion), RawText(\" -> \"), CodeSpan(newversion), "
                             f"but got {old_and_new_version_sequence!r} for PR {renovate_pr.html_url}")
        old_version_str = old_and_new_version_sequence[0].children
        new_version_str = old_and_new_version_sequence[2].children

        if (type(old_version_str), type(new_version_str)) != (str, str):
            raise ValueError(f"Unable to parse dependency table: old/new versions are not strings: "
                             f"{old_version_str!r}, {new_version_str!r}")

        try:
            old_version = Version(clean_version(old_version_str))
            new_version = Version(clean_version(new_version_str))
        except (InvalidVersion, ValueError) as e:
            raise ValueError(f"Unable to parse old/new versions '{old_version_str}' / '{new_version_str}' for "
                             f"dependency {dependency_name}: {e}") from None

        if has_label(renovate_pr, config.renovate_pr_security_label):
            update_type = "security"
        elif old_version.major != new_version.major:
            update_type = "major"
        elif old_version.minor != new_version.minor:
            update_type = "minor"
        else:
            update_type = "patch"

        database_pr.dependency_updates.append(
            DependencyUpdate(
                dependency_name=dependency_name,
                old_version=old_version_str,
                new_version=new_version_str,
                update_type=update_type,
            )
        )


def get_database_entities(renovate_prs: list[PullRequestSimple]) -> list[PullRequest]:
    """
    Creates the database entities (PullRequest and DependencyUpdate) by parsing the provided PRs.
    """
    database_prs: list[PullRequest] = []
    for renovate_pr in renovate_prs:
        closed_date = renovate_pr.closed_at or renovate_pr.merged_at
        if renovate_pr.merged_at:
            close_type = PrCloseType.merge
        elif renovate_pr.closed_at:
            close_type = PrCloseType.close
        else:
            close_type = None

        database_pr = PullRequest(
            repo=renovate_pr.base.repo.full_name,
            created_date=renovate_pr.created_at,
            closed_date=closed_date,
            close_type=close_type,
            number=renovate_pr.number,
            url=renovate_pr.html_url,
        )

        try:
            add_dependency_updates(database_pr, renovate_pr, configuration)
        except ValueError as e:
            print(f"Warning: skipping PR {renovate_pr.html_url}: {e}")
            continue

        database_prs.append(database_pr)

    return database_prs


def save_database_entities(database_prs: list[PullRequest],
                           database_onboarding_statuses: list[RepositoryOnboardingStatus],
                           configuration: Configuration) -> None:
    """
    Saves the given database entities to the PostgreSQL database, clearing all old records.
    """
    engine = create_engine(f"postgresql+psycopg2://{configuration.database_with_credentials}")
    Base.metadata.drop_all(engine)
    Base.metadata.create_all(engine)
    with Session(engine) as session:
        session.add_all(database_prs)
        session.add_all(database_onboarding_statuses)
        session.commit()


def get_full_repository_name_from_pr_url(pr_url: str) -> str:
    """
    Extracts '{owner}/{repo}' from pr_url, which has the form 'https://<base-url>/repos/{owner}/{repo}/pulls/<number>'.
    """
    return pr_url.split("/repos/")[1].split("/pulls/")[0]


def get_repository_contains_renovate_json_file(github_client: GitHub, owner: str, repo: str) -> bool:
    """
    Returns whether the given repository contains a `renovate.json[5]` file in the root of the repo's default branch.
    """
    repo_default_branch = github_client.rest.repos.get(owner=owner, repo=repo).parsed_data.default_branch
    git_tree = github_client.rest.git.get_tree(owner=owner, repo=repo, tree_sha=repo_default_branch).parsed_data
    for tree_entry in git_tree.tree:
        if tree_entry.path in ["renovate.json", "renovate.json5"]:
            return True
    return False


def has_renovate_onboarding_pr(github_client: GitHub, config: Configuration, owner: str, repo: str) -> bool:
    """
    Returns True if there is an open Renovate Onboarding PR, which adds a `renovate.json[5]` file to the root of the
    repo's default branch.
    """
    onboarding_title_regex = re.compile(configuration.renovate_onboarding_pr_regex)
    for pr in github_client.paginate(github_client.rest.pulls.list, owner=owner, repo=repo, state="open"):
        if config.renovate_github_user is None or pr.user.login == config.renovate_github_user:
            if onboarding_title_regex.search(pr.title):
                return True
    return False


def get_repository_onboarding_status(renovate_prs: list[PullRequestSimple],
                                     config: Configuration) -> list[RepositoryOnboardingStatus]:
    """
    Extracts the RepositoryOnboardingStatus database entities from the provided PRs.

    The onboarding status of a repository is defined as follows:
    - "onboarded": if there is a `renovate.json[5]` file in the root of the repo's default branch
    - "onboarding": if there is a PR open that adds a `renovate.json[5]` file in the root of the repo's default branch
    - "disabled": if both "onboarded" and "onboarding" are false
    """
    github = GitHub(configuration.github_pat, base_url=configuration.github_base_url)
    onboarding_statuses: list[RepositoryOnboardingStatus] = []
    for owner, repo in config.github_repos:
        has_renovate_json_file = get_repository_contains_renovate_json_file(github, owner, repo)
        onboarding_status = OnboardingType.onboarded if has_renovate_json_file else OnboardingType.disabled
        if has_renovate_onboarding_pr(github, config, owner, repo):
            onboarding_status = OnboardingType.in_progress

        onboarding_statuses.append(
            RepositoryOnboardingStatus(
                repo=f"{owner}/{repo}",
                onboarded=onboarding_status,
            )
        )

    return onboarding_statuses


if __name__ == '__main__':
    configuration = system_check()
    print("System check successful, starting to fetch Renovate PRs (this may take a few minutes) ...")
    renovate_prs = get_renovate_prs(configuration)
    print(f"Found {len(renovate_prs)} Renovate PRs, converting them to them to database entities ...")
    database_prs = get_database_entities(renovate_prs)
    print("Fetching repository onboarding statuses ...")
    database_onboarding_statuses = get_repository_onboarding_status(renovate_prs, configuration)
    print("Storing database entities in the database (deleting old entries) ...")
    save_database_entities(database_prs, database_onboarding_statuses, configuration)
    print("Finished importing data successfully")

"""
This script fills the provided PostgreSQL database with information about Renovate PRs and repository onboarding
statuses.
"""
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Optional

from marko.ext.gfm import gfm
from marko.ext.gfm.elements import Table
from marko.inline import Link, InlineElement, CodeSpan, RawText
from packaging.version import Version, InvalidVersion
from sqlalchemy import create_engine
from sqlalchemy.orm import Session
from tqdm import tqdm

from abstractions import PullRequest, GitRepository, NoCommitsFoundError, GitCommit
from conf import Configuration
from database_models import PullRequest as PullRequestDb, PrCloseType, DependencyUpdate, Base, \
    RepositoryOnboardingStatus, OnboardingType, DependencyUpdateType

PR_TITLE_REGEX = re.compile(r"^(chore|fix)\(deps\): (bump|update) ")


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

    If the repository mainly used semantic commit messages, then the PR title may also have the following patterns:
    chore(deps): bump <dependency> from <version> to <version>
    chore(deps): update <dependency> to <version>
    fix(deps): update <dependency> to <version>

    where <version> is either a single number indicating major updates (e.g. "2" or "2023") or a full version number
    (e.g. "1.2.3" or "2023.1.2"). <version> may also have a " [SECURITY]" postfix.
    """
    if renovate_pr_title.startswith("Update "):
        return True
    if PR_TITLE_REGEX.match(renovate_pr_title):
        return True
    return False


@dataclass
class RenovatePrs:
    dependency_prs: list[PullRequest] = field(default_factory=list)
    onboarding_prs: list[PullRequest] = field(default_factory=list)


def get_renovate_prs(config: Configuration) -> RenovatePrs:
    """
    Returns all those PRs from the specified repositories that were created by Renovate and that have a relevant title.
    """
    onboarding_title_regex = re.compile(config.renovate_onboarding_pr_regex)

    renovate_prs = RenovatePrs()

    iterator = tqdm(config.repos, ncols=80)
    for git_repo in iterator:
        for pr in git_repo.get_pull_requests(pr_author_username=config.renovate_scm_user,
                                             renovate_pr_label=config.renovate_pr_label,
                                             ignore_pr_labels=config.ignore_pr_labels):
            if onboarding_title_regex.search(pr.title):
                renovate_prs.onboarding_prs.append(pr)
                continue

            if not config.renovate_pr_label or config.renovate_pr_label in pr.labels:
                if has_relevant_pr_title(pr.title):
                    renovate_prs.dependency_prs.append(pr)

        # Work around issue https://github.com/tqdm/tqdm/issues/771
        if Path("/.dockerenv").exists():
            print(iterator)

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


DIGEST_REGEX = re.compile(r"^[a-f0-9]{7,40}$")


def is_digest_version(version: str) -> bool:
    """
    Returns True if the given version looks like a digest, e.g. a (short) Git hash, or a (Docker) SHA256 hash.
    """
    return DIGEST_REGEX.match(version) is not None


def parse_dependency_updates(renovate_pr: PullRequest,
                             config: Configuration) -> list[DependencyUpdate]:
    """
    Parses the MarkDown body of the given Renovate PR and returns all dependency updates.
    """
    dependency_updates = []
    # Note: use the GitHub-flavored Markdown parser, which supports parsing tables
    markdown_document = gfm.parse(renovate_pr.description)

    dependencies_table: Optional[Table] = None
    for child in markdown_document.children:
        if isinstance(child, Table):
            dependencies_table = child
            break
    if not dependencies_table:
        raise ValueError(f"Could not find dependencies table in PR {renovate_pr.url}")

    # Determine the columns, because they are not always at the same position:
    # The first column is always the "Package" column which contains the package name.
    # The other relevant column is called "Change" and it may appear as second column, or e.g. as fourth column, having
    # the content "<old version> -> <new version>"

    # Verify that the table has the "Package" column
    if dependencies_table.head.children[0].children[0].children != "Package":
        raise ValueError(f"Package column is missing in dependencies table in PR {renovate_pr.url}")

    # Find the "Change" column that contains the old and new version
    change_column_index = -1
    for i, column in enumerate(dependencies_table.head.children):
        if column.children[0].children == "Change":
            change_column_index = i
            break

    if change_column_index == -1:
        raise ValueError(f"Change column is missing in dependencies table in PR {renovate_pr.url}")

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
                                 f"for PR {renovate_pr.url}")
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
                             f"but got {old_and_new_version_sequence!r} for PR {renovate_pr.url}")
        old_version_str = old_and_new_version_sequence[0].children
        new_version_str = old_and_new_version_sequence[2].children

        if (type(old_version_str), type(new_version_str)) != (str, str):
            raise ValueError(f"Unable to parse dependency table: old/new versions are not strings: "
                             f"{old_version_str!r}, {new_version_str!r}")

        if is_digest_version(old_version_str):
            update_type = DependencyUpdateType.digest
        else:
            try:
                old_version = Version(clean_version(old_version_str))
                new_version = Version(clean_version(new_version_str))
            except (InvalidVersion, ValueError) as e:
                raise ValueError(f"Unable to parse old/new versions '{old_version_str}' / '{new_version_str}' for "
                                 f"dependency {dependency_name}: {e}") from None

            if config.renovate_pr_security_label in renovate_pr.labels:
                update_type = DependencyUpdateType.security
            elif old_version.major != new_version.major:
                update_type = DependencyUpdateType.major
                if config.detect_multiple_major_updates and (new_version.major - old_version.major) > 1:
                    update_type = DependencyUpdateType.multiple_major
            elif old_version.minor != new_version.minor:
                update_type = DependencyUpdateType.minor
            else:
                update_type = DependencyUpdateType.patch

        dependency_updates.append(DependencyUpdate(
            dependency_name=dependency_name,
            old_version=old_version_str,
            new_version=new_version_str,
            update_type=update_type))

    return dependency_updates


def get_database_entities(renovate_prs: list[PullRequest], config: Configuration) -> list[PullRequestDb]:
    """
    Creates the database entities (PullRequest and DependencyUpdate) by parsing the provided PRs.
    """
    database_prs: list[PullRequestDb] = []
    for renovate_pr in renovate_prs:
        closed_date = renovate_pr.closed_date or renovate_pr.merged_date
        if renovate_pr.merged_date:
            close_type = PrCloseType.merge
        elif renovate_pr.closed_date:
            close_type = PrCloseType.close
        else:
            close_type = None

        database_pr = PullRequestDb(
            repo=renovate_pr.repo.owner_and_name,
            created_date=renovate_pr.created_date,
            closed_date=closed_date,
            close_type=close_type,
            number=renovate_pr.pr_number,
            url=renovate_pr.url,
        )

        try:
            dependency_updates = parse_dependency_updates(renovate_pr, config)
            database_pr.dependency_updates.extend(dependency_updates)
        except ValueError as e:
            print(f"Warning: skipping PR {renovate_pr.url}: {e}")
            continue

        database_prs.append(database_pr)

    return database_prs


def save_database_entities(database_prs: list[PullRequestDb],
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


class GitCommitHelper:
    def __init__(self, git_repository: GitRepository, cutoff_date: datetime):
        try:
            self._commits = git_repository.get_commits(since=cutoff_date)
        except NoCommitsFoundError:
            # Repository might still be empty, or the default branch might have been renamed
            self._commits: list[GitCommit] = []

    def _get_closest_commit(self, sample_datetime: datetime) -> Optional[GitCommit]:
        """
        Returns the closest commit to the given datetime.
        """
        if not self._commits:
            return None
        closest_commit = self._commits[0]
        for commit in self._commits[1:]:
            if commit.author_or_commit_date <= sample_datetime:
                closest_commit = commit
            else:
                break

        return closest_commit

    def contains_renovate_json_file(self, sample_datetime: datetime) -> bool:
        closest_commit = self._get_closest_commit(sample_datetime)
        if not closest_commit:
            return False

        for tree_entry in closest_commit.get_tree():
            if tree_entry in ["renovate.json", "renovate.json5"]:
                return True
        return False


def has_renovate_onboarding_pr(onboarding_prs: list[PullRequest], sample_datetime: datetime,
                               sampling_interval_weeks: int) -> bool:
    """
    Returns True if there is an onboarding PR that has been created at/before sample_datetime and that was still
    open after the sampling interval.
    """
    for pr in onboarding_prs:
        if pr.created_date <= sample_datetime:
            closed_date = pr.closed_date or pr.merged_date
            if closed_date is None or closed_date > sample_datetime + timedelta(weeks=sampling_interval_weeks):
                return True
    return False


def get_sampling_dates(config: Configuration) -> list[datetime]:
    """
    Returns a list of Monday (8 AM UTC) datetimes, spread out in regular intervals (see
    config.renovate_onboarding_sampling_interval_weeks), starting from
    <Monday of this week> - <config.renovate_onboarding_sampling_max_weeks> weeks, until <Monday of this week>
    """
    # get monday 8 AM of the current week
    now = datetime.now(timezone.utc)
    days_to_subtract = (now.weekday()) % 7
    monday = now - timedelta(days=days_to_subtract)
    monday_8am = monday.replace(hour=8, minute=0, second=0, microsecond=0)

    sampling_dates = [monday_8am]

    for i in range(config.renovate_onboarding_sampling_max_weeks // config.renovate_onboarding_sampling_interval_weeks):
        sampling_dates.append(
            monday_8am - timedelta(weeks=(i + 1) * config.renovate_onboarding_sampling_interval_weeks))

    sampling_dates.reverse()
    return sampling_dates


def get_repository_onboarding_status(onboarding_prs: list[PullRequest],
                                     config: Configuration) -> list[RepositoryOnboardingStatus]:
    """
    Extracts the RepositoryOnboardingStatus database entities for the provided repositories, sampling them in regular
    intervals.

    The onboarding status of a repository (at a specific point in time) is defined as follows:
    - "onboarded": if there is a `renovate.json[5]` file in the root of the repo's default branch
    - "onboarding": if there is a PR open that adds a `renovate.json[5]` file in the root of the repo's default branch
    - "disabled": if both "onboarded" and "onboarding" are false

    Note that for determining the onboarding status, only the DEFAULT Git branch is considered, and we assume that
    whatever is the default branch now, has also been the default branch at any point in the past.
    """
    week_start_dates = get_sampling_dates(config)
    cutoff_date = week_start_dates[0] - timedelta(weeks=1)  # add one week to have some "leeway"

    onboarding_statuses: list[RepositoryOnboardingStatus] = []
    iterator = tqdm(config.repos, ncols=80)
    for git_repository in iterator:
        onboarding_prs_for_this_repo = [pr for pr in onboarding_prs if pr.repo == git_repository]
        commit_helper = GitCommitHelper(git_repository, cutoff_date=cutoff_date)

        for week_start_date in week_start_dates:
            has_renovate_json_file = commit_helper.contains_renovate_json_file(week_start_date)

            onboarding_status = OnboardingType.onboarded if has_renovate_json_file else OnboardingType.disabled
            if has_renovate_onboarding_pr(onboarding_prs_for_this_repo, week_start_date,
                                          config.renovate_onboarding_sampling_interval_weeks):
                onboarding_status = OnboardingType.in_progress

            onboarding_statuses.append(
                RepositoryOnboardingStatus(
                    repo=git_repository.owner_and_name,
                    onboarded=onboarding_status,
                    sample_date=week_start_date,
                )
            )

        # Work around issue https://github.com/tqdm/tqdm/issues/771
        if Path("/.dockerenv").exists():
            print(iterator)

    return onboarding_statuses

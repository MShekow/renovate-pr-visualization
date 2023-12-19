"""
This script fills the provided PostgreSQL database with information about Renovate PRs and repository onboarding
statuses.
"""

from conf import load_and_verify_configuration
from renovate_parser import get_renovate_prs, get_database_entities, get_repository_onboarding_status, \
    save_database_entities

if __name__ == '__main__':
    configuration = load_and_verify_configuration()
    print("Configuration check successful, starting to fetch Renovate PRs (this may take a few minutes) ...")
    renovate_prs = get_renovate_prs(configuration)
    print(f"Found {len(renovate_prs.dependency_prs)} Renovate PRs, converting them to them to database entities ...")
    database_dependency_prs = get_database_entities(renovate_prs.dependency_prs, configuration)
    print(f"Fetching repository onboarding statuses for {len(configuration.github_repos)} repos "
          f"(this may take several minutes) ...")
    database_onboarding_statuses = get_repository_onboarding_status(renovate_prs.onboarding_prs, configuration)
    print("Storing database entities in the database (deleting old entries) ...")
    save_database_entities(database_dependency_prs, database_onboarding_statuses, configuration)
    print("Finished importing data successfully")

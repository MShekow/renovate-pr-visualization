# Renovate PR visualization

Scratchpad:

- How to get if there are multiple dependencies per commit?
- https://docs.github.com/en/rest/pulls/pulls?apiVersion=2022-11-28#list-pull-requests

## Analysis of GitHub's List PR API

Required inputs:
- GitHub repo `owner`
- GitHub repo `repo`
- GitHub API Endpoint
- GitHub PAT
- Label name for dependencies (to filter PRs)
- Label name for security issues

Fields
- `number`
- `labels`: an object array, with important key `name`
- `title`: Format: `Update [dependency] <name with possibly spaces> to <version with possibly spaces>`
  - Note: detection of major/minor _cannot_ be reliably deduced from the title (it _may_ or may not contain `(major)`)
- `body` (retrieve multiple deps?)
- `created_at`
- optional `closed_at` or `merged_at`


## Idea for DB schema
Note: we break down the entries so that they are not per PR but per dependency

- Repo (owner+repo as one string)
- created date
- optional `closed` date (taken from either `closed_at` or `merged_at`)
- optional Close type (merge, close)
- Update type (patch, minor, major, multiple-major, security)
- Dependency name (extracted from the table of `body`)
- Related PR (URL)

## Idea for graphs
TODO
- Open PRs over time, sub-dividable by:
  - Update type
- Duration until PR was closed (segmented into a few bins), e.g. as histogram. Maybe sub-divided by update type
- Filters:
  - Repository

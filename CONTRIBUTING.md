# Contributing

How the team works in this repo. The rule is simple: **nobody pushes straight to `main`**. Every
change goes on a branch, gets a pull request, and is merged after a teammate has looked at it.

## One-time setup

Follow the Quick start in the [README](README.md), then turn on the guard that blocks accidental
pushes to `main` (it's free; GitHub only enforces branch rules on paid plans):

```bash
make hooks
```

## Starting a task

Set your branch name on the first line (`feature/` for new work, `fix/` for repairs, `docs/` for
writing).

```bash
BRANCH=feature/YOUR-TASK-NAME
git checkout main
git pull
git checkout -b "$BRANCH"
```

## Saving your work and opening a pull request

Change the message on the first line to say what you changed. Run `make test` first.

```bash
MSG="DESCRIBE WHAT YOU CHANGED"
git add -A
git commit -m "$MSG"
git push -u origin "$(git branch --show-current)"
gh pr create --fill
```

To add more changes to an open pull request, run the same block again.

## Merging (after a teammate approves)

```bash
gh pr merge --merge --delete-branch
git checkout main
git pull
```

`--merge` keeps every commit on `main` with its author and date, so each person's work shows on their
GitHub profile. (GitHub only counts commits once they reach `main`.)

## Bringing a long-running branch into `main`

If your branch started before `main` moved on (for example the Need or Opportunity work), update your branch
from `main` first, so any conflicts are fixed on your branch, not on `main`:

```bash
git checkout YOUR-BRANCH
git fetch origin
git merge origin/main          # fix any conflicts, then:
git add -A && git commit
make test
git push
gh pr create --base main --fill
```

Where conflicts usually happen, and how to settle them:

- **`README.md`**: keep the main README and fill in your tool's column in "How the repo is organized" plus one
  short paragraph under "The bigger project".
- **`DATA_SOURCES.md`** is an index: add one row pointing to your tool's own list (e.g. `docs/need_data_sources.md`).
- **`tests/test_pipelines.py`** holds the shared tests; put your tool's tests in `tests/test_<tool>.py`.

## Naming: keep each tool's pieces together

Use the tool's name on everything it owns, so anyone can tell what a folder holds:

| Piece | Fundability | Need | Opportunity |
|---|---|---|---|
| Website | `fundability_site/` | `need_site/` | `opportunity_site/` |
| Pipelines | `pipelines/fundability/` | `pipelines/need/` | `pipelines/opportunity/` |
| Processed tables | `data/processed/fundability/` | `data/processed/need/` | `data/processed/opportunity/` |
| Docs | `docs/fundability_*.md` | `docs/need_*.md` | `docs/opportunity_*.md` |
| Tests | `tests/test_fundability.py` | `tests/test_need.py` | `tests/test_opportunity.py` |
| Make targets | `make fundability-*` | `make need-*` | `make opportunity-*` |

Shared pieces stay where they are: the DCA applications pipeline (`pipelines/scrape_dca.py`,
`parse_applications.py`, `score_applications.py`) and its tables (`data/processed/applications_9pct*`).
Join tables on the 11-digit 2020 tract code; name the column `geoid` (or rename it when merging).

## Reviewing a teammate's pull request

Set the number from `gh pr list` on the first line.

```bash
PR=NUMBER
gh pr list
gh pr checkout "$PR"
gh pr diff "$PR"
gh pr review "$PR" --approve
git checkout main
```

## Conventions

- **Columns:** the shared tract table is defined in [`docs/fundability_data_dictionary.md`](docs/fundability_data_dictionary.md).
  To add or rename a column, change the dictionary in your pull request first or at the same time.
- **Data sources:** every source must be public, with no login or data use agreement. List it in your tool's
  sources file and make sure [`DATA_SOURCES.md`](DATA_SOURCES.md) points to that file.
- **Personal data:** never commit applicant emails, phone numbers, or other personal details.
- **Raw data stays out of git.** Scripts in `pipelines/` re-create it; outputs go in `data/processed/`.
- **One job per script**, in your tool's `pipelines/<tool>/` folder, wired into a `make <tool>-*` target
  when it's ready.
- **Tests:** add checks to `tests/test_<tool>.py` for anything another script depends on. CI runs every
  `tests/test_*.py` on every pull request.
- **Don't edit SiteLine.** It lives in its own repo; we only import it.

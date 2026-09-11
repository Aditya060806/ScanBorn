"""Cross-platform task runner.

The Makefile is the canonical entry point on POSIX, but it is POSIX-only: `make` is not
present on a stock Windows box, and `make clean` shells out to `rm -rf` and `find`. Since
this project is developed on Windows and deployed to Linux containers, the same tasks need
a runner that works in both places.

`nox` and `tox` were already declared in requirements-dev.txt with no noxfile.py or
tox.ini anywhere in the repo — a dependency nobody could use. This file makes the nox half
of that real and keeps the task list in step with the Makefile's PKGS.

    nox -s lint          flake8 + mypy
    nox -s format        black + isort, in place
    nox -s format_check  black + isort, check only
    nox -s tests         pytest with coverage (pyproject addopts)
    nox -s security      bandit

Sessions run in the *current* interpreter (`python=False`) rather than building a venv per
session. That is deliberate: this is a task runner, not a version matrix. CI already tests
3.10/3.11/3.12 via the GitHub Actions matrix, and rebuilding a venv here would reinstall
numpy and fastapi on every lint.
"""
import nox

# Mirrors PKGS in the Makefile. Keep the two in step.
PKGS = [
    "sdk/", "orchestrator/", "capture/", "reconstruction/", "semantic/",
    "robot/", "sarvam/", "twin/", "policy/", "deployment/", "tests/",
]

nox.options.sessions = ["lint", "tests"]
nox.options.reuse_existing_virtualenvs = True


@nox.session(python=False)
def lint(session: nox.Session) -> None:
    """flake8 then mypy, both reading their config from the repo root."""
    session.run("python", "-m", "flake8", *PKGS)
    session.run("python", "-m", "mypy", *PKGS)


@nox.session(python=False)
def format(session: nox.Session) -> None:
    """Rewrite in place. Note: the tree has never been black-formatted — see format_check."""
    session.run("python", "-m", "black", *PKGS)
    session.run("python", "-m", "isort", *PKGS)


@nox.session(python=False)
def format_check(session: nox.Session) -> None:
    """Check only.

    Expected to FAIL today. `format-check` is commented out in .github/workflows/ci.yml
    because the tree predates black and reformatting it would bury the real history in a
    whitespace commit. Run `nox -s format` deliberately, as its own commit, before turning
    this on in CI.
    """
    session.run("python", "-m", "black", "--check", *PKGS)
    session.run("python", "-m", "isort", "--check-only", *PKGS)


@nox.session(python=False)
def tests(session: nox.Session) -> None:
    """Full suite. Coverage flags come from [tool.pytest.ini_options] addopts."""
    session.run("python", "-m", "pytest", *session.posargs)


@nox.session(python=False)
def security(session: nox.Session) -> None:
    """bandit over the shipped packages, matching the CI security job (tests/ excluded)."""
    session.run("python", "-m", "bandit", "-r", "-ll",
                *[p for p in PKGS if p != "tests/"])

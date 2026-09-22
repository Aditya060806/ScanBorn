"""The orchestrator image copies every first-party package the orchestrator can import.

The Dockerfile once copied only orchestrator/ while service.py imported eight sibling packages, so
the image could never start. Imports are read from source rather than by importing, so a lazy
import inside a function (a remote planner, the AI Hub export) counts too.
"""
import ast
import os
import re

import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
DOCKERFILE = os.path.join(ROOT, "docker", "Dockerfile.orchestrator")
COMPOSE = os.path.join(ROOT, "docker", "docker-compose.yml")


def copied_dirs() -> set:
    with open(DOCKERFILE, encoding="utf-8") as f:
        return set(re.findall(r"^COPY (\w+)/ \./\1/$", f.read(), flags=re.MULTILINE))


def first_party(name: str) -> bool:
    return (os.path.isdir(os.path.join(ROOT, name))
            or os.path.isfile(os.path.join(ROOT, f"{name}.py")))


def imported_packages(package: str) -> set:
    found: set = set()
    for folder, dirs, files in os.walk(os.path.join(ROOT, package)):
        dirs[:] = [d for d in dirs if d not in ("__pycache__", "android")]
        for name in files:
            if not name.endswith(".py"):
                continue
            with open(os.path.join(folder, name), encoding="utf-8") as f:
                tree = ast.parse(f.read())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    found.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
                    found.add(node.module.split(".")[0])
    return {name for name in found if first_party(name)}


def test_every_package_the_orchestrator_reaches_is_copied():
    copied = copied_dirs()
    assert "orchestrator" in copied
    reached, frontier = set(), {"orchestrator"}
    while frontier:
        package = frontier.pop()
        reached.add(package)
        frontier |= imported_packages(package) - reached
    assert reached <= copied, f"not copied into the image: {sorted(reached - copied)}"


def test_the_benchmarks_the_orchestrator_serves_are_copied():
    assert "benchmarks" in copied_dirs()


def test_compose_runs_only_the_orchestrator_and_sets_no_unread_variables():
    with open(COMPOSE, encoding="utf-8") as f:
        services = yaml.safe_load(f)["services"]
    assert list(services) == ["orchestrator"]
    # CAPTURE_URL / SEMANTIC_URL were set here and read by nothing.
    assert "environment" not in services["orchestrator"]

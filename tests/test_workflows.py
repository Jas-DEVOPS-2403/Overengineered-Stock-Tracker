"""Structural checks on the GitHub Actions workflows.

actionlint (run in CI) checks that the YAML is valid Actions syntax. These
check the decisions this repo has already paid for once: each one below
encodes a failure that happened, or a choice that is easy to undo by
accident.
"""

import os
import re

import pytest
import yaml

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
WORKFLOWS = os.path.join(ROOT, ".github", "workflows")


def _load(name: str) -> dict:
    with open(os.path.join(WORKFLOWS, name), encoding="utf-8") as f:
        return yaml.safe_load(f)


def _all() -> list[str]:
    return sorted(f for f in os.listdir(WORKFLOWS) if f.endswith((".yml", ".yaml")))


def _uses(wf: dict) -> list[str]:
    return [s["uses"] for job in wf["jobs"].values()
            for s in job.get("steps", []) if "uses" in s]


@pytest.mark.parametrize("name", _all())
def test_every_job_names_a_runner(name):
    for job_id, job in _load(name)["jobs"].items():
        assert "runs-on" in job, f"{name}: job {job_id} has no runs-on"


@pytest.mark.parametrize("name", _all())
def test_actions_are_pinned_to_a_version(name):
    # @main or @master runs whatever the action's author pushed last.
    for ref in _uses(_load(name)):
        assert "@" in ref, f"{name}: {ref} has no version"
        version = ref.split("@", 1)[1]
        assert re.fullmatch(r"v\d+(\.\d+){0,2}|[0-9a-f]{40}", version), \
            f"{name}: {ref} is not pinned to a version tag or commit SHA"


@pytest.mark.parametrize("name", _all())
def test_permissions_are_declared(name):
    # Without a top-level block the token gets the repository default, which
    # may be read-write on everything.
    assert "permissions" in _load(name), f"{name} has no top-level permissions"


def test_publish_does_not_deploy_pages():
    # Petra is deliberately not hosted: public display of its data is not
    # clearly covered by Alpha Vantage's or TradingView's terms (README,
    # Status). Re-adding a deploy should be a decision, not a paste.
    wf = _load("publish.yml")
    pages = [u for u in _uses(wf) if "pages" in u]
    assert not pages, f"publish.yml deploys to Pages again: {pages}"
    assert "pages" not in wf["permissions"]
    assert "id-token" not in wf["permissions"]


def test_publish_still_pushes_the_data_branch():
    wf = _load("publish.yml")
    assert wf["permissions"].get("contents") == "write"
    runs = " ".join(s.get("run", "") for s in wf["jobs"]["publish"]["steps"])
    assert "push" in runs and " data" in runs


def test_publish_runs_on_a_schedule_and_by_hand():
    # yaml reads the bare key `on` as True.
    on = _load("publish.yml").get(True) or _load("publish.yml").get("on")
    assert "schedule" in on and "workflow_dispatch" in on


def test_alert_can_name_the_failing_step():
    # Job-level permissions replace the workflow-level block. Without
    # actions: read, the job lookup 403s and the issue cannot say which step
    # failed -- which is how the first version of this alert shipped.
    alert = _load("publish.yml")["jobs"]["alert"]
    assert alert["needs"] == "publish"
    assert alert["if"] == "failure()"
    assert alert["permissions"].get("actions") == "read"
    assert alert["permissions"].get("issues") == "write"


def test_state_restore_failure_is_not_swallowed():
    # `|| true` on the restore turned a failed checkout into a silent cold
    # start, under which the empty-run guards see no history at all.
    step = next(s for s in _load("publish.yml")["jobs"]["publish"]["steps"]
                if s.get("name") == "Fetch previous data")
    checkout = [ln for ln in step["run"].splitlines() if "checkout FETCH_HEAD" in ln]
    assert checkout and all("|| true" not in ln for ln in checkout)


def test_ci_runs_lint_and_tests_on_pull_requests():
    ci = _load("ci.yml")
    on = ci.get(True) or ci.get("on")
    assert "pull_request" in on
    runs = " ".join(s.get("run", "") for job in ci["jobs"].values()
                    for s in job.get("steps", []))
    assert "ruff check" in runs and "pytest" in runs

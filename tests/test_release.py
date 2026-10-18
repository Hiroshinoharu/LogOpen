"""Validate semantic release decisions, artifact integrity, and retry-safe publication."""

import json
from pathlib import Path
import subprocess

import pytest
from scripts import release


@pytest.fixture
def repository(tmp_path):
    """Create a private temporary Git repository without touching the user's checkout."""
    root = tmp_path / "source"
    root.mkdir()
    release.git("init", "--initial-branch=master", root=root)
    release.git("config", "user.email", "tests@example.invalid", root=root)
    release.git("config", "user.name", "Release tests", root=root)
    (root / "VERSION").write_text("0.1.0\n", encoding="utf-8")
    release.git("add", "VERSION", root=root)
    release.git("commit", "-m", "Initial source", root=root)
    return root


def commit(root, message):
    """Add an empty synthetic commit and return its exact object ID."""
    release.git("commit", "--allow-empty", "-m", message, root=root)
    return release.git("rev-parse", "HEAD", root=root)


@pytest.mark.parametrize("value", ["01.2.3", "1.2", "1.2.3.4", "v1.2.3", "1.2.3-beta", "1.2.3\n", "-1.0.0", "65536.0.0"])
def test_invalid_stable_windows_versions(value):
    """Reject malformed, prerelease, or out-of-range installer versions before packaging."""
    with pytest.raises(ValueError):
        release.parse_version(value)


@pytest.mark.parametrize("messages,kind,expected", [
    (["fix: repair key saving"], "patch", "1.2.4"),
    (["ordinary update"], "patch", "1.2.4"),
    (["docs: update readme", "feat(ui): add channels"], "minor", "1.3.0"),
    (["feat: add channels", "fix!: remove legacy interface"], "major", "2.0.0"),
    (["refactor: change format\n\nBREAKING CHANGE: report schema changed"], "major", "2.0.0"),
])
def test_semantic_bump_precedence(messages, kind, expected):
    """Use the strongest change in the push and reset lower components correctly."""
    assert release.bump_kind(messages) == kind
    assert release.bump_version("1.2.3", kind) == expected


def test_planning_is_read_only_and_reruns_reuse_the_tag(repository):
    """Planning increments an untagged push without mutating Git; an existing HEAD tag is reused."""
    before = release.git("rev-parse", "HEAD", root=repository)
    commit(repository, "fix: patch")
    plan = release.release_plan(repository, before=before)
    assert plan["version"] == "0.1.1" and not plan["reused"]
    assert release.git("tag", "--list", root=repository) == ""
    assert release.git("status", "--porcelain", root=repository) == ""
    release.git("tag", plan["tag"], root=repository)
    repeated = release.release_plan(repository, before=before)
    assert repeated["version"] == "0.1.1" and repeated["reused"]


def test_multiple_commits_produce_one_feature_release(repository):
    """One push with fixes and a feature receives one minor bump, not one bump per commit."""
    before = release.git("rev-parse", "HEAD", root=repository)
    release.git("tag", "v1.2.9", root=repository)
    commit(repository, "feat: introduce timeline")
    commit(repository, "fix: resize columns")
    plan = release.release_plan(repository, before=before)
    assert plan["version"] == "1.3.0" and plan["bump"] == "minor"
    assert release.release_plan(repository, before=before, bump="patch")["version"] == "1.2.10"


def test_tags_compare_numerically_and_unrelated_tags_are_ignored(repository):
    """Version 0.1.10 sorts after 0.1.9; nonrelease tags cannot corrupt the ledger."""
    for tag in ("v0.1.9", "v0.1.10", "experimental", "v01.2.3"):
        release.git("tag", tag, root=repository)
    commit(repository, "patch")
    assert release.current_version(repository) == "0.1.10"
    assert release.release_plan(repository)["version"] == "0.1.11"


def test_first_branch_push_checks_all_new_commits(repository):
    """A newly created branch has an all-zero before SHA; earlier features in that push still count."""
    commit(repository, "feat: first feature")
    commit(repository, "fix: follow-up")
    assert release.release_plan(repository, before="0" * 40)["version"] == "0.2.0"


def test_failed_feature_build_is_included_in_next_successful_version(repository):
    """A later patch cannot hide an unreleased feature merely because the feature's CI failed."""
    release.git("tag", "v0.1.0", root=repository)
    failed_push = commit(repository, "feat: new capability")
    commit(repository, "fix: repair tests")
    assert release.release_plan(repository, before=failed_push)["version"] == "0.2.0"


def test_archive_build_uses_version_without_git(tmp_path):
    """Downloaded source without Git metadata remains buildable from VERSION."""
    (tmp_path / "VERSION").write_text("0.3.0\n", encoding="utf-8")
    assert release.current_version(tmp_path) == "0.3.0"


def test_unknown_before_revision_fails_instead_of_guessing(repository):
    """Missing force-push history must be visible so a breaking change is not silently missed."""
    with pytest.raises(subprocess.CalledProcessError):
        release.release_plan(repository, before="f" * 40)


def test_ci_only_does_not_bump_or_create_tags(repository, tmp_path):
    """Pull-request and feature-branch packages cannot reserve or publish release versions."""
    plan = release.write_plan(tmp_path / "plan", root=repository, release=False)
    assert plan["version"] == "0.1.0" and plan["bump"] == "none"
    assert not release.git("tag", "--list", root=repository)


def stage(root, directory):
    """Prepare two inert test artifacts using the same staging and hashing path as CI."""
    plan = release.write_plan(directory, root=root)
    paths = [root / "dist" / f"LogOpen-{plan['version']}-Windows-x64.zip",
             root / "dist" / "installer" / f"LogOpen-{plan['version']}-Setup-x64.exe"]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"synthetic artifact " + path.name.encode())
    release.stage_artifacts(directory, root=root)
    return json.loads((directory / "plan.json").read_text(encoding="utf-8"))


def test_artifact_tampering_is_rejected_before_publication(repository, tmp_path):
    """Hash verification rejects a changed asset and path-injection names before any Git writes."""
    directory = tmp_path / "assets"
    plan = stage(repository, directory)
    release.verify_artifacts(directory, plan)
    (directory / next(iter(plan["sha256"]))).write_bytes(b"changed")
    with pytest.raises(ValueError, match="checksum"):
        release.verify_artifacts(directory, plan)
    plan["sha256"] = {"../unexpected": "ignored"}
    with pytest.raises(ValueError, match="assets"):
        release.verify_artifacts(directory, plan)


def test_metadata_agrees_with_selected_release_version(repository):
    """Bundle and Windows executable properties receive the same selected version."""
    release.build_metadata("2.3.4", repository)
    directory = repository / "build" / "version"
    assert (directory / "VERSION").read_text().strip() == "2.3.4"
    text = (directory / "windows-version.txt").read_text()
    assert "filevers=(2, 3, 4, 0)" in text and "'ProductVersion', '2.3.4'" in text


def test_publication_retries_draft_and_leaves_published_assets_untouched(repository, tmp_path, monkeypatch):
    """Exercise real local tag pushes with mocked GitHub responses, including failed upload recovery."""
    remote = tmp_path / "remote.git"
    release.git("init", "--bare", str(remote), root=repository)
    release.git("remote", "add", "origin", str(remote), root=repository)
    release.git("push", "origin", "master", root=repository)
    directory = tmp_path / "assets"
    plan = stage(repository, directory)
    original_run = subprocess.run
    calls = []
    state = {"release": None, "fail_upload": True}

    def run(arguments, **kwargs):
        """Fake GitHub release storage while allowing Git against the private bare repository."""
        if arguments[0] != "gh":
            return original_run(arguments, **kwargs)
        calls.append(arguments)
        output = ""
        if arguments[1] == "api":
            output = json.dumps([[state["release"]] if state["release"] else []])
        elif arguments[2] == "create":
            state["release"] = {"tag_name": plan["tag"], "draft": True}
        elif arguments[2] == "upload" and state["fail_upload"]:
            state["fail_upload"] = False
            raise subprocess.CalledProcessError(1, arguments)
        elif arguments[2] == "edit":
            state["release"]["draft"] = False
        return subprocess.CompletedProcess(arguments, 0, stdout=output, stderr="")

    monkeypatch.setattr(subprocess, "run", run)
    with pytest.raises(subprocess.CalledProcessError):
        release.publish(directory, repository)
    assert state["release"]["draft"]
    assert release.release_plan(repository)["version"] == plan["version"]
    release.publish(directory, repository)
    assert not state["release"]["draft"]
    assert sum(call[1:3] == ["release", "create"] for call in calls) == 1
    calls.clear()
    release.publish(directory, repository)
    assert all(call[1] == "api" for call in calls)

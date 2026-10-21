"""Plan semantic releases, generate build metadata, and publish verified artifacts.

Version tags are the release ledger; VERSION is the initial version for untagged
source archives. Planning never mutates Git. Only the explicit publish command
creates a tag/release, after CI tests and packaging have succeeded.
"""

import argparse
import hashlib
import json
import re
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
STABLE = re.compile(r"(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\.(0|[1-9][0-9]*)\Z")


def parse_version(value):
    """Validate a stable SemVer and return its three Windows-compatible integers."""
    if not isinstance(value, str) or not STABLE.fullmatch(value):
        raise ValueError("Use a stable semantic version: MAJOR.MINOR.PATCH, without leading zeros.")
    parts = tuple(int(part) for part in value.split("."))
    if any(part > 65535 for part in parts):
        raise ValueError("Windows file versions require each component to be at most 65535.")
    return parts


def git(*args, root=ROOT):
    """Run Git without shell interpolation and return stripped UTF-8 output."""
    return subprocess.run(["git", *args], cwd=root, check=True, capture_output=True,
                          text=True, encoding="utf-8").stdout.strip()


def stable_tags(root=ROOT, *, reachable=False):
    """Map valid vMAJOR.MINOR.PATCH tags to numeric tuples, ignoring unrelated tags."""
    args = ["tag", "--list", "v*"]
    if reachable:
        args += ["--merged", "HEAD"]
    tags = {}
    for tag in git(*args, root=root).splitlines():
        try:
            tags[tag] = parse_version(tag[1:])
        except ValueError:
            continue
    return tags


def current_version(root=ROOT):
    """Return the highest reachable stable tag or VERSION for an untagged/archive build."""
    baseline = (root / "VERSION").read_text(encoding="utf-8").strip()
    parse_version(baseline)
    try:
        tags = stable_tags(root, reachable=True)
    except (OSError, subprocess.CalledProcessError):
        tags = {}
    return ".".join(map(str, max([parse_version(baseline), *tags.values()])))


def bump_kind(messages):
    """Choose major for breaking changes, minor for feat commits, otherwise patch."""
    text = "\n".join(messages)
    if re.search(r"(?m)^(?:BREAKING[ -]CHANGE:|[a-z]+(?:\([^\n)]+\))?!:)", text):
        return "major"
    if re.search(r"(?m)^feat(?:\([^\n)]+\))?:", text):
        return "minor"
    return "patch"


def bump_version(version, kind):
    """Increment one SemVer component, resetting its less significant components."""
    major, minor, patch = parse_version(version)
    if kind not in ("major", "minor", "patch"):
        raise ValueError("Bump must be major, minor, or patch.")
    parts = (major + 1, 0, 0) if kind == "major" else (major, minor + 1, 0) if kind == "minor" else (major, minor, patch + 1)
    result = ".".join(map(str, parts))
    parse_version(result)
    return result


def release_plan(root=ROOT, *, before=None, bump="auto"):
    """Compute the next release for HEAD, reusing an existing tag on reruns.

    All stable tags reserve versions, including failed/draft releases. Commit
    classification includes changes since the latest reachable release so a
    failed feature build is not forgotten by a later fix. With no tags, the push
    range is used, or HEAD alone for the first manual release.
    """
    commit = git("rev-parse", "HEAD", root=root)
    tags = stable_tags(root)
    matching = [tag for tag in git("tag", "--points-at", "HEAD", root=root).splitlines() if tag in tags]
    if matching:
        version = max(matching, key=tags.get)[1:]
        return dict(version=version, tag="v" + version, commit=commit, bump="existing", reused=True)
    baseline = (root / "VERSION").read_text(encoding="utf-8").strip()
    previous = ".".join(map(str, max([parse_version(baseline), *tags.values()])))
    revisions = ["-1", "HEAD"]
    if before and set(before) != {"0"}:
        if not re.fullmatch(r"[0-9a-fA-F]{40,64}", before):
            raise ValueError("The previous revision must be a full commit SHA.")
        # Missing force-push history should fail visibly rather than silently
        # misclassifying an unknown range as a patch release.
        git("cat-file", "-e", before + "^{commit}", root=root)
        revisions = [before + "..HEAD"]
    elif before and set(before) == {"0"}:
        revisions = ["HEAD"]
    reachable = stable_tags(root, reachable=True)
    if reachable:
        revisions = [max(reachable, key=reachable.get) + "..HEAD"]
    messages = git("log", *revisions, "--format=%B%x00", root=root).split("\x00")
    kind = bump_kind(messages) if bump == "auto" else bump
    version = bump_version(previous, kind)
    return dict(version=version, tag="v" + version, commit=commit, bump=kind, reused=False)


def write_plan(directory, *, root=ROOT, before=None, bump="auto", release=True):
    """Save a version plan and plain-text release notes; no tags or commits are written."""
    directory.mkdir(parents=True, exist_ok=True)
    plan = release_plan(root, before=before, bump=bump) if release else {
        "version": current_version(root), "commit": git("rev-parse", "HEAD", root=root),
        "reused": False, "bump": "none"}
    plan["tag"] = "v" + plan["version"]
    (directory / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    # Include commits since the most recent ancestor release for useful notes,
    # even when several earlier pushes failed their CI checks.
    ancestors = {tag: number for tag, number in stable_tags(root, reachable=True).items()
                 if git("rev-parse", tag + "^{commit}", root=root) != plan["commit"]}
    revisions = [max(ancestors, key=ancestors.get) + "..HEAD"] if ancestors else ["-20", "HEAD"]
    subjects = git("log", *revisions, "--format=- %s (%h)", root=root)
    notes = (f"# LogOpen {plan['version']}\n\nSource commit: `{plan['commit']}`\n\n"
             "Windows x64 installer and portable ZIP. Builds are currently unsigned.\n"
             "Use SHA256SUMS.txt to verify the downloaded files.\n\n## Changes\n\n" + subjects + "\n")
    (directory / "RELEASE_NOTES.md").write_text(notes, encoding="utf-8")
    return plan


def build_metadata(version, root=ROOT):
    """Generate the packaged VERSION file and Windows executable version resource."""
    numbers = (*parse_version(version), 0)
    directory = root / "build" / "version"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "VERSION").write_text(version + "\n", encoding="utf-8")
    resource = f'''VSVersionInfo(
  ffi=FixedFileInfo(filevers={numbers!r}, prodvers={numbers!r}, mask=0x3f,
                   flags=0x0, OS=0x40004, fileType=0x1, subtype=0x0, date=(0, 0)),
  kids=[StringFileInfo([StringTable('040904B0', [
    StringStruct('FileDescription', 'LogOpen Windows Incident Analysis'),
    StringStruct('FileVersion', {version!r}), StringStruct('ProductVersion', {version!r}),
    StringStruct('ProductName', 'LogOpen'), StringStruct('OriginalFilename', 'LogOpen.exe')
  ])]), VarFileInfo([VarStruct('Translation', [1033, 1200])])])
'''
    (directory / "windows-version.txt").write_text(resource, encoding="utf-8")


def stage_artifacts(directory, root=ROOT):
    """Copy the two expected release assets and hash their exact bytes for publication."""
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    version = plan["version"]
    parse_version(version)
    sources = [root / "dist" / f"LogOpen-{version}-Windows-x64.zip",
               root / "dist" / "installer" / f"LogOpen-{version}-Setup-x64.exe"]
    checksums = {}
    for path in sources:
        shutil.copy2(path, directory / path.name)
        with path.open("rb") as source:
            checksums[path.name] = hashlib.file_digest(source, "sha256").hexdigest()
    plan["sha256"] = checksums
    (directory / "plan.json").write_text(json.dumps(plan, indent=2) + "\n", encoding="utf-8")
    (directory / "SHA256SUMS.txt").write_text("".join(f"{digest}  {name}\n" for name, digest in checksums.items()), encoding="utf-8")


def verify_artifacts(directory, plan):
    """Require the exact expected asset names and hashes before any publishing side effect."""
    version = plan["version"]
    parse_version(version)
    expected = {f"LogOpen-{version}-Windows-x64.zip", f"LogOpen-{version}-Setup-x64.exe"}
    if set(plan.get("sha256", {})) != expected:
        raise ValueError("Release assets do not match the planned version.")
    for name, expected_hash in plan["sha256"].items():
        with (directory / name).open("rb") as source:
            if hashlib.file_digest(source, "sha256").hexdigest() != expected_hash:
                raise ValueError("Release asset checksum mismatch: " + name)
    checksums = "".join(f"{digest}  {name}\n" for name, digest in plan["sha256"].items())
    if (directory / "SHA256SUMS.txt").read_text(encoding="utf-8") != checksums:
        raise ValueError("The checksum manifest does not match the verified assets.")


def publish(directory, root=ROOT):
    """Publish verified artifacts as a draft first, resuming safely after interrupted runs.

    Requires Git push authentication and GH_TOKEN for the GitHub CLI. Never
    force-pushes a tag or changes an already-published release. The checkout must
    exactly match the commit tested and packaged by the preceding CI job.
    """
    plan = json.loads((directory / "plan.json").read_text(encoding="utf-8"))
    verify_artifacts(directory, plan)
    version, commit = plan["version"], plan["commit"]
    tag = "v" + version
    if plan.get("tag") != tag or git("rev-parse", "HEAD", root=root) != commit:
        raise ValueError("Release plan does not match this checkout.")
    git("fetch", "origin", "--tags", root=root)
    if tag in stable_tags(root):
        if git("rev-parse", tag + "^{commit}", root=root) != commit:
            raise ValueError("The version tag already belongs to a different commit.")
    else:
        git("tag", tag, commit, root=root)
        git("push", "origin", "refs/tags/" + tag, root=root)

    def gh(*args, check=True):
        """Invoke authenticated GitHub CLI with argument arrays, never interpolated shell text."""
        return subprocess.run(["gh", *args], cwd=root, check=check, capture_output=True,
                              encoding="utf-8", text=True)

    # Listing succeeds even when this tag has no release; permission/network
    # errors fail here instead of being mistaken for a missing release.
    releases = json.loads(gh("api", "repos/{owner}/{repo}/releases", "--paginate", "--slurp").stdout)
    existing = next((item for page in releases for item in page if item["tag_name"] == tag), None)
    if existing and not existing["draft"]:
        print(f"{tag} is already published; leaving its assets unchanged.")
        return
    if existing is None:
        gh("release", "create", tag, "--verify-tag", "--draft", "--title", "LogOpen " + version,
           "--notes-file", str((directory / "RELEASE_NOTES.md").resolve()))
    assets = [str((directory / name).resolve()) for name in plan["sha256"]]
    assets.append(str((directory / "SHA256SUMS.txt").resolve()))
    gh("release", "upload", tag, *assets, "--clobber")
    # An old queued push must not replace a release of a newer descendant as Latest.
    known_tags = stable_tags(root)
    superseded = any(item["tag_name"] in known_tags and not item["draft"] and
                     subprocess.run(["git", "merge-base", "--is-ancestor", commit, item["tag_name"]],
                                    cwd=root, capture_output=True).returncode == 0
                     for page in releases for item in page if item["tag_name"] != tag)
    gh("release", "edit", tag, "--draft=false", "--latest=" + ("false" if superseded else "true"))
    print(f"Published {tag} from {commit}.")


def main():
    """Expose read-only version planning and explicit build/stage/publish commands."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=("current", "plan", "metadata", "stage", "publish"))
    parser.add_argument("--version")
    parser.add_argument("--before")
    parser.add_argument("--bump", choices=("auto", "patch", "minor", "major"), default="auto")
    parser.add_argument("--ci-only", action="store_true")
    parser.add_argument("--directory", type=Path, default=ROOT / "build" / "release")
    args = parser.parse_args()
    if args.command == "current":
        print(current_version())
    elif args.command == "plan":
        print(json.dumps(write_plan(args.directory, before=args.before, bump=args.bump, release=not args.ci_only)))
    elif args.command == "metadata":
        build_metadata(args.version or current_version())
    elif args.command == "stage":
        stage_artifacts(args.directory)
    else:
        publish(args.directory)


if __name__ == "__main__":
    main()

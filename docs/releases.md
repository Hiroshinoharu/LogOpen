# Automatic Windows releases

The **Windows CI and release** workflow tests and packages every branch push and
pull request. A successful push to the repository's **default branch** (currently
`master`) also creates a GitHub Release with an installer, portable ZIP, release
notes, and SHA256 checksums. No OpenAI key or personal access token is required.

## Version rules

Stable releases use `MAJOR.MINOR.PATCH` and tags such as `v0.1.1`.

| Commits included in one push | Automatic bump | Example from 0.1.0 |
| --- | --- | --- |
| `fix: ...`, `docs: ...`, ordinary commit messages | Patch | 0.1.1 |
| `feat: ...` or `feat(ui): ...` | Minor | 0.2.0 |
| `feat!: ...`, `fix!: ...`, or a `BREAKING CHANGE:` footer | Major | 1.0.0 |

The strongest change wins. Several commits pushed together produce **one release**.
Failed tests/builds publish nothing and consume no version. Feature branches and
pull requests get downloadable CI artifacts but do not bump stable versions.
Once a version tag exists, bump classification includes all changes since the
latest reachable release, so a feature from a failed build is included when the
next push succeeds.
The Actions tab also supports a manual run with an explicit patch/minor/major
bump; publication is still restricted to the default branch.

Git tags are the authoritative release history. `VERSION` supplies the initial
`0.1.0` baseline when there are no version tags or when building a source archive.
The workflow does not make version-only commits back to your branch, so there
are no bot-commit loops or routine pull conflicts. Fetch tags to see new versions
locally:

```powershell
git fetch --tags
python scripts/release.py current
```

Each packaged app contains the selected `VERSION`, Windows executable version
properties, and matching ZIP/installer names. The supported release format is
stable SemVer only; prerelease/build suffixes are not generated. Each numeric
component is limited to 65535 by the Windows executable version resource.

## What happens on a push

1. GitHub checks out the exact pushed commit and reads the version tags.
2. A Windows runner installs the pinned Python 3.14.7 toolchain/dependencies.
3. Tests run, then PyInstaller builds the desktop and checks packaged startup.
4. Inno Setup builds the installer. The official compiler download is verified
   by the existing build script's SHA256 and publisher-signature checks.
5. The two artifacts are staged with checksums and a release plan containing the
   tested commit. CI uploads this bundle, even for feature-branch/PR builds.
6. For the default branch, a separate job verifies the artifact hashes and commit,
   pushes the version tag, uploads assets to a draft, then publishes the release.

Rapid default-branch pushes queue through the full workflow, preventing version
allocation races. GitHub currently allows up to 100 pending runs in this queue;
an overflow can cancel additional runs. Queue order follows when runs enter the
queue, which can differ from push order. A release from an older queued commit
does not replace an already-published descendant release as **Latest**.

Rerunning a tagged commit reuses its version. An interrupted draft upload resumes;
an already-published release is left untouched. Tags are never force-moved. A
conflicting manually created tag causes a visible failure instead of overwriting
another release. Missing history after a force push also fails visibly.

## Enabling and finding releases

Commit and push the workflow, release/build scripts, `VERSION`, spec, installer,
tests, and docs together. The workflow can run once these files are on GitHub;
local validation alone does not enable a hosted pipeline.

- [Actions runs](https://github.com/Hiroshinoharu/LogOpen/actions)
- [Published releases](https://github.com/Hiroshinoharu/LogOpen/releases)

The workflow requests `contents: write` only in the publishing job and uses the
built-in GitHub token. Repository/organization policy must permit GitHub Actions,
the pinned official actions, release creation, and version-tag creation. If a
ruleset blocks those operations, adjust that policy explicitly rather than adding
a broad personal access token or weakening branch protection. No branch writes
are needed. Installer code signing and automatic in-app updates are not configured.

## Local builds

Use `scripts/build.ps1`, which resolves the highest reachable version tag by
default, or supply `-Version 0.2.0` to override the build version locally. This
does not create a tag or publish anything. Use the checked-in `LogOpen.spec`;
running `pyinstaller main.py --name LogOpen` overwrites it with a CLI-only spec.

`python scripts/release.py plan --directory build/release-preview` previews the
next release without changing Git. The `publish` subcommand writes to the remote
and is intended for the workflow after artifact validation.

## References

- [Semantic Versioning specification](https://semver.org/)
- [GitHub workflow triggers and token behavior](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/trigger-a-workflow)
- [GitHub queued concurrency](https://docs.github.com/en/actions/how-tos/write-workflows/choose-when-workflows-run/control-workflow-concurrency)

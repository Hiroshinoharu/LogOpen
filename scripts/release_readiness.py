"""Read-only public-beta checks against the exact artifact and recorded VM results."""
import argparse
import hashlib
import json
import os
from pathlib import Path
import subprocess

ROOT = Path(__file__).resolve().parents[1]
REQUIRED_CHECKS = frozenset({
    "clean_install_without_python", "normal_and_admin_scans", "uac_accept_and_cancel",
    "monitoring_resize_refresh", "offline_and_invalid_credentials", "ai_preview_cancel_and_send",
    "automatic_sharing_opt_in", "reviewed_problem_report", "history_and_key_removal",
    "upgrade_preserves_data", "uninstall_reinstall", "display_scaling_and_keyboard",
})


def evidence_failures(evidence, installer_hash):
    failures = []
    if evidence.get("installer_sha256") != installer_hash:
        failures.append("Clean-machine evidence belongs to a different or unspecified installer.")
    if not evidence.get("windows_build") or not evidence.get("tester"):
        failures.append("Record the test-machine Windows build and tester.")
    checks = evidence.get("checks", {})
    for name in sorted(REQUIRED_CHECKS):
        if not isinstance(checks, dict) or checks.get(name) is not True:
            failures.append("Not passed: " + name)
    return failures


def signed_thumbprint(path):
    # Paths enter via an environment value, never shell interpolation.
    environment = dict(os.environ, LOGOPEN_RELEASE_ARTIFACT=str(path.resolve()))
    script = ("$s = Get-AuthenticodeSignature -LiteralPath $env:LOGOPEN_RELEASE_ARTIFACT; "
              "if ($s.Status -ne 'Valid' -or -not $s.TimeStamperCertificate) { exit 1 }; "
              "$s.SignerCertificate.Thumbprint")
    result = subprocess.run(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script],
                            env=environment, capture_output=True, text=True, timeout=30)
    if result.returncode or not result.stdout.strip():
        raise ValueError("Missing or invalid timestamped signature: " + path.name)
    return result.stdout.strip()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--installer", required=True, type=Path)
    parser.add_argument("--executable", required=True, type=Path)
    parser.add_argument("--evidence", required=True, type=Path)
    args = parser.parse_args()
    failures = []
    tracked = subprocess.run(["git", "ls-files", "--", "reports", "*.sqlite3", "*.sqlite3-wal", "*.sqlite3-shm"],
                             cwd=ROOT, capture_output=True, text=True)
    if tracked.returncode or tracked.stdout.strip():
        failures.append("Source check failed: generated reports/history must not be tracked in the release repository.")
    try:
        with args.installer.open("rb") as source:
            digest = hashlib.file_digest(source, "sha256").hexdigest()
        evidence = json.loads(args.evidence.read_text(encoding="utf-8"))
        if not isinstance(evidence, dict):
            raise ValueError("Evidence must be a JSON object.")
        failures.extend(evidence_failures(evidence, digest))
        if signed_thumbprint(args.installer) != signed_thumbprint(args.executable):
            failures.append("Installer and executable have different signing identities.")
        package_root = args.executable.parent
        for name in ("LICENSE", "THIRD_PARTY_NOTICES.txt", "THIRD_PARTY_NOTICES.md"):
            if not any((base / name).is_file() for base in (package_root, package_root / "_internal")):
                failures.append("Missing packaged notice: " + name)
    except (OSError, ValueError, subprocess.SubprocessError) as error:
        failures.append(str(error))
    print("\n".join(failures) if failures else "Release checks passed for the supplied artifacts and evidence.")
    return 1 if failures else 0


if __name__ == "__main__":
    raise SystemExit(main())

# Launch readiness — 3 November 2026

**Assessment: beta candidate. A broad public release is not yet verified.**

## Verified in this workspace

- 536 automated tests and 54 subtests passed. Network/credential services were
  mocked; tests used isolated preferences and temporary history.
- Source startup and the packaged executable's offline startup check passed.
- Windows x64 installer and portable ZIP built successfully using the pinned
  dependencies. Dependency consistency checks passed.
- Privacy, AI preview and feedback dialogs were visually checked in both themes.
- Manual AI request preview, masking, automatic-sharing opt-in, exact reviewed
  request delivery, cancellation and confirmed data removal have regression tests.
- GPLv3 licence, generated dependency notices and privacy documentation are included
  in the portable bundle. No scan reports or history database are in that bundle.
- PowerShell signing/build scripts parsed successfully. They were not used to sign.

## Local test artifacts

- `dist/installer/LogOpen-0.3.1-Setup-x64.exe`
- `dist/LogOpen-0.3.1-Windows-x64.zip`
- Installer SHA256: `d6c5f686e04ebb8d6fa0c7508f8b4b112082818ba4550421bcbdb6082857ca5a`

These were built from the current working tree, which contains uncommitted changes.
They are local test artifacts, not a newly published or signed release. The version
comes from the existing version resolver; assign/tag the proper release version
when publishing the final source and rebuilt artifacts.

## Remaining before broad release

1. **Signing:** no code-signing certificate was found in either Windows personal
   certificate store. `maxceban` is not a certificate thumbprint. Supply a usable
   certificate or configure a signing service, then build and verify signed artifacts.
2. **Clean Windows trial:** install, scan without/admin permissions, exercise UAC
   cancellation, monitoring/fullscreen, upgrade, uninstall/reinstall, keyboard and
   scaling checks on the exact release installer. Windows Sandbox was unavailable
   here. The development-PC startup check is not this trial.
3. **Live AI check:** use a synthetic incident and an explicitly approved test
   account request. Model compatibility currently means documented support, not
   paid end-to-end validation on a real account.
4. **Source release review:** commit the intended source, licence and build changes,
   publish matching source, and review dependency distribution obligations. The local
   `reports/incidents.json` file has been kept on disk and removed from the Git
   index; generated reports/history are now ignored. Older Git revisions may still
   contain reports and require review before wider sharing. History was not rewritten.

The read-only readiness checker currently returns failure for missing clean-machine
evidence and the unsigned installer, as expected. A pending evidence file is at
`build/release-evidence-pending.json`; no manual check has been marked passed.

Follow [the release checklist](release-checklist.md). Once the artifact changes,
record its new hash and repeat the relevant installer checks before announcing it.

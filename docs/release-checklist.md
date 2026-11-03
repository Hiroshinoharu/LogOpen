# Public beta release checks

Run the tests and build the **exact installer** you intend to announce. Unit tests
and a packaged startup check do not replace clean-machine testing. Do not report
a check as passed until it has been performed on that artifact.

## Licence and source

LogOpen is GPL-3.0-only. Include LICENSE, THIRD_PARTY_NOTICES.md and the generated
THIRD_PARTY_NOTICES.txt. Publish the matching source tag, dependency locks and build
scripts alongside every release. Review dependency source/distribution obligations.

## Signing

Use an existing Windows code-signing certificate with an accessible private key:

```powershell
./scripts/build.ps1 -Installer -DownloadInno -RequireSigning -SigningThumbprint YOUR_40_HEX_THUMBPRINT -SigningStore CurrentUser
```

SignTool from the Windows SDK must be on PATH, or supply `-SignTool` with its path.
`-SigningStore LocalMachine` is also supported. Optional `-TimestampServer` selects
your RFC3161 service. The executable is signed **before** ZIP and installer creation;
the installer is then signed before release checksums are generated. Signatures
and timestamps are verified. No PFX/password is placed in source or command arguments.
Hardware-backed certificates may ask for a PIN through their own provider.
For hosted signing services, integrate the provider on a secured signing runner;
the certificate-store adapter does not claim to configure a hosted service.

CI builds remain unsigned unless a signing runner is explicitly configured. Never
put a private key or a certificate password in a public workflow. Signing does not
guarantee that a new application will avoid SmartScreen warnings.

## Clean Windows test

Use a disposable Windows VM or Windows Sandbox, with no developer environment,
Python installation or saved OpenAI key. Do not run installer experiments on a
machine containing valuable LogOpen data. Test every Windows version you advertise.

1. Run the installer as a standard user; verify shortcuts and launch without Python.
2. Scan System/Application without admin; confirm protected logs explain access limits.
3. Request admin restart; test both UAC cancellation and consent. Scan again.
4. Start monitoring, maximise/fullscreen and restore repeatedly; confirm visible rows
   and refreshing incidents. Stop monitoring before data removal.
5. Test offline startup, failed model discovery, invalid key, missing key and no results.
6. Review masked AI input; cancel and confirm no request. With a test account,
   explicitly approve a small paid request and verify structured output. Record
   the exact model ID. Do not use personal/workplace incident data for this test.
7. Confirm automatic sharing is initially off and enabling it is a distinct choice.
8. Export a problem report and check it for sensitive data before sharing.
9. Delete synthetic history; verify notes, review state and checklists also disappear.
   Remove a synthetic key and verify AI is disabled. Exported reports must remain.
10. Upgrade from the previous packaged release; verify settings and history survive.
11. Uninstall and reinstall; verify documented data retention and launch again.
12. Check 100%, 150%, 200% display scaling, keyboard navigation, both themes and large text.

Record results using `docs/release-evidence.example.json`, including artifact SHA256
and Windows build. Keep real user data, API keys and private certificate details out
of the record. Run the local readiness check before making an announcement:

```powershell
python scripts/release_readiness.py --installer dist/installer/LogOpen-VERSION-Setup-x64.exe --executable dist/LogOpen/LogOpen.exe --evidence build/release-evidence.json
```

This reports missing/failed checks and verifies signatures. It never installs,
publishes or silently marks manual checks as passed. A failed check is an unfinished
release task. The existing CI is still build/test automation, not proof of this audit.

Sources: https://learn.microsoft.com/en-us/windows/win32/seccrypto/signtool
and https://learn.microsoft.com/en-us/windows/apps/package-and-deploy/smartscreen-reputation

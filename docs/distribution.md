# Windows distribution

LogOpen can run from source, from an extracted desktop bundle, or from a per-user
Windows installer. The bundle includes Python, Qt, the Windows Event Log bindings,
and the optional AI client. Users do not need to install Python.

## Development setup

The dependency locks target **CPython 3.14 x64 on Windows** and match the project's
tested environment. `requirements.txt` includes pinned runtime dependencies and
their transitive dependencies. Tests and build tools are pinned separately.

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements-dev.txt
.\.venv\Scripts\python.exe -m pytest -q
.\.venv\Scripts\python.exe desktop.py
```

The existing `python -m ui.main_window` desktop and `python main.py` CLI entry
points remain available. The CLI scans immediately; desktop startup does not.

## Build the executable

GitHub Actions also runs this build automatically. Successful default-branch
pushes publish versioned releases; see [release automation](releases.md).

Run from the repository root:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build.ps1
```

The script creates `.build-venv`, installs the pinned build and test dependencies,
runs the test suite, packages `LogOpen.spec`, and starts a hidden UI check. It then
creates:

- `dist/LogOpen/LogOpen.exe`: desktop application; keep the adjacent `_internal`
  directory with it.
- `dist/LogOpen-0.1.0-Windows-x64.zip`: the complete directory for sharing.

To use an existing project environment:

```powershell
.\scripts\build.ps1 -Python .\LogOpen\Scripts\python.exe -UseExistingEnvironment
```

By default, the build uses the highest reachable stable version tag, falling back
to `VERSION` for an untagged source tree. `-Version 0.2.0` overrides the executable
properties, bundled VERSION, archive, and installer version together. It does not
publish or create tags. Build with `scripts/build.ps1`; the installer requires
its version to be supplied by the script (or explicitly with `/DAppVersion`).
`-SkipDependencyInstall` avoids package installation in an already prepared
environment. `-SkipTests` skips the unit suite, but the packaged startup check
still runs. Builds are version-pinned rather than byte-for-byte reproducible:
toolchain, OS, timestamps, and Python patch versions can change binary output.

## Build the installer

```powershell
.\scripts\build.ps1 -Installer -DownloadInno
```

This downloads the official **Inno Setup 7.1.0 x64** compiler, verifies its pinned
SHA256 hash and publisher signature, and extracts it into `build/tools` using
[Inno Setup's portable mode](https://jrsoftware.org/ishelp/topic_technotes.htm).
It does not install the compiler system-wide. An existing compiler can be supplied:

```powershell
.\scripts\build.ps1 -Installer -InnoCompiler 'C:\Tools\Inno Setup\ISCC.exe'
```

The installer is written to
`dist/installer/LogOpen-0.1.0-Setup-x64.exe`. It installs in
`%LOCALAPPDATA%\Programs\LogOpen`, adds a Start menu shortcut and a desktop
shortcut selected by default for new installations, and includes an uninstaller.
Users can deselect the desktop shortcut during setup or recreate it later from
**Settings → App → Create desktop shortcut**. Both use the LogOpen icon.
Installation requires no elevation.
Uninstall removes application files and shortcuts, preserving scan data,
preferences, and saved credentials.

The installer includes the short WAV effects under `assets/sounds` in the bundle.
**Settings → App** and **View → Sound effects** control their saved mute preference.

## Startup and data

`desktop.py` supplies safe output streams for the
[windowed PyInstaller process](https://pyinstaller.org/en/stable/common-issues-and-pitfalls.html#sys-stdin-sys-stdout-and-sys-stderr-in-noconsole-windowed-applications).
Packaged runs use `%LOCALAPPDATA%\LogOpen` as their working directory so relative
report exports remain writable. `LOGOPEN_DATA_DIR` overrides that location for
isolated environments. Source launches retain their original working directory.
The stylesheet is explicitly included in the
[PyInstaller specification](https://pyinstaller.org/en/stable/spec-files.html).
No saved reports, scan history, preferences, or API credentials enter the bundle.

The executable supports `--smoke-test`: it creates and closes the real window
offscreen using temporary application storage, verifies its stylesheet and native
Windows bindings, and constructs an offline AI client with a synthetic key. It
does not read saved credentials, start a scan, or make an AI request. The build
runs this check automatically with a 30-second timeout.

## Release status

The generated executable and installer are unsigned development artifacts. A
public release can use the same scripts with your own signing and release process.
Dependency metadata and bundled license files are retained under `_internal`.
LogOpen does not currently include automatic update downloading or installation.

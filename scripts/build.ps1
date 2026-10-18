<#
.SYNOPSIS
Build and smoke-test a Windows x64 desktop bundle, optionally an installer.
.DESCRIPTION
Creates an isolated .build-venv by default, installs pinned dependencies, runs
tests, packages the GUI, and validates startup without a visible window or scan.
UseExistingEnvironment chooses the supplied Python directly instead. DownloadInno
fetches the official pinned, SHA256- and signature-checked portable compiler into
build/tools only; it does not install software system-wide.
#>
[CmdletBinding()]
param(
    [string]$Python = "python",
    [ValidatePattern('^\d+\.\d+\.\d+(\.\d+)?$')]
    [string]$Version = "0.1.0",
    [switch]$UseExistingEnvironment,
    [switch]$SkipDependencyInstall,
    [switch]$SkipTests,
    [switch]$Installer,
    [string]$InnoCompiler,
    [switch]$DownloadInno
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$projectRoot = Split-Path -Parent $PSScriptRoot

function Invoke-Checked {
    <# Run a native command and stop the build on a nonzero exit status. #>
    param([string]$Program, [string[]]$Arguments)
    & $Program @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Program failed with exit code $LASTEXITCODE."
    }
}

function Get-InnoCompiler {
    <# Resolve a supplied/local compiler, or download the pinned portable edition. #>
    $portableDirectory = Join-Path $projectRoot "build\tools\InnoSetup-7.1.0"
    $portableCompiler = Join-Path $portableDirectory "ISCC.exe"
    if ($InnoCompiler) {
        return (Resolve-Path -LiteralPath $InnoCompiler).Path
    }
    if (Test-Path -LiteralPath $portableCompiler) { return $portableCompiler }
    $installedCompiler = Get-Command ISCC.exe -ErrorAction SilentlyContinue
    if ($installedCompiler) { return $installedCompiler.Source }
    if (-not $DownloadInno) {
        throw "Inno Setup was not found. Supply -InnoCompiler path\ISCC.exe or add -DownloadInno."
    }

    $toolDirectory = Split-Path -Parent $portableDirectory
    New-Item -ItemType Directory -Path $toolDirectory -Force | Out-Null
    $downloadPath = Join-Path $toolDirectory "innosetup-7.1.0-x64.exe"
    $downloadUrl = "https://github.com/jrsoftware/issrc/releases/download/is-7_1_0/innosetup-7.1.0-x64.exe"
    $expectedHash = "0362a383ed217d4c4239b5933866dd96d3eb2102737da92f80f6057a4b40df2f"
    Invoke-WebRequest -Uri $downloadUrl -OutFile $downloadPath
    if ((Get-FileHash -LiteralPath $downloadPath -Algorithm SHA256).Hash -ne $expectedHash) {
        throw "The Inno Setup download did not match the pinned SHA256 digest."
    }
    $signature = Get-AuthenticodeSignature -LiteralPath $downloadPath
    if ($signature.Status -ne "Valid" -or $signature.SignerCertificate.Subject -notmatch "Pyrsys") {
        throw "The Inno Setup download does not have the expected valid publisher signature."
    }
    $arguments = @(
        "/PORTABLE=1", "/CURRENTUSER", "/VERYSILENT", "/SUPPRESSMSGBOXES",
        "/NORESTART", "/SP-", "/NOICONS", "/TASKS=", ('/DIR="{0}"' -f $portableDirectory)
    )
    $extractProcess = Start-Process -FilePath $downloadPath -ArgumentList $arguments -WindowStyle Hidden -PassThru -Wait
    if ($extractProcess.ExitCode -ne 0 -or -not (Test-Path -LiteralPath $portableCompiler)) {
        throw "Portable Inno Setup preparation failed with exit code $($extractProcess.ExitCode)."
    }
    return $portableCompiler
}

Push-Location -LiteralPath $projectRoot
try {
    # Test the interpreter before downloading packages; all pins target this ABI.
    Invoke-Checked $Python @("-c", "import struct,sys; assert sys.platform == 'win32' and struct.calcsize('P') == 8 and sys.version_info[:2] == (3,14), 'Use CPython 3.14 x64 on Windows.'")
    $buildPython = $Python
    if (-not $UseExistingEnvironment) {
        $buildPython = Join-Path $projectRoot ".build-venv\Scripts\python.exe"
        if (-not (Test-Path -LiteralPath $buildPython)) {
            Invoke-Checked $Python @("-m", "venv", (Join-Path $projectRoot ".build-venv"))
        }
    }
    if (-not $SkipDependencyInstall) {
        Invoke-Checked $buildPython @("-m", "pip", "install", "-r", "requirements-build.txt", "-r", "requirements-dev.txt")
    }
    Invoke-Checked $buildPython @("-m", "pip", "check")
    if (-not $SkipTests) {
        Invoke-Checked $buildPython @("-m", "pytest", "-q")
    }
    Invoke-Checked $buildPython @("-m", "PyInstaller", "--noconfirm", "--clean", "LogOpen.spec")

    $executablePath = Join-Path $projectRoot "dist\LogOpen\LogOpen.exe"
    $smokeProcess = Start-Process -FilePath $executablePath -ArgumentList "--smoke-test" -WindowStyle Hidden -PassThru
    if (-not $smokeProcess.WaitForExit(30000)) {
        $smokeProcess.Kill()
        throw "The packaged UI did not finish its startup check within 30 seconds."
    }
    if ($smokeProcess.ExitCode -ne 0) {
        throw "The packaged UI startup check failed with exit code $($smokeProcess.ExitCode)."
    }

    $zipBase = Join-Path $projectRoot ("dist\LogOpen-{0}-Windows-x64" -f $Version)
    Invoke-Checked $buildPython @("-c", "import shutil,sys; shutil.make_archive(sys.argv[1], 'zip', 'dist', 'LogOpen')", $zipBase)
    if ($Installer) {
        $compiler = Get-InnoCompiler
        Invoke-Checked $compiler @("/Qp", "/DAppVersion=$Version", "/DSourceRoot=$projectRoot", (Join-Path $projectRoot "installer\LogOpen.iss"))
    }
    Write-Host "Verified application: $executablePath"
    Write-Host "Shareable bundle: $zipBase.zip"
    if ($Installer) {
        Write-Host ("Installer: " + (Join-Path $projectRoot "dist\installer\LogOpen-$Version-Setup-x64.exe"))
    }
} finally {
    Pop-Location
}

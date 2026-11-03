<# Sign a supplied LogOpen artifact with a certificate already in Windows storage.
   No PFX/password/private-key export. SignTool may prompt through the certificate provider. #>
[CmdletBinding()]
param(
    [Parameter(Mandatory)][string]$Path,
    [Parameter(Mandatory)][ValidatePattern('^[A-Fa-f0-9]{40}$')][string]$Thumbprint,
    [ValidateSet('CurrentUser', 'LocalMachine')][string]$Store = 'CurrentUser',
    [string]$SignTool = 'signtool.exe',
    [string]$TimestampServer = 'https://timestamp.digicert.com'
)
$ErrorActionPreference = 'Stop'
$artifact = (Resolve-Path -LiteralPath $Path).Path
if ([IO.Path]::GetExtension($artifact) -ne '.exe') { throw 'Only executable artifacts can be signed.' }
if (([Uri]$TimestampServer).Scheme -notin @('http', 'https')) { throw 'Use an HTTP(S) timestamp service.' }
$certificate = Get-Item -LiteralPath "Cert:\$Store\My\$Thumbprint"
if (-not $certificate.HasPrivateKey) { throw 'The selected certificate has no accessible private key.' }
if ($certificate.NotAfter -le (Get-Date)) { throw 'The signing certificate has expired.' }
$arguments = @('sign', '/sha1', $Thumbprint, '/s', 'My', '/fd', 'SHA256', '/tr', $TimestampServer, '/td', 'SHA256', '/d', 'LogOpen')
if ($Store -eq 'LocalMachine') { $arguments += '/sm' }
$arguments += $artifact
& $SignTool @arguments
if ($LASTEXITCODE -ne 0) { throw 'Signing or timestamping failed.' }
& $SignTool verify /pa /all $artifact
if ($LASTEXITCODE -ne 0) { throw 'Signature verification failed.' }
$signature = Get-AuthenticodeSignature -LiteralPath $artifact
if ($signature.Status -ne 'Valid' -or $signature.SignerCertificate.Thumbprint -ne $Thumbprint -or -not $signature.TimeStamperCertificate) {
    throw 'The artifact is not validly signed and timestamped by the expected certificate.'
}
Write-Host "Signed and verified: $artifact"

"""Check public GitHub releases and download verified Windows packages."""

from dataclasses import dataclass
import hashlib
import hmac
import json
import os
from pathlib import Path
import re
from tempfile import NamedTemporaryFile
from urllib.error import HTTPError, URLError
from urllib.parse import urlsplit
from urllib.request import HTTPRedirectHandler, Request, build_opener

from version_info import application_version

REPOSITORY = "Hiroshinoharu/LogOpen"
RELEASES_URL = f"https://github.com/{REPOSITORY}/releases"
LATEST_URL = f"https://api.github.com/repos/{REPOSITORY}/releases/latest"
_HOSTS = {"github.com", "api.github.com", "release-assets.githubusercontent.com",
          "objects.githubusercontent.com", "github-releases.githubusercontent.com"}
_MAX_PACKAGE_SIZE = 1024 * 1024 * 1024


class UpdateError(RuntimeError):
    """An update failure carrying only a fixed, display-safe explanation."""


class DownloadCancelled(UpdateError):
    def __init__(self):
        super().__init__("Download cancelled. No update was installed.")


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    version: str
    notes: str
    assets: tuple[ReleaseAsset, ...]

    @property
    def page_url(self):
        return f"{RELEASES_URL}/tag/v{self.version}"


def version_tuple(value):
    if not isinstance(value, str) or not re.fullmatch(r"(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)", value):
        raise UpdateError("The release has an unsupported version number.")
    result = tuple(int(part) for part in value.split("."))
    if any(part > 65535 for part in result):
        raise UpdateError("The release has an unsupported version number.")
    return result


def _trusted_url(url):
    try:
        parsed = urlsplit(url)
        return (parsed.scheme == "https" and parsed.hostname in _HOSTS
                and not parsed.username and not parsed.password and parsed.port in (None, 443))
    except (ValueError, TypeError):
        return False


class _GitHubRedirects(HTTPRedirectHandler):
    def redirect_request(self, request, response, code, message, headers, newurl):
        if not _trusted_url(newurl):
            raise UpdateError("GitHub returned an unsupported download location.")
        return super().redirect_request(request, response, code, message, headers, newurl)


def _open_url(url, *, accept="application/vnd.github+json"):
    if not _trusted_url(url):
        raise UpdateError("The update URL is not a supported GitHub location.")
    request = Request(url, headers={
        "Accept": accept, "User-Agent": f"LogOpen/{application_version()}",
        "X-GitHub-Api-Version": "2026-03-10",
    })
    return build_opener(_GitHubRedirects()).open(request, timeout=15)


def _bounded_read(url, limit, *, accept="application/vnd.github+json"):
    with _open_url(url, accept=accept) as response:
        content = response.read(limit + 1)
    if len(content) > limit:
        raise UpdateError("GitHub returned an unexpectedly large release response.")
    return content


def _release_asset(data, version, suffix, checksums):
    name = f"LogOpen-{version}-{suffix}"
    matches = [asset for asset in data if isinstance(asset, dict) and asset.get("name") == name]
    if not matches:
        return None
    asset = matches[0]
    url = f"{RELEASES_URL}/download/v{version}/{name}"
    size = asset.get("size")
    if (len(matches) != 1 or asset.get("state") != "uploaded" or asset.get("browser_download_url") != url
            or type(size) is not int or not 0 < size <= _MAX_PACKAGE_SIZE):
        raise UpdateError("The release package information is incomplete. Please try again later.")
    digest = asset.get("digest")
    if isinstance(digest, str) and re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
        checksum = digest[7:].lower()
    else:
        if not checksums:
            text = _bounded_read(f"{RELEASES_URL}/download/v{version}/SHA256SUMS.txt", 65536,
                                 accept="text/plain").decode("utf-8")
            for line in text.splitlines():
                match = re.fullmatch(r"([0-9a-fA-F]{64})\s+\*?([^/\\\s]+)", line)
                if match:
                    if match[2] in checksums:
                        raise UpdateError("The release checksums are ambiguous. Download was not started.")
                    checksums[match[2]] = match[1].lower()
        checksum = checksums.get(name)
        if not checksum:
            raise UpdateError("This release has no checksum for the update package. Please try again later.")
    return ReleaseAsset(name, url, size, checksum)


def latest_release():
    """Read the latest published stable release; no credentials or incident data are sent."""
    try:
        data = json.loads(_bounded_read(LATEST_URL, 2 * 1024 * 1024))
        if not isinstance(data, dict) or data.get("draft") or data.get("prerelease"):
            raise UpdateError("No published stable release is available yet.")
        tag = data.get("tag_name", "")
        if not isinstance(tag, str) or not tag.startswith("v"):
            raise UpdateError("The release has an unsupported version number.")
        version = tag[1:]
        version_tuple(version)
        assets = data.get("assets")
        if not isinstance(assets, list):
            raise UpdateError("No Windows update packages are available in this release.")
        checksums = {}
        packages = tuple(package for suffix in ("Setup-x64.exe", "Windows-x64.zip")
                         if (package := _release_asset(assets, version, suffix, checksums)))
        if not packages:
            raise UpdateError("No Windows update packages are available in this release.")
        notes = data.get("body")
        return Release(version, notes[:50000] if isinstance(notes, str) else "", packages)
    except HTTPError as error:
        if error.code == 404:
            raise UpdateError("No published release was found. Please check again later.") from None
        if error.code in (403, 429):
            raise UpdateError("GitHub is limiting update checks. Please try again later.") from None
        raise UpdateError("GitHub could not provide the update. Please try again later.") from None
    except (URLError, OSError):
        raise UpdateError("Could not reach GitHub. Check your internet connection and try again.") from None
    except (ValueError, TypeError, KeyError):
        raise UpdateError("GitHub returned unreadable release information. Please try again later.") from None


def download_release(asset, destination, *, cancel_event, progress):
    """Stream to a temporary file and atomically save only after size/SHA256 verification."""
    temporary = None
    try:
        if cancel_event.is_set():
            raise DownloadCancelled()
        # Validate again at the download boundary rather than trusting callers.
        if (not isinstance(asset, ReleaseAsset) or not _trusted_url(asset.url)
                or not asset.url.startswith(RELEASES_URL + "/download/")
                or not re.fullmatch(r"[0-9a-f]{64}", asset.sha256)
                or type(asset.size) is not int or not 0 < asset.size <= _MAX_PACKAGE_SIZE):
            raise UpdateError("The update package could not be verified.")
        destination = Path(destination)
        received = 0
        digest = hashlib.sha256()
        with NamedTemporaryFile(dir=destination.parent, prefix=".logopen-update-", suffix=".part", delete=False) as output:
            temporary = Path(output.name)
            with _open_url(asset.url, accept="application/octet-stream") as response:
                while True:
                    if cancel_event.is_set():
                        raise DownloadCancelled()
                    chunk = response.read(256 * 1024)
                    if not chunk:
                        break
                    received += len(chunk)
                    if received > asset.size:
                        raise UpdateError("The downloaded file does not match the release. Please try again.")
                    output.write(chunk)
                    digest.update(chunk)
                    progress(received, asset.size)
        if received != asset.size or not hmac.compare_digest(digest.hexdigest(), asset.sha256):
            raise UpdateError("The downloaded file did not pass verification. Please try again.")
        if cancel_event.is_set():
            raise DownloadCancelled()
        os.replace(temporary, destination)
        temporary = None
        return destination
    except HTTPError:
        raise UpdateError("GitHub could not provide this file. Check for updates again and retry.") from None
    except URLError:
        raise UpdateError("The download was interrupted. Check your connection and retry.") from None
    except OSError:
        raise UpdateError("The update could not be saved. Check the download location and try again.") from None
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)

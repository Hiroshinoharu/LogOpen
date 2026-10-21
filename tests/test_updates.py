"""Release parsing and atomic, cancellable downloads without network access."""

import hashlib
from io import BytesIO
import json
from threading import Event
from unittest.mock import Mock
from urllib.error import HTTPError
from urllib.request import Request

import pytest

import update_service as updates


def asset(content=b"synthetic installer", **changes):
    return updates.ReleaseAsset(**{
        "name": "LogOpen-1.2.3-Setup-x64.exe",
        "url": updates.RELEASES_URL + "/download/v1.2.3/LogOpen-1.2.3-Setup-x64.exe",
        "size": len(content), "sha256": hashlib.sha256(content).hexdigest(), **changes,
    })


def metadata(package=None):
    package = package or asset()
    return dict(tag_name="v1.2.3", draft=False, prerelease=False, body="Release notes <literal>", assets=[{
        "name": package.name, "browser_download_url": package.url, "state": "uploaded",
        "size": package.size, "digest": "sha256:" + package.sha256,
    }])


def test_latest_release_uses_stable_version_and_exact_repository_assets(monkeypatch):
    data = metadata()
    read = Mock(return_value=json.dumps(data).encode())
    monkeypatch.setattr(updates, "_bounded_read", read)
    release = updates.latest_release()
    assert release.version == "1.2.3"
    assert release.notes == "Release notes <literal>"
    assert release.assets == (asset(),)
    assert updates.version_tuple("1.10.0") > updates.version_tuple("1.9.9")
    read.assert_called_once_with(updates.LATEST_URL, 2 * 1024 * 1024)


def test_checksum_file_supports_assets_without_github_digest(monkeypatch):
    data = metadata()
    data["assets"][0]["digest"] = None
    checksums = f"{asset().sha256}  {asset().name}\n".encode()
    monkeypatch.setattr(updates, "_bounded_read", Mock(side_effect=[json.dumps(data).encode(), checksums]))
    assert updates.latest_release().assets == (asset(),)


@pytest.mark.parametrize("change", [
    {"browser_download_url": "https://example.com/installer.exe"}, {"size": 0},
    {"size": True}, {"size": 2 ** 40}, {"state": "new"},
])
def test_incomplete_or_redirected_package_metadata_is_rejected(monkeypatch, change):
    data = metadata()
    data["assets"][0].update(change)
    monkeypatch.setattr(updates, "_bounded_read", lambda *_args, **_kwargs: json.dumps(data).encode())
    with pytest.raises(updates.UpdateError):
        updates.latest_release()


@pytest.mark.parametrize("status,message", [(404, "No published release"), (403, "limiting"), (429, "limiting")])
def test_release_http_errors_are_actionable(monkeypatch, status, message):
    monkeypatch.setattr(updates, "_bounded_read", Mock(side_effect=HTTPError(updates.LATEST_URL, status, "private details", {}, None)))
    with pytest.raises(updates.UpdateError, match=message):
        updates.latest_release()


def test_successful_download_checks_bytes_before_replacing_destination(monkeypatch, tmp_path):
    content = b"synthetic installer"
    destination = tmp_path / asset().name
    destination.write_bytes(b"old download")
    monkeypatch.setattr(updates, "_open_url", lambda *_args, **_kwargs: BytesIO(content))
    progress = Mock()
    assert updates.download_release(asset(), destination, cancel_event=Event(), progress=progress) == destination
    assert destination.read_bytes() == content
    progress.assert_called_once_with(len(content), len(content))
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("content", [b"short", b"much longer than the expected installer", b"corrupted installer"])
def test_failed_verification_preserves_existing_file_and_cleans_partial(monkeypatch, tmp_path, content):
    destination = tmp_path / asset().name
    destination.write_bytes(b"existing file")
    monkeypatch.setattr(updates, "_open_url", lambda *_args, **_kwargs: BytesIO(content))
    with pytest.raises(updates.UpdateError):
        updates.download_release(asset(), destination, cancel_event=Event(), progress=Mock())
    assert destination.read_bytes() == b"existing file"
    assert not list(tmp_path.glob("*.part"))


def test_cancelled_download_removes_partial_without_publishing_file(monkeypatch, tmp_path):
    token = Event()
    destination = tmp_path / asset().name
    monkeypatch.setattr(updates, "_open_url", lambda *_args, **_kwargs: BytesIO(b"synthetic installer"))
    with pytest.raises(updates.DownloadCancelled):
        updates.download_release(asset(), destination, cancel_event=token, progress=lambda *_args: token.set())
    assert not destination.exists()
    assert not list(tmp_path.glob("*.part"))


@pytest.mark.parametrize("url", ["http://github.com/file", "https://example.com/file", "https://github.com@evil.example/file"])
def test_redirects_cannot_leave_https_github_hosts(url):
    with pytest.raises(updates.UpdateError):
        updates._GitHubRedirects().redirect_request(Request(updates.LATEST_URL), None, 302, "", {}, url)


def test_github_requests_do_not_send_openai_credentials(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-private-test-key")
    monkeypatch.setattr(updates, "application_version", lambda: "1.2.0")
    opener = Mock()
    monkeypatch.setattr(updates, "build_opener", lambda *_args: opener)
    updates._open_url(updates.LATEST_URL)
    request = opener.open.call_args.args[0]
    assert request.get_header("User-agent") == "LogOpen/1.2.0"
    assert not request.has_header("Authorization")
    assert "sk-private-test-key" not in str(request.headers)

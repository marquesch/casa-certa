import subprocess
from pathlib import Path

import pytest

from casa_certa.web.publish import PublishError, origin_url, pages_url, publish, redact_url


def git(*args: str, cwd: Path | None = None) -> str:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True, check=True).stdout.strip()


@pytest.fixture(autouse=True)
def git_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    for prefix in ("GIT_AUTHOR", "GIT_COMMITTER"):
        monkeypatch.setenv(f"{prefix}_NAME", "Tester")
        monkeypatch.setenv(f"{prefix}_EMAIL", "tester@example.com")


@pytest.fixture
def remote(tmp_path: Path) -> Path:
    path = tmp_path / "origin.git"
    git("init", "--bare", "-q", str(path))
    return path


@pytest.fixture
def site(tmp_path: Path) -> Path:
    path = tmp_path / "site"
    path.mkdir()
    (path / "index.html").write_text("<html>one</html>", encoding="utf8")
    (path / ".nojekyll").write_text("", encoding="utf8")
    return path


async def test_publish_pushes_one_commit_with_the_site_files(site: Path, remote: Path) -> None:
    await publish(site, str(remote))

    assert git("rev-list", "--count", "gh-pages", cwd=remote) == "1"
    assert git("log", "-1", "--format=%s", "gh-pages", cwd=remote) == "chore: publish site"
    assert sorted(git("ls-tree", "--name-only", "gh-pages", cwd=remote).split()) == [".nojekyll", "index.html"]
    assert git("show", "gh-pages:index.html", cwd=remote) == "<html>one</html>"


async def test_publishing_again_replaces_the_history(site: Path, remote: Path) -> None:
    await publish(site, str(remote))
    (site / "index.html").write_text("<html>two</html>", encoding="utf8")

    await publish(site, str(remote))

    assert git("rev-list", "--count", "gh-pages", cwd=remote) == "1"
    assert git("show", "gh-pages:index.html", cwd=remote) == "<html>two</html>"


async def test_publish_does_not_touch_the_site_directory_or_other_branches(site: Path, remote: Path) -> None:
    await publish(site, str(remote))
    assert git("branch", "--list", cwd=remote).strip("* ") == "gh-pages"
    assert not (site / ".git").exists()


async def test_publish_reports_git_failures(site: Path, tmp_path: Path) -> None:
    with pytest.raises(PublishError, match="push"):
        await publish(site, str(tmp_path / "does-not-exist.git"))


async def test_publish_refuses_a_missing_site(tmp_path: Path, remote: Path) -> None:
    with pytest.raises(PublishError, match="make site"):
        await publish(tmp_path / "nope", str(remote))


async def test_origin_url_reads_the_remote_of_a_repository(tmp_path: Path) -> None:
    git("init", "-q", str(tmp_path / "repo"))
    git("remote", "add", "origin", "git@github.com:someone/repo.git", cwd=tmp_path / "repo")
    assert await origin_url(tmp_path / "repo") == "git@github.com:someone/repo.git"


@pytest.mark.parametrize(
    ("origin", "expected"),
    [
        ("git@github.com:someone/casa-certa.git", "https://someone.github.io/casa-certa/"),
        ("https://github.com/someone/casa-certa.git", "https://someone.github.io/casa-certa/"),
        ("https://github.com/someone/casa-certa", "https://someone.github.io/casa-certa/"),
        ("/tmp/some/local.git", None),
    ],
)
def test_pages_url(origin: str, expected: str | None) -> None:
    assert pages_url(origin) == expected


async def test_publish_ignores_everything_but_the_site_files(site: Path, remote: Path) -> None:
    (site / "secret.txt").write_text("secret", encoding="utf8")
    (site / ".env").write_text("TOKEN=1", encoding="utf8")
    (site / "nested").mkdir()
    (site / "nested" / "file.txt").write_text("x", encoding="utf8")

    await publish(site, str(remote))

    assert sorted(git("ls-tree", "-r", "--name-only", "gh-pages", cwd=remote).split()) == [".nojekyll", "index.html"]


async def test_publish_works_without_nojekyll(site: Path, remote: Path) -> None:
    (site / ".nojekyll").unlink()
    await publish(site, str(remote))
    assert git("ls-tree", "--name-only", "gh-pages", cwd=remote).split() == ["index.html"]


@pytest.mark.parametrize(
    ("url", "expected"),
    [
        ("https://token@github.com/a/b.git", "https://github.com/a/b.git"),
        ("https://user:pw@github.com/a/b.git", "https://github.com/a/b.git"),
        ("https://github.com/a/b.git", "https://github.com/a/b.git"),
        ("git@github.com:a/b.git", "git@github.com:a/b.git"),
        ("/tmp/some/local.git", "/tmp/some/local.git"),
    ],
)
def test_redact_url(url: str, expected: str) -> None:
    assert redact_url(url) == expected

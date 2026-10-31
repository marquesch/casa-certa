"""Publishes the built site as a single commit on the gh-pages branch, from a temporary repository."""

import asyncio
import re
import shutil
import tempfile
from pathlib import Path

BRANCH = "gh-pages"
COMMIT_MESSAGE = "chore: publish site"
_GITHUB_URL = re.compile(r"(?:git@github\.com:|https://github\.com/)(?P<owner>[^/]+)/(?P<repo>[^/]+?)(?:\.git)?/?$")


class PublishError(Exception):
    """A git step failed, or there is nothing to publish."""


async def _git(cwd: Path, *args: str) -> str:
    process = await asyncio.create_subprocess_exec(
        "git", *args, cwd=cwd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE
    )
    stdout, stderr = await process.communicate()
    if process.returncode != 0:
        raise PublishError(f"git {args[0]} failed: {stderr.decode().strip()}")
    return stdout.decode().strip()


async def origin_url(repository: Path) -> str:
    return await _git(repository, "remote", "get-url", "origin")


def pages_url(origin: str) -> str | None:
    """The GitHub Pages address for a GitHub remote, or None for any other remote."""
    match = _GITHUB_URL.search(origin)
    if match is None:
        return None
    return f"https://{match['owner']}.github.io/{match['repo']}/"


async def publish(site_dir: Path, origin: str) -> None:
    """Force-push the site files as one orphan commit to the gh-pages branch of `origin`.

    The working tree, `master` and every local branch are untouched: the commit is built in a temporary repository."""
    if not (site_dir / "index.html").is_file():
        raise PublishError(f"No site in {site_dir}: run `make site` first.")
    with tempfile.TemporaryDirectory() as temporary:
        work = Path(temporary)
        await asyncio.to_thread(shutil.copytree, site_dir, work, dirs_exist_ok=True)
        await _git(work, "init", "-q", "-b", BRANCH)
        await _git(work, "add", "--all")
        await _git(work, "commit", "-q", "-m", COMMIT_MESSAGE)
        await _git(work, "push", "--force", origin, f"HEAD:refs/heads/{BRANCH}")

"""
Shared helper: commit agent-proposed changes and push them back to the PR branch.

Without this, spec_updater.py and test_updater.py write files into the CI
workspace that vanish when the runner is torn down — the change shows up in the
aggregated PR comment as a diff, but never lands as a commit.

Branch resolution is the fiddly part. On a `pull_request` event actions/checkout
leaves the workspace on a detached HEAD at the merge commit, so a bare
`git push` has no upstream to push to. We always push explicitly to the PR head
branch, taken from --branch or $GITHUB_HEAD_REF.
"""
import os
import subprocess

BOT_NAME = "github-actions[bot]"
BOT_EMAIL = "41898282+github-actions[bot]@users.noreply.github.com"


def _run(args, **kwargs):
    return subprocess.run(args, capture_output=True, text=True, **kwargs)


def _ensure_identity():
    """CI runners have no committer identity; set one locally if absent."""
    if not _run(["git", "config", "user.email"]).stdout.strip():
        _run(["git", "config", "user.email", BOT_EMAIL])
    if not _run(["git", "config", "user.name"]).stdout.strip():
        _run(["git", "config", "user.name", BOT_NAME])


def resolve_branch(explicit=None):
    return explicit or os.environ.get("GITHUB_HEAD_REF") or ""


def commit_and_push(paths, message, branch=None, remote="origin") -> bool:
    """Stage `paths`, commit, push to `branch`. Returns True only if pushed.

    Never raises: a fork PR has a read-only GITHUB_TOKEN, and a push failure
    there must not kill the loop — the diff still reaches the PR comment.
    """
    paths = [p for p in paths if p]
    if not paths:
        print("git-publish: nothing written, skipping commit.")
        return False

    _run(["git", "add", "--"] + paths)

    # returncode 0 from --quiet means no staged difference
    if _run(["git", "diff", "--cached", "--quiet", "--"] + paths).returncode == 0:
        print("git-publish: no staged changes, skipping commit.")
        return False

    _ensure_identity()
    commit = _run(["git", "commit", "-m", message])
    if commit.returncode != 0:
        print(f"git-publish: WARNING commit failed, changes not landed:\n{commit.stderr}")
        return False

    branch = resolve_branch(branch)
    if not branch:
        print("git-publish: WARNING no target branch (set --branch or $GITHUB_HEAD_REF); "
              "committed locally but not pushed.")
        return False

    push = _run(["git", "push", remote, f"HEAD:refs/heads/{branch}"])
    if push.returncode != 0:
        print(f"git-publish: WARNING push to {branch} failed, committed locally only "
              f"(expected for fork PRs — GITHUB_TOKEN is read-only there):\n{push.stderr}")
        return False

    print(f"git-publish: committed and pushed {len(paths)} path(s) to {branch}.")
    return True

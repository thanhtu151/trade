"""Conflict-safe state-branch publisher: bounded retry, never merges state files."""

import argparse
import subprocess
import time
from pathlib import Path


class StateConflict(RuntimeError):
    pass


def _git(worktree, *args, check=True):
    return subprocess.run(
        ["git", "-C", str(worktree), *args], check=check,
        capture_output=True, text=True, encoding="utf-8", errors="replace",
    )


def publish(worktree, expected_sha, task, attempts=3, sleeper=time.sleep):
    worktree = Path(worktree)
    _git(worktree, "add", "-A")
    if _git(worktree, "diff", "--cached", "--quiet", check=False).returncode == 0:
        return "unchanged"
    _git(worktree, "commit", "-m", f"state: after {task}")
    expected_sha = str(expected_sha or "").strip()
    last_error = None
    for attempt in range(max(1, int(attempts))):
        _git(worktree, "fetch", "origin", "state", check=False)
        remote = _git(worktree, "rev-parse", "--verify", "refs/remotes/origin/state", check=False)
        remote_sha = remote.stdout.strip() if remote.returncode == 0 else ""
        if remote_sha != expected_sha:
            raise StateConflict(
                f"state branch changed concurrently: loaded {expected_sha or '<none>'}, now {remote_sha or '<none>'}; refusing merge"
            )
        pushed = _git(worktree, "push", "origin", "HEAD:state", check=False)
        if pushed.returncode == 0:
            return "pushed"
        last_error = (pushed.stderr or pushed.stdout).strip()
        if attempt + 1 < attempts:
            sleeper(min(8.0, 2.0 ** attempt))
    raise RuntimeError(f"state push failed after {attempts} attempts: {last_error}")


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--worktree", required=True)
    parser.add_argument("--expected-sha", default="")
    parser.add_argument("--task", required=True)
    args = parser.parse_args()
    print(publish(args.worktree, args.expected_sha, args.task))


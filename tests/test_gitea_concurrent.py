"""Regression tests for concurrent populate_repo() safety.

Root cause: two harness processes calling populate_repo() for the same
(instance_id, repo_name) key simultaneously raced on `git push --force`
to the same Gitea remote.  The fix serialises them with fcntl.flock.

These tests use threads (not processes) to verify the fix because
fcntl.flock on Linux creates per-open-file-description locks: each
thread's separate open() call gets an independent lock, so mutual
exclusion works correctly within a single process.
"""

from __future__ import annotations

import threading
import time
from pathlib import Path

import pytest

from amplifier_eval_harness.gitea import GiteaSession, populate_repo


@pytest.fixture()
def session() -> GiteaSession:
    return GiteaSession(
        instance_id="test-instance-id",
        port=10110,
        url="http://localhost:10110",
        token="test-token",
    )


# ---------------------------------------------------------------------------
# Primary regression test: same (instance, repo) key must be serialised
# ---------------------------------------------------------------------------


def test_populate_repo_concurrent_same_key_serialised(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: GiteaSession
) -> None:
    """Two concurrent calls for the same key must not overlap.

    Without the lock, both threads would call snapshot_push simultaneously
    and the second `git push --force` would fail.  With the lock, they queue.
    """
    monkeypatch.setattr("amplifier_eval_harness.gitea._LOCK_DIR", tmp_path / "locks")

    # Record enter/exit events in the order they actually happen.
    call_log: list[str] = []
    log_lock = threading.Lock()

    def fake_snapshot_push(sess: GiteaSession, local_path: Path, repo_name: str) -> None:
        with log_lock:
            call_log.append("enter")
        time.sleep(0.05)  # long enough for second thread to contend on the flock
        with log_lock:
            call_log.append("exit")

    monkeypatch.setattr("amplifier_eval_harness.gitea.snapshot_push", fake_snapshot_push)

    errors: list[Exception] = []

    def worker() -> None:
        try:
            populate_repo(
                session,
                repo_owner="microsoft",
                repo_name="shared-repo",
                local_path=tmp_path,  # truthy → snapshot_push branch
            )
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker)
    t2 = threading.Thread(target=worker)
    t1.start()
    t2.start()
    t1.join()
    t2.join()

    assert not errors, f"populate_repo() raised during concurrent run: {errors}"
    assert len(call_log) == 4, f"Expected 4 events (enter/exit × 2), got: {call_log}"

    # With correct serialisation the pattern must be [enter, exit, enter, exit].
    # A race produces [enter, enter, exit, exit] or similar.
    assert call_log == ["enter", "exit", "enter", "exit"], f"Calls overlapped — flock did not serialise: {call_log}"


# ---------------------------------------------------------------------------
# Complementary test: *different* repos must NOT block each other
# ---------------------------------------------------------------------------


def test_populate_repo_different_repos_run_concurrently(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: GiteaSession
) -> None:
    """Different repo names use different lock files and must not block each other."""
    monkeypatch.setattr("amplifier_eval_harness.gitea._LOCK_DIR", tmp_path / "locks")

    both_inside = threading.Event()
    entered: list[str] = []
    entered_lock = threading.Lock()

    def fake_snapshot_push(sess: GiteaSession, local_path: Path, repo_name: str) -> None:
        with entered_lock:
            entered.append(repo_name)
            if len(entered) == 2:
                both_inside.set()
        # Hold until both threads have entered, proving no mutual exclusion.
        assert both_inside.wait(timeout=3.0), (
            "Timed out waiting for both threads to enter — they may have been serialised"
        )

    monkeypatch.setattr("amplifier_eval_harness.gitea.snapshot_push", fake_snapshot_push)

    errors: list[Exception] = []

    def worker(repo_name: str) -> None:
        try:
            populate_repo(session, repo_owner="microsoft", repo_name=repo_name, local_path=tmp_path)
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    t1 = threading.Thread(target=worker, args=("repo-alpha",))
    t2 = threading.Thread(target=worker, args=("repo-beta",))
    t1.start()
    t2.start()
    t1.join(timeout=5.0)
    t2.join(timeout=5.0)

    assert not errors
    assert both_inside.is_set(), "Both threads must have entered fake_snapshot_push concurrently"
    assert set(entered) == {"repo-alpha", "repo-beta"}


# ---------------------------------------------------------------------------
# Smoke test: lock directory is created automatically
# ---------------------------------------------------------------------------


def test_populate_repo_creates_lock_dir(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, session: GiteaSession) -> None:
    """_LOCK_DIR is created on demand; populate_repo() must not require it pre-existing."""
    lock_dir = tmp_path / "does" / "not" / "exist" / "yet"
    monkeypatch.setattr("amplifier_eval_harness.gitea._LOCK_DIR", lock_dir)

    monkeypatch.setattr("amplifier_eval_harness.gitea.snapshot_push", lambda *a, **kw: None)

    populate_repo(session, repo_owner="microsoft", repo_name="some-repo", local_path=tmp_path)

    assert lock_dir.is_dir(), f"Lock directory was not created: {lock_dir}"
    lock_file = lock_dir / f"{session.instance_id}-some-repo.lock"
    assert lock_file.exists(), f"Lock file was not created: {lock_file}"

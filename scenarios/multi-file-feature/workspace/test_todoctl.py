"""Tests for the todoctl CLI. The 4 tests below define the EXISTING contract.

You may add NEW tests below. Do not modify or delete the existing 4.
"""

import subprocess

WORKDIR = "/workspace"


def _run(*args, file):
    return subprocess.run(
        ["python", "-m", "todoctl", "--file", str(file), *args],
        capture_output=True,
        text=True,
        cwd=WORKDIR,
    )


def test_add_then_list_shows_item(tmp_path):
    f = tmp_path / "todo.json"
    r = _run("add", "buy milk", file=f)
    assert r.returncode == 0
    r = _run("list", file=f)
    assert r.returncode == 0
    assert "buy milk" in r.stdout
    assert "[ ]" in r.stdout


def test_done_marks_item_done(tmp_path):
    f = tmp_path / "todo.json"
    _run("add", "task1", file=f)
    r = _run("done", "1", file=f)
    assert r.returncode == 0
    r = _run("list", file=f)
    assert "[x]" in r.stdout


def test_empty_store_prints_no_items(tmp_path):
    f = tmp_path / "empty.json"
    r = _run("list", file=f)
    assert r.returncode == 0
    assert "no items" in r.stdout.lower()


def test_done_unknown_id_errors(tmp_path):
    f = tmp_path / "todo.json"
    _run("add", "x", file=f)
    r = _run("done", "999", file=f)
    assert r.returncode != 0

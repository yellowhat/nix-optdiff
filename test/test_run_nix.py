"""Offline tests for run_nix and main error paths (a python script stands in for nix)."""

import sys
from io import StringIO

import pytest

import main
from main import run_nix

MARKER = "trace: TRACE_CONFIG"


@pytest.fixture(name="err", autouse=True)
def fixture_err(monkeypatch: pytest.MonkeyPatch) -> StringIO:
    """Set the trace marker and capture main's stderr (bound at import time)."""
    monkeypatch.setitem(main.internal, "marker", MARKER)
    err = StringIO()
    monkeypatch.setattr("main.stderr", err)
    return err


def fake_nix(script: str) -> list[str]:
    """Return a command that runs a python script in place of nix."""
    return [sys.executable, "-c", f"import sys\n{script}"]


def test_traces_are_split_from_other_stderr(err: StringIO) -> None:
    """Marker lines become traces; other stderr passes through; stdout is returned."""
    out, traces = run_nix(
        fake_nix(
            "sys.stdout.write('result')\n"
            f'sys.stderr.write(\'{MARKER}["new","a","1"]\\n\')\n'
            "sys.stderr.write('warning: x\\n')"
        )
    )
    assert out == "result"
    assert traces == ['["new","a","1"]\n']
    assert err.getvalue() == "warning: x\n"


def test_nonzero_exit_dies_with_its_code() -> None:
    """A failing nix exits with nix's code."""
    with pytest.raises(SystemExit) as exc:
        run_nix(fake_nix("sys.exit(3)"))
    assert exc.value.code == 3


def test_invalid_utf8_is_replaced(err: StringIO) -> None:
    """Invalid UTF-8 from nix neither crashes nor hangs the reader."""
    out, _ = run_nix(
        fake_nix(
            "sys.stdout.buffer.write(b'a\\xff')\nsys.stderr.buffer.write(b'b\\xff\\n')"
        )
    )
    assert out == "a\ufffd"
    assert err.getvalue() == "b\ufffd\n"


def test_empty_traces_fail(monkeypatch: pytest.MonkeyPatch, err: StringIO) -> None:
    """No trace output is an error, not an empty diff with exit 0."""
    cli = ["nix-optdiff", "a#x", "b#y"]
    monkeypatch.setattr("sys.argv", cli)
    monkeypatch.setattr("main.argv", cli)
    monkeypatch.setattr("main.run_flake", lambda args, nix_args: [])
    with pytest.raises(SystemExit) as exc:
        main.main()
    assert exc.value.code == 1
    assert "no trace output" in err.getvalue()

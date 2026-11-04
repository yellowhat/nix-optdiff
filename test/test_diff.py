"""Offline unit tests for the diff logic in main.py (no nix needed).

The trace lines below are hand-written in the format nix-optdiff reads from
nix's stderr, after the marker is removed: a JSON list [label, path, value].
"""

from argparse import Namespace
from contextlib import redirect_stdout
from io import StringIO
from json import dumps

import pytest

from main import die, flatten, norm_store_paths, optional_arg, print_diff

OLD_PATH = "/nix/store/" + "a" * 32 + "-hello"
NEW_PATH = "/nix/store/" + "b" * 32 + "-hello"


def trace(label: str, path: str, value: str) -> str:
    """Build one trace line, as run_nix returns it."""
    return dumps([label, path, value])


def diff(*lines: str, include_hashes: bool = False) -> list[str]:
    """Run print_diff on trace lines and return the printed lines."""
    args = Namespace(include_hashes=include_hashes)
    out = StringIO()
    with redirect_stdout(out):
        print_diff(args, lines)
    return out.getvalue().splitlines()


def test_single_line_change() -> None:
    """A one-line value puts the key on each line."""
    lines = [trace("old", "a.enable", "false"), trace("new", "a.enable", "true")]
    assert diff(*lines) == [
        "- a.enable = false",
        "+ a.enable = true",
    ]


def test_added_and_removed() -> None:
    """A path on only one side shows as a pure add or remove."""
    assert diff(trace("new", "added", "1"), trace("old", "removed", "2")) == [
        "+ added = 1",
        "- removed = 2",
    ]


def test_unchanged_is_silent() -> None:
    """Equal values print nothing."""
    assert not diff(trace("old", "a", "1"), trace("new", "a", "1"))


def test_multi_line_block() -> None:
    """A multi-line value prints an @@ block and counts unchanged runs."""
    old = '[\n  "a"\n  "b"\n]'
    new = '[\n  "a"\n  "b"\n  "c"\n]'
    assert diff(trace("old", "list", old), trace("new", "list", new)) == [
        "@@ list @@",
        "  ... 3 unchanged",
        '+   "c"',
        "  ... 1 unchanged",
    ]


def test_values_of_one_path_accumulate() -> None:
    """Several trace lines for one path (e.g. list items) form one value."""
    lines = [
        trace("old", "xs.*", "1"),
        trace("new", "xs.*", "1"),
        trace("new", "xs.*", "2"),
    ]
    assert diff(*lines) == ["@@ xs.* @@", "  ... 1 unchanged", "+ 2"]


def test_output_is_sorted_by_path() -> None:
    """Paths print in sorted order, whatever order the traces arrive in."""
    lines = [trace("new", "b", "1"), trace("new", "a", "1")]
    assert diff(*lines) == ["+ a = 1", "+ b = 1"]


def test_hash_only_change_is_hidden() -> None:
    """By default a change of only the store path hash prints nothing."""
    assert not diff(trace("old", "pkg", OLD_PATH), trace("new", "pkg", NEW_PATH))


def test_include_hashes_shows_hash_change() -> None:
    """--include-hashes shows a hash-only change."""
    lines = [trace("old", "pkg", OLD_PATH), trace("new", "pkg", NEW_PATH)]
    assert diff(*lines, include_hashes=True) == [
        f"- pkg = {OLD_PATH}",
        f"+ pkg = {NEW_PATH}",
    ]


def test_hash_lines_count_as_unchanged() -> None:
    """In a block, a line that differs only by hash counts as unchanged but keeps its real text."""
    old = f"{OLD_PATH}\nfalse"
    new = f"{NEW_PATH}\ntrue"
    assert diff(trace("old", "p", old), trace("new", "p", new)) == [
        "@@ p @@",
        "  ... 1 unchanged",
        "- false",
        "+ true",
    ]


def test_norm_store_paths() -> None:
    """Different hashes normalize to the same text."""
    assert norm_store_paths(OLD_PATH) == norm_store_paths(NEW_PATH)
    assert norm_store_paths("/nix/store/short") == "/nix/store/short"
    # e, o, t, u are not in nix's base32 alphabet, so this is not a store hash
    not_hash = "/nix/store/" + "e" * 32 + "-x"
    assert norm_store_paths(not_hash) == not_hash


def test_value_splits_only_on_newline() -> None:
    """Other line separators (here U+2028) stay in one value line."""
    assert diff(trace("new", "k", "a\u2028b")) == ["+ k = a\\u2028b"]


def test_flatten() -> None:
    """Nested command args flatten in order."""
    assert flatten(["a", ["b", ("c",)], "d"]) == ["a", "b", "c", "d"]


def test_optional_arg() -> None:
    """A set option becomes a camelCase --argstr; an unset one disappears."""
    args = Namespace(old_module="{ }", eval=None)
    assert optional_arg(args, "old_module") == ["--argstr", "oldModule", "{ }"]
    assert not optional_arg(args, "eval")


@pytest.mark.parametrize(("code", "expected"), [(1, 1), (2, 2), (-9, 137)])
def test_die_exit_code(
    code: int, expected: int, monkeypatch: pytest.MonkeyPatch
) -> None:
    """die prints to stderr and maps a signal (negative code) to 128+N."""
    # main bound sys.stderr at import time, so capsys can't see it
    err = StringIO()
    monkeypatch.setattr("main.stderr", err)
    with pytest.raises(SystemExit) as exc:
        die("boom", code)
    assert exc.value.code == expected
    assert err.getvalue() == "error: boom\n"


def test_control_chars_are_escaped() -> None:
    """Terminal escape codes in keys and values print as text."""
    lines = [trace("old", "a\x1bb", "x"), trace("new", "a\x1bb", "\x1b[2Jy\tz")]
    assert diff(*lines) == [
        "- a\\x1bb = x",
        "+ a\\x1bb = \\x1b[2Jy\tz",
    ]

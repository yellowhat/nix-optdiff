"""Diff how nixpkgs lib.evalModules gets used between configurations."""

from argparse import ArgumentParser, Namespace
from collections.abc import Iterable, Sequence
from difflib import SequenceMatcher
from json import loads
from os.path import join
from re import compile as re_compile
from shutil import copyfile
from subprocess import PIPE, Popen  # nosec B404
from sys import argv, stderr
from sys import exit as sys_exit
from tempfile import TemporaryDirectory
from threading import Thread
from typing import NoReturn

parser = ArgumentParser(usage="""%(prog)s [OPTIONS] ARGS [-- NIX_ARGS]

diff how nixpkgs lib.evalModules gets used between configurations

examples:
    %(prog)s {old,new}#nixosConfigurations.machine
    %(prog)s flake#nixosConfigurations.{machine,other}
    %(prog)s ~/flake-repo{?ref=HEAD,}#nixosConfigurations.machine

    %(prog)s flake#nixosConfigurations.machine -- --override-input new/nixpkgs nixpkgs/nixos-unstable-small
    %(prog)s flake#nixosConfigurations.machine --new-module '{ services.postgresql.enable = true; }'
    %(prog)s flake#homeConfigurations.user --new-module '{ programs.git.enable = true; }'
    %(prog)s flake#nixvimConfiguration --new-module '{ lsp.servers.ty.enable = true; }'
""")

parser.add_argument(
    "old",
    nargs="?",
    help="flake installable for old configuration, e.g. old#nixosConfigurations.foo",
)
parser.add_argument("new", nargs="?", help="flake installable for new configuration")
parser.add_argument(
    "-i",
    "--include-hashes",
    action="store_true",
    help="include changes when the only difference is a store path hash",
)
parser.add_argument(
    "--old-module",
    help="the text of a module that gets injected into old, "
    "e.g. '{ services.postgresql.enable = true; }'. if this is set, only one flake is needed",
)
parser.add_argument(
    "--new-module", help="the text of a module that gets injected into new"
)
parser.add_argument(
    "--eval",
    help="nix path in config to evaluate for trace, e.g. system.build.toplevel.outPath",
)
parser.add_argument("--usage", action="store_true", help="print usage section of help")

internal: dict[str, str] = {}
# NIX_INTERNAL

SPINNER_CHARS = "/-\\"
# nix's base32 alphabet: digits and lowercase letters except e, o, t, u
STORE_HASH_PATTERN = re_compile(r"/nix/store/[0-9a-df-np-sv-z]{32}-")

type CmdArg = str | Sequence[CmdArg]


def die(message: str, exit_code: int = 1) -> NoReturn:
    """Print an error and exit."""
    print(f"error: {message}", file=stderr)
    # a signal-killed child has a negative returncode; map it to the shell convention 128+N
    sys_exit(exit_code if exit_code > 0 else 128 - exit_code)


def flatten(x: CmdArg) -> list[str]:
    """Recursively flatten nested lists/tuples into a flat list."""
    if isinstance(x, str):
        return [x]
    return [y for item in x for y in flatten(item)]


def run_nix(*cmdline: CmdArg) -> tuple[str, list[str]]:
    """Run a nix command, returning its stdout and the trace lines from stderr."""
    run_args = flatten(cmdline)
    show_spinner = stderr.isatty()
    spinner_idx = 0
    stdout_parts: list[str] = []
    reader_errors: list[BaseException] = []
    traces: list[str] = []
    # argv list, no shell: argv[0] is always a hardcoded nix binary.
    # errors="replace": nix output may hold invalid UTF-8; U+FFFD keeps trace JSON valid
    with Popen(  # nosec B603
        run_args, encoding="utf-8", errors="replace", stderr=PIPE, stdout=PIPE
    ) as proc:
        out, err = proc.stdout, proc.stderr
        if out is None or err is None:
            die("failed to open nix stdout/stderr pipes")

        def drain_stdout() -> None:
            try:
                stdout_parts.append(out.read())
            except BaseException as exc:  # pylint: disable=broad-exception-caught
                reader_errors.append(exc)  # re-raised in the main thread below

        # drain stdout in a thread so a full pipe can't block nix while we read stderr
        reader = Thread(target=drain_stdout)
        reader.start()
        for line in err:
            if line.startswith(internal["marker"]):
                if show_spinner:
                    stderr.write(SPINNER_CHARS[spinner_idx] + "\r")
                    spinner_idx = (spinner_idx + 1) % len(SPINNER_CHARS)
                traces.append(line.removeprefix(internal["marker"]))
            else:
                stderr.write(line)
        reader.join()
    if reader_errors:
        raise reader_errors[0]
    if show_spinner:
        stderr.write("\r\033[1K")
    if proc.returncode:
        die("nix had non-zero exit code", proc.returncode)
    return "".join(stdout_parts), traces


def run_nix_str(*cmdline: CmdArg) -> str:
    """Run a nix command and return its stdout as a string."""
    return run_nix(*cmdline)[0]


def get_flake_path(ref: str) -> tuple[str, str]:
    """Return the store path of a flake ref's source tree and its dir in it."""
    run_nix_str("nix", "flake", "archive", ref)
    data = loads(run_nix_str("nix", "flake", "metadata", "--json", ref))
    return data["path"], data["resolved"].get("dir", "")


def flake_args(name: str, ref: str) -> list[str]:
    """Pass a flake's source tree and dir to mkFlake."""
    path, subdir = get_flake_path(ref)
    return ["--arg", name, path, "--argstr", f"{name}Dir", subdir]


def optional_arg(args: Namespace, name: str) -> list[str]:
    """Turn a cli option into a nix --argstr, if it is set."""
    arg = getattr(args, name)
    if not arg:
        return []
    parts = name.split("_")
    nix_name = "".join(parts[:1] + [part.capitalize() for part in parts[1:]])
    return ["--argstr", nix_name, arg]


def run_flake(args: Namespace, nix_args: list[str]) -> list[str]:
    """Build the trace flake and evaluate it, returning trace lines."""
    if not (args.new and args.old):
        parser.error("missing required args")
    if "#" not in args.old or "#" not in args.new:
        die("expected flake installables, e.g. flake#nixosConfigurations.machine")
    old_ref, old_output = args.old.split("#", 1)
    new_ref, new_output = args.new.split("#", 1)
    trace_flake = run_nix_str(
        ["nix-build", "--no-build-output", internal["self_nix"]],
        ["--attr", f"{internal['self_nix_attr']}.mkFlake"],
        optional_arg(args, "eval"),
        "--no-link",
        flake_args("nix-optdiff", internal["self_flake"]),
        flake_args("old", old_ref),
        flake_args("new", new_ref),
        ["--argstr", "oldOutput", old_output],
        ["--argstr", "newOutput", new_output],
        optional_arg(args, "old_module"),
        optional_arg(args, "new_module"),
    ).strip()
    # lock outside the nix build: the sandbox has no network and maybe no store db access
    with TemporaryDirectory(prefix="nix-optdiff-") as tmp:
        copyfile(join(trace_flake, "flake.nix"), join(tmp, "flake.nix"))
        run_nix_str("nix", "flake", "lock", f"path:{tmp}")
        return run_nix("nix", "eval", "--raw", f"path:{tmp}#traced", nix_args)[1]


def norm_store_paths(text: str) -> str:
    """Replace store path hashes with a fixed placeholder."""
    return STORE_HASH_PATTERN.sub(f"/nix/store/{'A' * 32}-", text)


def diff_lines(
    old: list[str], new: list[str], old_seq: list[str], new_seq: list[str]
) -> list[tuple[str, str]]:
    """Line diff, returning (prefix, line) pairs; prefix is "-", "+" or " "."""
    matcher = SequenceMatcher(a=old_seq, b=new_seq, autojunk=False)
    results: list[tuple[str, str]] = []
    for tag, i1, i2, j1, j2 in matcher.get_opcodes():
        if tag == "equal":
            results.append((" ", f"... {i2 - i1} unchanged"))
            continue
        results.extend(("-", line) for line in old[i1:i2])
        results.extend(("+", line) for line in new[j1:j2])
    return results


def escape(text: str) -> str:
    """Escape non-printable characters so they can't drive the terminal."""
    return "".join(c if c.isprintable() or c == "\t" else repr(c)[1:-1] for c in text)


def diff_item(args: Namespace, key: str, old: list[str], new: list[str]) -> None:
    """Print the diff of one config path, if it changed."""
    old_seq, new_seq = old, new
    if not args.include_hashes:
        old_seq = [norm_store_paths(line) for line in old]
        new_seq = [norm_store_paths(line) for line in new]
    if old_seq == new_seq:
        return
    results = [(p, escape(line)) for p, line in diff_lines(old, new, old_seq, new_seq)]
    key = escape(key)
    if len(old) <= 1 and len(new) <= 1:
        # single-line values: put the key on each line, like `- key = old`
        for prefix, line in results:
            print(prefix, key, "=", line)
        return
    print(f"@@ {key} @@")
    for prefix, line in results:
        print(prefix, line)


def print_diff(args: Namespace, trace_lines: Iterable[str]) -> None:
    """Group trace lines by config path and print each diff."""
    items: dict[str, dict[str, list[str]]] = {}
    for label, path, value in map(loads, trace_lines):
        # split("\n"), not splitlines(): that also splits on \v, \f, \x1c, \u2028, ...
        items.setdefault(path, {"old": [], "new": []})[label].extend(value.split("\n"))
    for path in sorted(items):
        diff_item(args, path, items[path]["old"], items[path]["new"])


def main() -> None:
    """Entry point."""
    if "--" in argv:
        i = argv.index("--")
        nix_args = argv[i + 1 :]
        del argv[i:]
    else:
        nix_args = []

    args = parser.parse_args()

    if args.usage:
        print(parser.format_usage(), end="")
        return

    implies_same = args.old_module or args.new_module or "--override-input" in nix_args
    if not args.new and implies_same:
        args.new = args.old

    traces = run_flake(args, nix_args)
    if not traces:
        die("nix produced no trace output; check that --eval reads the configuration")
    print_diff(args, traces)


if __name__ == "__main__":
    main()

"""
End-to-end tests: run nix-optdiff against the configs in this directory.
"""

from subprocess import PIPE, CompletedProcess, run

import pytest


def flake(output: str) -> str:
    """Return an installable for an output of the test flake."""
    return f".?dir=test#{output}"


@pytest.fixture(name="optdiff", scope="session")
def fixture_optdiff() -> str:
    """Build nix-optdiff once per session and return its binary path."""
    cmd = ["nix", "build", ".#default", "--no-link", "--print-out-paths"]
    out = run(cmd, check=True, stdout=PIPE, text=True).stdout
    return f"{out.strip()}/bin/nix-optdiff"


CASES = [
    pytest.param(
        [flake("nixosConfigurations.base"), flake("nixosConfigurations.postgresql")],
        "+ systemd.services.postgresql.enable = true",
        id="postgresql",
    ),
    pytest.param(
        [flake("nixosConfigurations.base"), flake("nixosConfigurations.home-manager")],
        "home-manager.users.foo.home.activationPackage",
        id="hm-nixos",
    ),
    pytest.param(
        [flake("homeConfigurations.base"), flake("homeConfigurations.user@git")],
        '+ xdg.configFile."git/config".enable = true',
        id="hm-standalone",
    ),
    pytest.param(
        [flake("nixvimConfigurations.base"), flake("nixvimConfigurations.ty")],
        'vim.lsp.enable("ty")',
        id="nixvim",
    ),
    pytest.param(
        [
            flake("nixosConfigurations.base"),
            "--new-module",
            "{ programs.zsh.enable = true; }",
        ],
        "+ environment.etc.zinputrc.enable = true",
        id="new-module",
    ),
    # a home-manager user that only exists in --new-module is traced per option
    pytest.param(
        [
            flake("nixosConfigurations.home-manager"),
            "--new-module",
            "{ users.users.bar.isNormalUser = true;"
            " home-manager.users.bar = { config, ... }: {"
            " home.stateVersion = config.home.version.release;"
            " programs.git.enable = true; }; }",
        ],
        "+ home-manager.users.bar.programs.git.enable = true",
        id="new-module-hm-user",
    ),
    # _module.args keys that depend on config must not recurse under the patched lib
    pytest.param(
        [
            flake("nixosConfigurations.base"),
            "--new-module",
            "{ config, lib, ... }: {"
            " _module.args = lib.mkIf config.services.postgresql.enable { foo = 1; };"
            " services.postgresql.enable = true; }",
        ],
        "+ services.postgresql.enable = true",
        id="conditional-module-args",
    ),
    pytest.param(
        [
            flake("nixosConfigurations.base"),
            "--",
            "--override-input",
            "new/nixpkgs",
            "nixpkgs/nixos-unstable-small",
        ],
        "+ system.nixos.revision =",
        id="override-input",
    ),
    # catches upstream lib/modules.nix changes that break patchModules early
    pytest.param(
        [
            flake("nixosConfigurations.base"),
            "--",
            "--override-input",
            "new/nixpkgs",
            "github:NixOS/nixpkgs/master",
        ],
        "+ system.nixos.revision =",
        id="nixpkgs-master",
    ),
    pytest.param(
        [
            flake("nixosConfigurations.base"),
            "--old-module",
            "{ programs.zsh.enable = true; }",
        ],
        "- environment.etc.zinputrc.enable = true",
        id="old-module",
    ),
    # self.submodules shares a line with another input; clearSubmodules must strip it
    pytest.param(
        [
            ".?dir=test/submodules#nixosConfigurations.base",
            "--new-module",
            "{ programs.zsh.enable = true; }",
        ],
        "+ environment.etc.zinputrc.enable = true",
        id="submodules",
    ),
    # a ?dir flake that reaches its parent dir via `path:..` and a `../` import
    pytest.param(
        [
            ".?dir=test/parent-dir#nixosConfigurations.base",
            "--new-module",
            "{ programs.zsh.enable = true; }",
        ],
        "+ environment.etc.zinputrc.enable = true",
        id="parent-dir",
    ),
    # the root flake, without ?dir
    pytest.param(
        [".#nixosConfigurations.base", ".#nixosConfigurations.hello"],
        "-hello-",
        id="root-flake",
    ),
]


def optdiff_run(optdiff: str, *args: str) -> CompletedProcess[str]:
    """Run nix-optdiff and echo its stdout, so pytest shows it on failure."""
    result = run([optdiff, *args], check=False, stdout=PIPE, stderr=PIPE, text=True)
    print(result.stdout)
    print(result.stderr)
    return result


@pytest.mark.parametrize(("args", "expect"), CASES)
def test_optdiff(optdiff: str, args: list[str], expect: str) -> None:
    """The diff exits 0, is not empty, and contains the expected line."""
    result = optdiff_run(optdiff, *args)
    assert result.returncode == 0
    assert result.stdout.strip()
    assert expect in result.stdout


def test_eval(optdiff: str) -> None:
    """--eval traces only what that path needs, not the whole system."""
    result = optdiff_run(
        optdiff,
        flake("nixosConfigurations.base"),
        flake("nixosConfigurations.postgresql"),
        "--eval",
        "services.postgresql.enable",
    )
    assert result.returncode == 0
    assert "+ services.postgresql.enable = true" in result.stdout
    assert "systemd.services.postgresql" not in result.stdout


@pytest.mark.parametrize(
    ("args", "code", "stderr"),
    [
        pytest.param([], 2, "missing required args", id="no-args"),
        pytest.param(["a", "b"], 1, "expected flake installables", id="no-hash"),
    ],
)
def test_usage_errors(optdiff: str, args: list[str], code: int, stderr: str) -> None:
    """Bad arguments fail before nix runs, with a clear message."""
    result = optdiff_run(optdiff, *args)
    assert result.returncode == code
    assert stderr in result.stderr


def test_usage(optdiff: str) -> None:
    """--usage prints the usage text and exits 0."""
    result = optdiff_run(optdiff, "--usage")
    assert result.returncode == 0
    assert result.stdout.startswith("usage:")

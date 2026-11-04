# nix-optdiff

Diff how options are used between arbitrary nixos/home-manager/etc
configurations in nix flakes (only flake installables are supported).
Requires `nix` and `nix-build` on PATH with `nix-command flakes` enabled.

```shell
$ nix run . -- .#nixosConfigurations.base .#nixosConfigurations.hello
@@ environment.systemPackages.*.out @@
  ... N unchanged
+ "<derivation /nix/store/zi2bj2hlavv8q743li2s9diqbcpmrf9b-hello-2.12.3>"
```

```shell
$ nix run . -- '.?dir=test#nixosConfigurations.'{base,postgresql}
@@ environment.pathsToLink.* @@
  ... N unchanged
+ "/share/postgresql"
...
+ services.postgresql.dataDir = "/var/lib/postgresql/18"
- services.postgresql.enable = false
+ services.postgresql.enable = true
...
```

Arguments after `--` are passed to the final `nix eval` that runs the trace,
allowing you to override inputs in the `old` and `new` flakes:

```bash
# base config: nixos-unstable vs nixos-unstable-small
nix run github:yellowhat/nix-optdiff -- \
    'github:yellowhat/nix-optdiff?dir=test#nixosConfigurations.base'{,} -- \
    --override-input old/nixpkgs nixpkgs/nixos-unstable \
    --override-input new/nixpkgs nixpkgs/nixos-unstable-small
```

You can pass the text of a module that gets injected into the configuration
with `--new-module` or `--old-module`:

```bash
nix run github:yellowhat/nix-optdiff -- \
    'github:yellowhat/nix-optdiff?dir=test#nixosConfigurations.base' \
    --new-module '{ services.postgresql.enable = true; }'
```

By default, store paths that differ only in their hash are hidden.
Pass `-i`/`--include-hashes` to show those changes too.

To find which options are read, nix-optdiff evaluates the whole configuration:
`system.build.toplevel` for NixOS, `home.activationPackage` for home-manager,
and `build.package` for nixvim.
For any other configuration, or to trace only part of one,
pass a path inside `config` with `--eval`:

```bash
nix run . -- \
    '.?dir=test#nixosConfigurations.'{base,postgresql} \
    --eval systemd.services.postgresql.serviceConfig
```

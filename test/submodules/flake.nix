# self.submodules on one line with another input: clearSubmodules must still strip it
{
  inputs = { self.submodules = true; nixpkgs.url = "github:NixOS/nixpkgs/1d060086d60cb76a40cf37109f979d88a5bd6396"; };
  outputs = { self, nixpkgs }: {
    nixosConfigurations.base = nixpkgs.lib.nixosSystem {
      modules = [
        # ../ checks that clearSubmodules copies the whole source tree
        ../nixos/configuration.nix
        { nixpkgs.pkgs = nixpkgs.legacyPackages.x86_64-linux; }
      ];
    };
  };
}

# reaches outside its own dir: a `path:..` input and a `../` import
{
  inputs.parent.url = "path:..";
  outputs = { self, parent }:
    let inherit (parent.inputs) nixpkgs; in
    {
      nixosConfigurations.base = nixpkgs.lib.nixosSystem {
        modules = [
          ../nixos/configuration.nix
          { nixpkgs.pkgs = nixpkgs.legacyPackages.x86_64-linux; }
        ];
      };
    };
}

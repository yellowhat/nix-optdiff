{
  inputs = {
    nixpkgs.url = "github:NixOS/nixpkgs/nixos-unstable";
    home-manager.url = "github:nix-community/home-manager";
    home-manager.inputs.nixpkgs.follows = "nixpkgs";
    nixvim.url = "github:nix-community/nixvim";
    nixvim.inputs.nixpkgs.follows = "nixpkgs";
  };
  outputs =
    { self
    , nixpkgs
    , home-manager
    , nixvim
    }:
    {
      nixosConfigurations =
        let
          baseModule = { modulesPath, ... }: {
            imports = [ ./nixos/configuration.nix ];
            nixpkgs.pkgs = nixpkgs.legacyPackages.x86_64-linux;
          };
        in
        {
          base = nixpkgs.lib.nixosSystem { modules = [ baseModule ]; };
          postgresql = nixpkgs.lib.nixosSystem {
            modules = [
              baseModule
              { services.postgresql.enable = true; }
            ];
          };
          home-manager = nixpkgs.lib.nixosSystem {
            modules = [
              baseModule
              home-manager.nixosModules.default
              {
                users.users.foo.isNormalUser = true;
                home-manager.users.foo = { config, ... }: {
                  home.stateVersion = config.home.version.release;
                  programs.git.enable = true;
                };
              }
            ];
          };
        };

      homeConfigurations =
        let
          pkgs = nixpkgs.legacyPackages.x86_64-linux;
          baseModule = ./home-manager/home.nix;
        in
        {
          base = home-manager.lib.homeManagerConfiguration {
            inherit pkgs;
            modules = [ baseModule ];
          };
          # "@" needs quoting in the generated trace flake
          "user@git" = home-manager.lib.homeManagerConfiguration {
            inherit pkgs;
            modules = [
              baseModule
              { programs.git.enable = true; }
            ];
          };
        };

      nixvimConfigurations = {
        base = nixvim.lib.evalNixvim { system = "x86_64-linux"; };
        ty = nixvim.lib.evalNixvim {
          system = "x86_64-linux";
          modules = [{ lsp.servers.ty.enable = true; }];
        };
      };
    };
}

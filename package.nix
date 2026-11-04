{ lib
, writers
, runCommandLocal
, stdenv

  # required configuration
, optdiffNix
, optdiffNixAttr
, optdiffFlake ? optdiffNix
, optdiffFlakeAttr ? optdiffNixAttr

  # optional configuration
, skipPatterns ? [
    [ "_module" ]
    [ "assertions" ]
    [ "warnings" ]
    [ "home-manager" "extraSpecialArgs" ]
    # submodules that get the trace specialArgs trace themselves under their own
    # prefix; skip them in the parent so values are not traced twice
    [ "home-manager" "users" ]
    [ "home-manager" "users" null "_module" ]
    [ "home-manager" "users" null "assertions" ]
    [ "home-manager" "users" null "warnings" ]
    [ "specialisation" ]
    [ "specialisation" null "configuration" "_module" ]
    [ "specialisation" null "configuration" "assertions" ]
    [ "specialisation" null "configuration" "warnings" ]
  ]
, skipDerivationAttrs ? [ "type" "outputName" "outputs" "meta" ]
}:
let
  inherit (lib)
    any isString concatMapStringsSep length foldl' fix zipLists toJSON init
    elem isAttrs mapAttrs isList imap optionalAttrs concatStringsSep
    hasAttrByPath splitString optionalString all attrValues
    trim concatMapAttrsStringSep readFile hasInfix hasAttr
    concatStrings elemAt head
    ;
  inherit (lib.generators) toPretty;
  inherit (lib.strings) escapeNixIdentifier;
  inherit (stdenv.hostPlatform) system;

  marker = "TRACE_CONFIG";
  traceMarker = "trace: ${marker}";
  internalMarker = "# NIX_INTERNAL";
  extraParser = /* python */ ''
    internal["self_nix"] = "${optdiffNix}"
    internal["self_nix_attr"] = "${optdiffNixAttr}"
    internal["self_flake"] = "${optdiffFlake}"
    internal["marker"] = "${traceMarker}"
  '';
  # wrap declaredConfig in the target's own lib/modules.nix with lib.traceConfigUsage,
  # so the traced lib matches whatever nixpkgs version the configuration uses
  patchModules = lib:
    let
      src = readFile (builtins.unsafeGetAttrPos "evalModules" lib.modules).file;
      # [ before [ binding ] after ] when the binding occurs exactly once
      parts = builtins.split "([[:space:]]declaredConfig[[:space:]]*=)" src;
    in
    if length parts == 3
    then builtins.toFile "modules.nix" (concatStrings [
      (elemAt parts 0)
      (head (elemAt parts 1))
      " lib.traceConfigUsage { inherit prefix specialArgs; } declaredConfig'; declaredConfig' ="
      (elemAt parts 2)
    ])
    else throw "nix-optdiff: unsupported lib/modules.nix, need exactly one declaredConfig binding";
  toPathStringPart = n: if isString n then escapeNixIdentifier n else "*";
  toPathString = path: concatMapStringsSep "." toPathStringPart path;
  pathMatches = path: pattern:
    length path == length pattern &&
    foldl' (acc: { fst, snd }: snd == null || fst == snd) true (zipLists path pattern);
  traceUsage = (f: f null null) (fix (cont': parent: at: info@{ label, path }: arg:
    let
      isDerivation = x: x ? outPath && x ? drvPath;
      inDerivation = isDerivation parent;
      cont = at: cont' arg at (info // { path = path ++ [ at ]; });
      trace = p: x:
        builtins.trace "${marker}${toJSON [ label (toPathString p) (toPretty { } x) ]}" arg;
    in
    # FIXME probably want to move the non-trace logic into python
    if any (pathMatches path) skipPatterns then arg
    else if inDerivation && at == "outPath" then trace (init path) "<derivation ${arg}>"
    else if inDerivation && elem at skipDerivationAttrs then arg
    else if isAttrs arg then mapAttrs cont arg
    else if isList arg then imap (i: cont (i - 1)) arg
    else trace path arg
  ));
  tracedLib = lib: lib.extend (final: prev: {
    # read the label from specialArgs, not config._module.args: specialArgs are
    # fixed before evaluation, so a config-dependent _module.args can't recurse
    traceConfigUsage = { prefix, specialArgs }: config:
      if specialArgs ? _traceConfigUsage
      then traceUsage { inherit (specialArgs._traceConfigUsage) label; path = prefix; } config
      else config;
    modules = import (patchModules prev) { lib = final; };
  });
  getLib = configuration: (configuration.extendModules {
    modules = [
      ({ lib, ... }: {
        _module.args._traceConfigUsage.lib = lib;
      })
    ];
  })._module.args._traceConfigUsage.lib;
  traceConfig = args: label:
    let
      configuration = args.${label};
      trace = { _traceConfigUsage = { inherit label; }; };
      inherit (configuration.type.functor) payload;
    in
    ((tracedLib (getLib configuration)).evalModules {
      inherit (payload) class;
      specialArgs = payload.specialArgs // trace;
      modules = payload.modules ++ [
        (args."${label}Module" or { })
        # home-manager evaluates each user as a submodule with these specialArgs
        (optionalAttrs (hasAttrByPath [ "home-manager" "extraSpecialArgs" ] configuration.options)
          { home-manager.extraSpecialArgs = trace; })
      ];
    }).config;
  indent = i: s: concatStringsSep "\n${i}" (splitString "\n" (trim s));
  buildFlake = inputs: outputsStr: runCommandLocal "source"
    {
      flakeNix = /* nix */ ''
        {
          inputs = {
            ${concatMapAttrsStringSep "\n    " (n: i: ''${n}.url = "path:${i.path}${optionalString (i.dir != "") "?dir=${i.dir}"}";'') inputs}
          };
          outputs = { self, ${concatMapAttrsStringSep ", " (n: _: n) inputs} }:
            ${indent "    " outputsStr};
        }
      '';
      passAsFile = [ "flakeNix" ];
    }
    # unlocked on purpose: main.py locks it with the user's nix, outside the sandbox
    ''
      mkdir $out
      cp $flakeNixPath $out/flake.nix
    '';
  tryEvalOutput = cfg: config:
    if cfg.class or null == "nixos" then config.system.build.toplevel.outPath
    else if cfg.class or null == "homeManager" then config.home.activationPackage.outPath
    else if hasAttrByPath [ "meta" "nixvimInfo" ] cfg.options then config.build.package.outPath
    else throw "unknown configuration type, please pass `--eval PATH`";
  # input = { path = source tree; dir = flake dir inside it, maybe "" }
  clearSubmodules = { path, dir }@input:
    let flakeNix = if dir == "" then "flake.nix" else "${dir}/flake.nix"; in
    if hasInfix "self.submodules" (readFile (path + "/${flakeNix}"))
    then {
      inherit dir;
      path = runCommandLocal "without-submodules" { } ''
        cp -r ${path} $out
        chmod -R +w $out
        # this is safe because we're only using flakes already in the store
        sed -Ei 's/(inputs\.)?self\.submodules\s*=\s*true\s*;//' $out/${flakeNix}
        if grep -Eq 'self\.submodules\s*=\s*true' $out/${flakeNix}; then
          echo "nix-optdiff: could not remove self.submodules from flake.nix" >&2
          exit 1
        fi
      '';
    }
    else input;
  mkFlake =
    { nix-optdiff
    , old
    , new
    , oldOutput
    , newOutput
    , ...
    }@args:
    let
      # quote each part so names like homeConfigurations.user@host parse
      setOutputString = ref: output:
        let out = concatMapStringsSep "." escapeNixIdentifier (splitString "." output); in
        indent "    " /* nix */ ''
        ${ref} = ${ref}.outputs.${out} or
          ${ref}.outputs.packages.${system}.${out} or
          ${ref}.outputs.legacyPackages.${system}.${out};
      '';
      optionalRunArg = name: f: optionalString (hasAttr name args)
        "${name} = ${f args.${name}};";
      passedArgs = [ "oldModule" "newModule" ];
    in
    buildFlake
      (mapAttrs
        (n: path: clearSubmodules { inherit path; dir = args."${n}Dir" or ""; })
        { inherit nix-optdiff old new; })
      /* nix */ ''
      {
        traced = nix-optdiff.packages.${system}.${optdiffFlakeAttr}.run {
          ${setOutputString "old" oldOutput}
          ${setOutputString "new" newOutput}
          ${indent "    " (concatMapStringsSep "\n" (x: optionalRunArg x lib.id) passedArgs)}
          ${optionalRunArg "eval" (e: "_: c: c.${e}")}
        };
      }
    '';
  # like deepSeq, but only force a derivation's outPath: its out/all attrs point back
  # at it, and traceUsage rebuilds each attrset, so deepSeq's cycle check never fires
  deepForce = x:
    if isAttrs x && x ? outPath then builtins.seq x.outPath true
    else if isAttrs x then all deepForce (attrValues x)
    else if isList x then all deepForce x
    else builtins.seq x true;
  run = { old, new, eval ? tryEvalOutput, ... }@args: builtins.seq
    (all deepForce [
      (eval old (traceConfig args "old"))
      (eval new (traceConfig args "new"))
    ]) "";
in
(writers.writePython3Bin "nix-optdiff"
  {
    doCheck = false;
  }
  (
    let src = readFile ./main.py; in
    if hasInfix internalMarker src
    then lib.replaceString internalMarker extraParser src
    else throw "nix-optdiff: '${internalMarker}' not found in main.py"
  )
).overrideAttrs { passthru = { inherit mkFlake run; }; }

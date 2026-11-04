@_:
    just --list --unsorted

# Start environment in a container
[group("lifecycle")]
env:
    bash scripts/00_env.sh

# Run tests
[group("test")]
test:
    nix shell --inputs-from . nixpkgs#python3Packages.pytest --command pytest --verbose -o pythonpath=. test

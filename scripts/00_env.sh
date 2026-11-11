#!/usr/bin/env bash
# Start container
set -euo pipefail

# renovate: datasource=docker depName=nixos/nix
NIX_VER=2.35.6

args=(
    --name nix-optdiff
    --interactive
    --tty
    --rm
    --env NIX_CONFIG='experimental-features = nix-command flakes'
    --volume .:/data:z
    --workdir /data
    --entrypoint bash
)
podman run "${args[@]}" "docker.io/nixos/nix:${NIX_VER}"

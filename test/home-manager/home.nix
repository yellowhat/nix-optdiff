{ config, ... }:
{
  home = {
    stateVersion = config.home.version.release;
    username = "foo";
    homeDirectory = "/home/foo";
  };
}

"""Launch the configured Yui shell."""

from importlib import import_module

from settings import load_config


def main() -> int:
    shell = import_module(load_config()["shell"])
    return shell.main()


if __name__ == "__main__":
    raise SystemExit(main())

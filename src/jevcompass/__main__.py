"""Module entry point; keep the normal hook path independent of CLI imports."""

import sys


def main(argv: list[str] | None = None) -> int:
    arguments = sys.argv[1:] if argv is None else argv
    if arguments and arguments[0] == "hook":
        if arguments[1:] not in ([], ["--strategy-advice"]):
            return 2
        from .advisor import hook_main

        return hook_main(strategy_advice=arguments[1:] == ["--strategy-advice"])
    from .cli import main as cli_main

    return cli_main(arguments)


if __name__ == "__main__":
    raise SystemExit(main())

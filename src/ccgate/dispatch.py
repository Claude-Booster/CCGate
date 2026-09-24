"""dispatch.py — single entry point; lazy-imports handlers to minimize startup cost."""
import sys


def main(argv: list[str] | None = None) -> None:
    args = argv if argv is not None else sys.argv[1:]

    if not args:
        _usage()
        sys.exit(0)

    subcmd = args[0]

    if subcmd in ("statusline", "status"):
        from ccgate.scripts.statusline import main as _main
        _main()

    elif subcmd in ("audit", "miss-audit"):
        from ccgate.scripts.miss_audit import main as _main
        _main(args[1:])

    elif subcmd in ("shape", "lint"):
        from ccgate.scripts.shape import main as _main
        _main(args[1:])

    elif subcmd in ("--help", "-h", "help"):
        _usage()

    else:
        print(f"ccgate: unknown subcommand '{subcmd}'", file=sys.stderr)
        _usage()
        sys.exit(1)


def _usage() -> None:
    print(
        "usage: ccgate <subcommand> [options]\n"
        "\n"
        "subcommands:\n"
        "  statusline        Read status-line JSON from stdin, write one line to stdout\n"
        "  audit [paths]     Miss-cause attribution report\n"
        "  shape             Static config and CLAUDE.md lint\n"
    )


if __name__ == "__main__":
    main()

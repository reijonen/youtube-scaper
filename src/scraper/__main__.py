"""Entry point: dispatches `controller` and `native-host` subcommands."""

import argparse
import sys

from scraper import config


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scraper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    controller_parser = subparsers.add_parser(
        "controller", help="Run the collection controller."
    )
    controller_parser.add_argument(
        "--video-ids", required=True, help="Comma-separated YouTube video IDs."
    )
    controller_parser.add_argument(
        "--max-recommendations",
        type=int,
        default=config.DEFAULT_MAX_RECOMMENDATIONS,
        help="Stored recommendations per video, after filtering to videos.",
    )
    controller_parser.add_argument(
        "--inter-video-delay-ms",
        type=int,
        default=config.DEFAULT_INTER_VIDEO_DELAY_MS,
        help="Idle pause between videos.",
    )
    controller_parser.add_argument(
        "--scroll-delay-ms",
        type=int,
        default=config.DEFAULT_SCROLL_DELAY_MS,
        help="Dwell between scroll actions within a page.",
    )
    controller_parser.add_argument(
        "--delay-jitter",
        type=float,
        default=config.DEFAULT_DELAY_JITTER,
        help="Fraction of each delay applied as uniform random jitter.",
    )

    subparsers.add_parser(
        "native-host", help="Run the native-messaging bridge (launched by Chrome)."
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "controller":
        raise NotImplementedError("controller is implemented in a later phase")
    elif args.command == "native-host":
        raise NotImplementedError("native-host is implemented in a later phase")

    return 1


if __name__ == "__main__":
    sys.exit(main())

"""Entry point: dispatches `controller` and `native-host` subcommands."""

import argparse
import logging
import sys

from scraper import config, native_host
from scraper.controller import run_controller


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m scraper")
    subparsers = parser.add_subparsers(dest="command", required=True)

    controller_parser = subparsers.add_parser("controller", help="Run the collection controller.")
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
    controller_parser.add_argument(
        "--max-scroll-rounds",
        type=int,
        default=config.MAX_SCROLL_ROUNDS,
        help=(
            "Scroll attempts per video before giving up as CIRCUIT_BREAKER. Default is "
            "conservative on purpose; raise it explicitly for videos you trust will "
            "paginate normally, not as the default everyone gets."
        ),
    )
    controller_parser.add_argument(
        "--wait-for-comments",
        action="store_true",
        default=config.DEFAULT_WAIT_FOR_COMMENTS,
        help=(
            "Keep scrolling past what the recommendation side alone would stop at, "
            "until the comments header resolves to a real count (zero, or at least "
            "one thread collected) — still bounded by --max-scroll-rounds and the "
            "page-duration circuit breaker."
        ),
    )

    subparsers.add_parser(
        "native-host", help="Run the native-messaging bridge (launched by Chrome)."
    )

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)

    if args.command == "controller":
        logging.basicConfig(
            stream=sys.stderr, level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s"
        )
        return run_controller(
            video_ids_arg=args.video_ids,
            max_recommendations=args.max_recommendations,
            inter_video_delay_ms=args.inter_video_delay_ms,
            scroll_delay_ms=args.scroll_delay_ms,
            delay_jitter=args.delay_jitter,
            max_scroll_rounds=args.max_scroll_rounds,
            wait_for_comments=args.wait_for_comments,
        )
    elif args.command == "native-host":
        return native_host.main()

    return 1


if __name__ == "__main__":
    sys.exit(main())

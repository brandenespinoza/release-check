"""Command-line interface.

A bare invocation runs a scan. The extra subcommands exist only for the tasks
the PRD calls out — resolving artists, inspecting state and clearing it — and
deliberately stop there.
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path

from . import __version__
from .config import (
    SETTINGS,
    SETTINGS_BY_KEY,
    USER_AGENT,
    Config,
    describe_settings,
    find_env_file,
    invocation_name,
    load_config,
    load_dotenv,
    normalize_url,
    save_settings,
    user_config_dir,
)
from .deezer import DeezerProvider, make_rate_limiter
from .errors import ExitCode, ReleaseCheckError
from .http import HttpClient
from .logging_setup import setup_logging
from .models import DatePrecision, ReleaseDate, ReleaseType
from .navidrome import NavidromeClient
from .report import (
    build_summary,
    print_results,
    print_review,
    print_summary,
    print_unresolved,
    sort_releases,
)
from .scan import Scanner, ScanOptions
from .state import MappingTarget, Store

log = logging.getLogger("release_check.cli")

SUBCOMMANDS = {
    "scan", "setup", "config", "check", "resolve", "review", "artists", "map",
    "unmap", "ignore", "cache",
}

_TYPE_ALIASES = {
    "album": ReleaseType.ALBUM,
    "albums": ReleaseType.ALBUM,
    "ep": ReleaseType.EP,
    "eps": ReleaseType.EP,
    "single": ReleaseType.SINGLE,
    "singles": ReleaseType.SINGLE,
    "unknown": ReleaseType.UNKNOWN,
}


def _global_options() -> argparse.ArgumentParser:
    """Options accepted both before and after the subcommand."""
    common = argparse.ArgumentParser(add_help=False)
    # SUPPRESS keeps the subparser from overwriting a value that was given
    # before the subcommand ("release_check -vv scan") with its own default.
    common.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=argparse.SUPPRESS,
        help="verbose output to stderr; repeat for debug logging",
    )
    common.add_argument(
        "--env-file",
        type=Path,
        default=argparse.SUPPRESS,
        help="path to the config file (default: ./.env, then ~/.config/release_check/.env)",
    )
    return common


def build_parser() -> argparse.ArgumentParser:
    common = _global_options()
    parser = argparse.ArgumentParser(
        prog=invocation_name(),
        parents=[common],
        description=(
            "List releases by artists in your Navidrome library that Deezer has "
            "and you appear not to own."
        ),
        epilog=(
            "Run with no arguments to perform a normal scan. Configuration is read "
            "from the environment, then ./.env, then ~/.config/release_check/.env. "
            "Run `release-check setup` to configure it."
        ),
    )
    parser.add_argument("--version", action="version", version=f"release_check {__version__}")

    sub = parser.add_subparsers(dest="command")

    scan = sub.add_parser(
        "scan", parents=[common], help="scan the library and list missing releases"
    )
    scan.add_argument(
        "--artist",
        action="append",
        default=[],
        metavar="NAME",
        help="restrict the scan to this local artist (repeatable)",
    )
    scan.add_argument("--limit", type=int, default=None, help="scan at most N artists")
    scan.add_argument(
        "--since",
        type=_parse_since,
        default=None,
        metavar="DATE",
        help="only consider releases on or after this date: YYYY, YYYY-MM or YYYY-MM-DD",
    )
    scan.add_argument(
        "--type",
        action="append",
        default=[],
        metavar="TYPE",
        choices=sorted(_TYPE_ALIASES),
        help="only report these release types (repeatable)",
    )
    scan.add_argument(
        "--refresh",
        action="store_true",
        help="ignore cached Deezer data and refetch everything",
    )
    scan.add_argument(
        "--flat",
        action="store_true",
        help="one continuous list sorted by date, without release-type groups",
    )
    scan.add_argument("--no-progress", action="store_true", help="suppress the progress line")

    sub.add_parser(
        "setup",
        parents=[common],
        help="guided setup: enter your Navidrome details and test the connection",
    )

    config_cmd = sub.add_parser(
        "config", parents=[common], help="view or change individual settings"
    )
    config_sub = config_cmd.add_subparsers(dest="config_action")
    config_sub.add_parser("path", parents=[common], help="print the settings file location")
    setter = config_sub.add_parser("set", parents=[common], help="change one setting")
    setter.add_argument("key", choices=[s.key for s in SETTINGS])
    setter.add_argument("value")
    unsetter = config_sub.add_parser("unset", parents=[common], help="remove one setting")
    unsetter.add_argument("key", choices=[s.key for s in SETTINGS])
    config_sub.add_parser(
        "password", parents=[common], help="set the Navidrome password (prompted, not echoed)"
    )

    sub.add_parser(
        "check",
        parents=[common],
        help="verify Navidrome connectivity and credentials, then exit",
    )

    sub.add_parser(
        "review",
        parents=[common],
        help="decide on ambiguous releases so they stop being reported",
    )

    resolver = sub.add_parser(
        "resolve",
        parents=[common],
        help="work through unresolved artists, or re-open one by name",
    )
    resolver.add_argument(
        "artist",
        nargs="?",
        default=None,
        metavar="ARTIST",
        help="re-open this artist even if it is already mapped",
    )

    artists = sub.add_parser(
        "artists", parents=[common], help="show unresolved artists and manual mappings"
    )
    artists.add_argument(
        "--mappings", action="store_true", help="list saved mappings instead"
    )

    mapper = sub.add_parser(
        "map",
        parents=[common],
        help="map a local artist to one or more Deezer artists",
    )
    mapper.add_argument("local_name")
    mapper.add_argument(
        "deezer_id",
        nargs="+",
        metavar="DEEZER_ID",
        help="one or more Deezer artist IDs; several are merged into one artist",
    )

    unmap = sub.add_parser(
        "unmap",
        parents=[common],
        help="clear everything known about an artist, back to unresolved",
    )
    unmap.add_argument("local_name")

    ignore = sub.add_parser(
        "ignore", parents=[common], help="never report releases for this local artist"
    )
    ignore.add_argument("local_name")

    cache = sub.add_parser("cache", parents=[common], help="inspect or clear local state")
    cache.add_argument("--clear", action="store_true", help="delete cached API responses")
    cache.add_argument(
        "--reset-mappings", action="store_true", help="delete all artist mappings"
    )
    cache.add_argument(
        "--reset-decisions",
        action="store_true",
        help="forget every review decision",
    )

    return parser


def _parse_since(text: str) -> ReleaseDate:
    """Accept a year, a year-month, or a full date for --since."""
    parsed = ReleaseDate.parse(text)
    if parsed.precision is DatePrecision.UNKNOWN:
        raise argparse.ArgumentTypeError(
            f"{text!r} is not a date. Use YYYY, YYYY-MM or YYYY-MM-DD."
        )
    return parsed


#: Options that consume the following token, so it is not mistaken for a command.
_VALUE_FLAGS = {"--env-file", "--artist", "--limit", "--since", "--type"}


def _insert_default_command(argv: list[str]) -> list[str]:
    """Make a bare invocation, or one with only flags, mean `scan`."""
    skip_next = False
    for token in argv:
        if skip_next:
            skip_next = False
            continue
        if token in ("-h", "--help", "--version"):
            return argv
        if token.startswith("-"):
            if token in _VALUE_FLAGS:
                skip_next = True
            continue
        return argv if token in SUBCOMMANDS else ["scan", *argv]
    return ["scan", *argv]


def _make_clients(config: Config, store: Store, refresh: bool):
    navidrome_http = HttpClient(
        timeout=config.request_timeout,
        user_agent=USER_AGENT,
        verify_tls=config.verify_tls,
    )
    deezer_http = HttpClient(
        timeout=config.request_timeout,
        user_agent=USER_AGENT,
        rate_limiter=make_rate_limiter(),
    )
    client = NavidromeClient(config, navidrome_http)
    provider = DeezerProvider(deezer_http, cache=None if refresh else store)
    return client, provider


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv[1:] if argv is None else argv)
    parser = build_parser()
    args = parser.parse_args(_insert_default_command(argv))
    # Restore the defaults that SUPPRESS omitted.
    args.verbose = getattr(args, "verbose", 0)
    args.env_file = getattr(args, "env_file", None)
    setup_logging(args.verbose)

    command = args.command or "scan"
    try:
        if command == "scan":
            return cmd_scan(args)
        if command == "setup":
            return cmd_setup(args)
        if command == "config":
            return cmd_config(args)
        if command == "check":
            return cmd_check(args)
        if command == "resolve":
            return cmd_resolve(args)
        if command == "review":
            return cmd_review(args)
        if command == "artists":
            return cmd_artists(args)
        if command == "map":
            return cmd_map(args)
        if command in ("unmap", "ignore"):
            return cmd_mapping_change(args, command)
        if command == "cache":
            return cmd_cache(args)
    except ReleaseCheckError as exc:
        _report_error(exc)
        return exc.exit_code
    except KeyboardInterrupt:
        print("\ninterrupted", file=sys.stderr)
        return ExitCode.FAILURE

    parser.error(f"unknown command {command!r}")
    return ExitCode.USAGE


def _report_error(exc: ReleaseCheckError) -> None:
    print(f"error: {exc.message}", file=sys.stderr)
    if exc.hint:
        print(f"  {exc.hint}", file=sys.stderr)


def _open_store(config: Config) -> Store:
    return Store(config.cache_path, max_age_hours=config.cache_max_age_hours)


def cmd_scan(args) -> int:
    config = load_config(env_file=args.env_file)

    if args.type:
        types = {_TYPE_ALIASES[t] for t in args.type}
    elif config.release_types:
        types = {t for t in ReleaseType if t.value in config.release_types}
    else:
        types = None
    options = ScanOptions(
        artist_filters=args.artist,
        limit=args.limit,
        since=args.since,
        types=types,
        progress=not args.no_progress,
    )

    with _open_store(config) as store:
        client, provider = _make_clients(config, store, refresh=args.refresh)
        client.ping()

        scanner = Scanner(client, provider, store, config)
        result = scanner.run(options)

        items = sort_releases(result.missing)
        print_results(items, grouped=not args.flat)
        summary = build_summary(
            items,
            result.unresolved,
            result.review,
            result.artists_scanned,
            result.partial_reasons,
        )
        if items:
            print(file=sys.stdout)
        print_summary(summary)
        print_review(result.review)
        print_unresolved(result.unresolved)

    return ExitCode.PARTIAL if summary.partial else ExitCode.OK


def _settings_path(args) -> Path:
    """Where `config` writes. Follows discovery, else the user config dir."""
    if args.env_file:
        return Path(args.env_file).expanduser()
    found = find_env_file(None)
    return found if found is not None else user_config_dir() / ".env"


def cmd_setup(args) -> int:
    from .setup_wizard import run_setup

    return run_setup(env_file=args.env_file)


def cmd_config(args) -> int:
    # A bare `config` shows the settings; there is no separate `list`.
    action = getattr(args, "config_action", None) or "show"
    path = _settings_path(args)

    if action == "path":
        print(path)
        return ExitCode.OK

    if action == "show":
        rows = describe_settings(args.env_file)
        width = max(len(r.setting.key) for r in rows)
        value_width = max(len(r.display) for r in rows)
        for row in rows:
            source = "" if row.value is None else f"({row.source})"
            line = f"{row.setting.key:<{width}}  {row.display:<{value_width}}  {source}"
            print(line.rstrip())
        print(
            f"\nChange one with: {invocation_name()} config set <key> <value>"
            f"\n         or all: {invocation_name()} setup"
        )
        missing = [r.setting.key for r in rows if r.setting.required and not r.value]
        if missing:
            # stdout is block-buffered when piped; flush so the note below
            # cannot overtake the table it refers to.
            sys.stdout.flush()
            print(
                f"\nNot configured: {', '.join(missing)}. "
                f"Run `{invocation_name()} setup`.",
                file=sys.stderr,
            )
        return ExitCode.OK

    if action == "password":
        if not sys.stdin.isatty():
            print("error: setting a password needs an interactive terminal.", file=sys.stderr)
            return ExitCode.CONFIG
        import getpass

        value = getpass.getpass("Navidrome password: ")
        if not value:
            print("No password entered; nothing changed.", file=sys.stderr)
            return ExitCode.CONFIG
        if value != getpass.getpass("Confirm: "):
            print("error: passwords did not match.", file=sys.stderr)
            return ExitCode.CONFIG
        _write_one(path, SETTINGS_BY_KEY["password"].env_var, value)
        print(f"Password updated in {path}")
        return ExitCode.OK

    setting = SETTINGS_BY_KEY[args.key]

    if action == "unset":
        _write_one(path, setting.env_var, None)
        print(f"Removed {args.key} from {path}")
        return ExitCode.OK

    # `set`. Secrets are rejected at the parser via choices, but guard anyway:
    # a password in argv lands in shell history and is visible to `ps`.
    if setting.secret:
        print(
            f"error: refusing to take a {args.key} on the command line, where it "
            "would be saved in your shell history.",
            file=sys.stderr,
        )
        print(f"  Use: {invocation_name()} config password", file=sys.stderr)
        return ExitCode.CONFIG

    value = args.value
    if args.key == "url":
        value = normalize_url(value)
    _write_one(path, setting.env_var, value)
    print(f"Set {args.key} = {value}")

    conflict = next(
        (r for r in describe_settings(args.env_file) if r.setting.key == args.key), None
    )
    if conflict is not None and conflict.source == "environment":
        print(
            f"note: ${setting.env_var} is set in your environment and still "
            "overrides this file.",
            file=sys.stderr,
        )
    return ExitCode.OK


def _write_one(path: Path, env_var: str, value: str | None) -> None:
    """Update a single key, leaving every other setting untouched."""
    values = load_dotenv(path)
    if value is None:
        values.pop(env_var, None)
    else:
        values[env_var] = value
    save_settings(path, values)


def cmd_check(args) -> int:
    config = load_config(env_file=args.env_file)
    with _open_store(config) as store:
        client, _ = _make_clients(config, store, refresh=False)
        server = client.ping()
        artists = client.get_artists()
    print(f"Connected to {config.navidrome_url} ({server})")
    print(f"{len(artists)} album artists visible")
    return ExitCode.OK


def cmd_review(args) -> int:
    from .review_ui import run_review

    config = load_config(env_file=args.env_file)
    with _open_store(config) as store:
        return run_review(store)


def cmd_resolve(args) -> int:
    from .resolve_ui import run_resolve

    config = load_config(env_file=args.env_file)
    with _open_store(config) as store:
        client, provider = _make_clients(config, store, refresh=False)
        # Local album titles let the picker highlight the decisive evidence.
        local_albums: set[str] = set()
        try:
            from .normalize import parse_title

            for album in client.get_all_albums():
                base = parse_title(album.name).base
                if base:
                    local_albums.add(base)
        except ReleaseCheckError as exc:
            log.info("Could not read local albums for comparison: %s", exc)
        return run_resolve(store, provider, local_albums, only=args.artist)


def cmd_artists(args) -> int:
    config = load_config(env_file=args.env_file)
    with _open_store(config) as store:
        if args.mappings:
            mappings = store.list_mappings()
            if not mappings:
                print("No saved artist mappings.")
                return ExitCode.OK
            width = max(len(m.local_name) for m in mappings)
            for mapping in mappings:
                print(f"{mapping.local_name:<{width}}  ->  {mapping.describe()}")
            return ExitCode.OK

        unresolved = store.load_unresolved()

    if not unresolved:
        print("No unresolved artists recorded. Run a scan first.")
        return ExitCode.OK

    print(f"{len(unresolved)} unresolved artist(s) from the last scan:\n")
    for entry in unresolved:
        print(f"{entry['name']}: {entry['reason']}")
        for candidate in entry.get("candidates", [])[:5]:
            print(
                f"    {candidate['name']}  deezer id {candidate['id']}  "
                f"({candidate['fans']:,} fans)"
            )
    program = invocation_name()
    print(
        f"\nWork through these interactively:  {program} resolve"
        f'\nOr set one directly:               {program} map "<artist>" <id> [<id> ...]'
        f'\nOr skip one:                       {program} ignore "<artist>"'
    )
    return ExitCode.OK


def cmd_map(args) -> int:
    config = load_config(env_file=args.env_file)
    ids = list(dict.fromkeys(args.deezer_id))  # de-duplicate, keep order
    with _open_store(config) as store:
        _, provider = _make_clients(config, store, refresh=False)
        targets = []
        for deezer_id in ids:
            artist = provider.get_artist(deezer_id)
            if artist is None:
                print(f"error: Deezer has no artist with id {deezer_id}", file=sys.stderr)
                return ExitCode.DEEZER
            targets.append(MappingTarget(artist.id, artist.name))
        store.set_mapping(args.local_name, targets)
    joined = ", ".join(f"{t.deezer_name} [{t.deezer_id}]" for t in targets)
    print(f"Mapped {args.local_name!r} -> {joined}")
    if len(targets) > 1:
        print("Their discographies will be merged and de-duplicated.")
    return ExitCode.OK


def cmd_mapping_change(args, command: str) -> int:
    config = load_config(env_file=args.env_file)
    with _open_store(config) as store:
        if command == "ignore":
            store.ignore_artist(args.local_name)
            print(f"Ignoring {args.local_name!r}.")
            return ExitCode.OK
        removed = store.clear_mapping(args.local_name)
    if removed:
        print(
            f"Cleared {args.local_name!r}. It will be resolved from scratch on "
            "the next scan."
        )
    else:
        print(f"No saved entry for {args.local_name!r}.")
    return ExitCode.OK


def cmd_cache(args) -> int:
    config = load_config(env_file=args.env_file)
    with _open_store(config) as store:
        if args.clear:
            store.clear_cache()
            print("Cache cleared.")
        if args.reset_mappings:
            count = store.reset_mappings()
            print(f"Removed {count} artist mapping(s).")
        if getattr(args, "reset_decisions", False):
            count = store.reset_release_decisions()
            print(f"Removed {count} review decision(s).")
        if not args.clear and not args.reset_mappings and not getattr(
            args, "reset_decisions", False
        ):
            age = store.cache_age_hours()
            stats = store.cache_stats()
            print(f"State file: {config.cache_path}")
            print(f"Mappings:   {len(store.list_mappings())}")
            print(f"Decisions:  {store.count_release_decisions()}")
            print(
                "Cache age:  "
                + (f"{age:.1f}h" if age is not None else "empty")
            )
            if stats["total"]:
                print(
                    f"Entries:    {stats['total']} "
                    f"({stats['volatile']} refreshed every "
                    f"{config.cache_max_age_hours:g}h, "
                    f"{stats['stable']} long-lived)"
                )
    return ExitCode.OK


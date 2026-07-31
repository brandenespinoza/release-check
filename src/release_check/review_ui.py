"""Interactive review of ambiguous releases.

Every release the matcher could not settle used to print to stderr on every
run, with no way to answer it. This is that answer: the user says whether they
own it, the decision is stored against the Deezer release id, and the question
is not asked again.
"""

from __future__ import annotations

import sys

from .config import invocation_name
from .errors import ExitCode
from .models import DECISION_MISSING, DECISION_OWNED
from .state import Store


def _show(entry: dict, position: int, total: int, current: str | None) -> None:
    print(f"\n─── {position}/{total} ───")
    print(f"{entry['artist']} — {entry['title']}")
    print(f"  {entry['type']}, {entry['date']}")
    print(f"  {entry['reason']}")
    if entry.get("url"):
        print(f"  {entry['url']}")
    if current:
        print(f"  currently marked: {current}")
    print("  [o] I own it   [m] I don't, report it   [s]kip   [u]ndo   [q]uit")


def run_review(store: Store) -> int:
    """Walk the ambiguous releases from the last scan. Returns an exit code."""
    if not sys.stdin.isatty():
        print("error: review needs an interactive terminal.", file=sys.stderr)
        print(
            f"  Non-interactively, use `{invocation_name()} scan` and read the "
            "review section on stderr.",
            file=sys.stderr,
        )
        return ExitCode.CONFIG

    entries = store.load_review()
    if not entries:
        print("Nothing to review. Run a scan first.")
        return ExitCode.OK

    decisions = store.release_decisions()
    print(f"{len(entries)} release(s) need review from the last scan.")
    print("Press Enter to leave one undecided.")

    changed = 0
    for position, entry in enumerate(entries, start=1):
        release_id = str(entry["id"])
        _show(entry, position, len(entries), decisions.get(release_id))

        while True:
            try:
                answer = input("  > ").strip().lower()
            except EOFError:
                answer = "q"

            if answer in ("q", "quit"):
                _report(changed)
                return ExitCode.OK

            if answer == "" or answer in ("s", "skip"):
                break

            if answer in ("o", "own", "owned"):
                store.set_release_decision(release_id, DECISION_OWNED)
                print("  Marked as owned. It will not be reported again.")
                changed += 1
                break

            if answer in ("m", "missing"):
                store.set_release_decision(release_id, DECISION_MISSING)
                print("  Marked as missing. It will appear in the main list.")
                changed += 1
                break

            if answer in ("u", "undo"):
                if store.clear_release_decision(release_id):
                    print("  Decision cleared; it will be judged normally again.")
                    changed += 1
                else:
                    print("  No decision was stored for this release.")
                break

            print("  Enter o, m, s, u or q.")

    _report(changed)
    return ExitCode.OK


def _report(changed: int) -> None:
    if changed:
        print(f"\n{changed} decision(s) recorded. Run a scan to see the effect.")
    else:
        print("\nNothing changed.")

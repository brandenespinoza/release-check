"""Review workflow: ambiguous releases must be answerable, once."""

from __future__ import annotations

import sys

import pytest
from conftest import deezer_release, local_album, local_artist

from release_check.errors import ExitCode
from release_check.models import DECISION_MISSING, DECISION_OWNED, Ownership, ReleaseType
from release_check.release_match import LocalIndex, determine_ownership
from release_check.review_ui import run_review


@pytest.fixture
def queued(store):
    store.save_review(
        [
            {
                "id": "555",
                "artist": "Alabama",
                "title": "American Christmas",
                "type": "Album",
                "date": "2017-10-06",
                "reason": "title closely resembles local album 'Christmas'",
                "url": "https://www.deezer.com/album/555",
            }
        ]
    )
    return store


def drive(monkeypatch, answers):
    queue = list(answers)
    monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
    monkeypatch.setattr("builtins.input", lambda _="": queue.pop(0) if queue else "q")


class TestDecisions:
    def test_mark_owned(self, queued, monkeypatch):
        drive(monkeypatch, ["o"])
        assert run_review(queued) == ExitCode.OK
        assert queued.release_decisions() == {"555": DECISION_OWNED}

    def test_mark_missing(self, queued, monkeypatch):
        drive(monkeypatch, ["m"])
        run_review(queued)
        assert queued.release_decisions() == {"555": DECISION_MISSING}

    def test_skip_records_nothing(self, queued, monkeypatch, capsys):
        drive(monkeypatch, ["s"])
        run_review(queued)
        assert queued.release_decisions() == {}
        assert "Nothing changed" in capsys.readouterr().out

    def test_enter_is_skip(self, queued, monkeypatch):
        drive(monkeypatch, [""])
        run_review(queued)
        assert queued.release_decisions() == {}

    def test_undo_clears_a_previous_decision(self, queued, monkeypatch):
        queued.set_release_decision("555", DECISION_OWNED)
        drive(monkeypatch, ["u"])
        run_review(queued)
        assert queued.release_decisions() == {}

    def test_garbage_reprompts(self, queued, monkeypatch, capsys):
        drive(monkeypatch, ["what", "o"])
        run_review(queued)
        assert queued.release_decisions() == {"555": DECISION_OWNED}
        assert "Enter o, m, s, u or q" in capsys.readouterr().out

    def test_quit_keeps_earlier_decisions(self, store, monkeypatch):
        store.save_review(
            [
                {"id": "1", "artist": "A", "title": "T1", "type": "Album",
                 "date": "2024-01-01", "reason": "r", "url": ""},
                {"id": "2", "artist": "A", "title": "T2", "type": "Album",
                 "date": "2024-01-01", "reason": "r", "url": ""},
            ]
        )
        drive(monkeypatch, ["o", "q"])
        run_review(store)
        assert store.release_decisions() == {"1": DECISION_OWNED}

    def test_current_decision_is_shown(self, queued, monkeypatch, capsys):
        queued.set_release_decision("555", DECISION_OWNED)
        drive(monkeypatch, ["s"])
        run_review(queued)
        assert "currently marked: owned" in capsys.readouterr().out


class TestGuards:
    def test_needs_a_terminal(self, queued, monkeypatch, capsys):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        assert run_review(queued) == ExitCode.CONFIG
        assert "interactive terminal" in capsys.readouterr().err

    def test_empty_queue(self, store, monkeypatch, capsys):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: True)
        assert run_review(store) == ExitCode.OK
        assert "Nothing to review" in capsys.readouterr().out


class TestDecisionsAffectOwnership:
    """A recorded decision must actually change the next scan's verdict."""

    def _setup(self):
        index = LocalIndex(local_artist("A", [local_album("Christmas", "A", 2000)]))
        release = deezer_release("American Christmas", release_id="555", date="2017-10-06")
        return release, index

    def test_without_a_decision_it_is_ambiguous(self):
        release, index = self._setup()
        verdict = determine_ownership(release, index, ReleaseType.ALBUM, {})
        assert verdict.ownership is Ownership.AMBIGUOUS

    def test_owned_decision_suppresses_it(self):
        release, index = self._setup()
        verdict = determine_ownership(
            release, index, ReleaseType.ALBUM, {"555": DECISION_OWNED}
        )
        assert verdict.ownership is Ownership.IGNORED
        assert not verdict.reportable

    def test_missing_decision_forces_it_into_the_report(self):
        release, index = self._setup()
        verdict = determine_ownership(
            release, index, ReleaseType.ALBUM, {"555": DECISION_MISSING}
        )
        assert verdict.ownership is Ownership.MISSING
        assert verdict.reportable

    def test_a_decision_for_another_release_is_ignored(self):
        release, index = self._setup()
        verdict = determine_ownership(
            release, index, ReleaseType.ALBUM, {"999": DECISION_OWNED}
        )
        assert verdict.ownership is Ownership.AMBIGUOUS

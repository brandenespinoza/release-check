"""Settings storage, provenance, and the setup/config commands."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from release_check.cli import main
from release_check.config import (
    SETTINGS,
    describe_settings,
    load_dotenv,
    save_settings,
)
from release_check.errors import ExitCode


@pytest.fixture
def cfg(tmp_path, monkeypatch):
    """An isolated config directory, with the real environment neutralised."""
    directory = tmp_path / "cfg"
    monkeypatch.setenv("RELEASE_CHECK_CONFIG_DIR", str(directory))
    for setting in SETTINGS:
        monkeypatch.delenv(setting.env_var, raising=False)
    monkeypatch.delenv("RELEASE_CHECK_ENV", raising=False)
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(sys, "argv", ["/usr/local/bin/release-check"])
    return directory / ".env"


class TestSaveSettings:
    def test_file_is_private(self, tmp_path):
        path = tmp_path / "cfg" / ".env"
        save_settings(path, {"NAVIDROME_URL": "http://your-server:4533"})
        assert path.stat().st_mode & 0o077 == 0
        assert path.parent.stat().st_mode & 0o077 == 0

    def test_roundtrip(self, tmp_path):
        path = tmp_path / ".env"
        save_settings(path, {"NAVIDROME_URL": "http://your-server:4533", "WORKERS": "8"})
        values = load_dotenv(path)
        assert values["NAVIDROME_URL"] == "http://your-server:4533"
        assert values["WORKERS"] == "8"

    def test_empty_values_are_omitted(self, tmp_path):
        path = tmp_path / ".env"
        save_settings(path, {"NAVIDROME_URL": "http://a:1", "WORKERS": ""})
        assert "WORKERS" not in load_dotenv(path)

    def test_hand_added_keys_are_preserved(self, tmp_path):
        path = tmp_path / ".env"
        path.write_text("NAVIDROME_URL=http://a:1\nMY_OWN_NOTE=keepme\n")
        save_settings(path, {"NAVIDROME_URL": "http://b:2"})
        values = load_dotenv(path)
        assert values["MY_OWN_NOTE"] == "keepme"
        assert values["NAVIDROME_URL"] == "http://b:2"

    def test_write_is_atomic(self, tmp_path, monkeypatch):
        # A crash mid-write must not destroy a working config.
        path = tmp_path / ".env"
        save_settings(path, {"NAVIDROME_URL": "http://good:1"})

        def boom(*args, **kwargs):
            raise RuntimeError("disk full")

        monkeypatch.setattr(os, "replace", boom)
        with pytest.raises(RuntimeError):
            save_settings(path, {"NAVIDROME_URL": "http://bad:2"})
        assert load_dotenv(path)["NAVIDROME_URL"] == "http://good:1"
        leftovers = [p for p in path.parent.iterdir() if p.name.startswith(".env.")]
        assert leftovers == [], "temporary files must be cleaned up"


class TestProvenance:
    def test_environment_beats_file(self, tmp_path):
        path = tmp_path / ".env"
        save_settings(path, {"NAVIDROME_URL": "http://from-file:1"})
        rows = {r.setting.key: r for r in describe_settings(path, {"NAVIDROME_URL": "http://from-env:2"})}
        assert rows["url"].value == "http://from-env:2"
        assert rows["url"].source == "environment"

    def test_file_is_reported_by_path(self, tmp_path):
        path = tmp_path / ".env"
        save_settings(path, {"NAVIDROME_URL": "http://your-server:4533"})
        rows = {r.setting.key: r for r in describe_settings(path, {})}
        assert rows["url"].source == str(path)

    def test_unset_optional_falls_back_to_default(self, tmp_path):
        path = tmp_path / ".env"
        save_settings(path, {"NAVIDROME_URL": "http://your-server:4533"})
        rows = {r.setting.key: r for r in describe_settings(path, {})}
        assert rows["timeout"].value == "20"
        assert rows["timeout"].source == "default"

    def test_password_is_masked_in_display(self, tmp_path):
        path = tmp_path / ".env"
        save_settings(path, {"NAVIDROME_PASSWORD": "hunter2-not-real"})
        rows = {r.setting.key: r for r in describe_settings(path, {})}
        assert rows["password"].display == "********"
        assert "hunter2-not-real" not in rows["password"].display


class TestConfigCommand:
    def test_set_and_list(self, cfg, capsys):
        assert main(["config", "set", "url", "your-server:8102"]) == ExitCode.OK
        capsys.readouterr()
        assert main(["config", "list"]) == ExitCode.OK
        out = capsys.readouterr().out
        assert "http://your-server:4533" in out, "the URL should be normalised on the way in"

    def test_set_rejects_an_invalid_url(self, cfg, capsys):
        # main() turns ReleaseCheckError into an exit code, it does not raise.
        assert main(["config", "set", "url", "ftp://nope"]) == ExitCode.CONFIG
        assert "http or https" in capsys.readouterr().err
        assert not cfg.exists(), "a rejected value must not be written"

    def test_password_is_refused_on_the_command_line(self, cfg, capsys):
        code = main(["config", "set", "password", "hunter2"])
        err = capsys.readouterr().err
        assert code == ExitCode.CONFIG
        assert "shell history" in err
        assert "config password" in err
        assert not cfg.exists(), "nothing should have been written"

    def test_password_never_reaches_the_file_via_set(self, cfg, capsys):
        main(["config", "set", "url", "your-server:8102"])
        main(["config", "set", "password", "hunter2"])
        assert "hunter2" not in cfg.read_text()

    def test_set_one_key_leaves_others_alone(self, cfg):
        main(["config", "set", "url", "your-server:8102"])
        main(["config", "set", "username", "branden"])
        main(["config", "set", "workers", "8"])
        values = load_dotenv(cfg)
        assert values["NAVIDROME_URL"] == "http://your-server:4533"
        assert values["NAVIDROME_USERNAME"] == "branden"
        assert values["WORKERS"] == "8"

    def test_unset(self, cfg, capsys):
        main(["config", "set", "workers", "8"])
        assert main(["config", "unset", "workers"]) == ExitCode.OK
        assert "WORKERS" not in load_dotenv(cfg)

    def test_list_reports_what_is_missing(self, cfg, capsys):
        main(["config", "list"])
        captured = capsys.readouterr()
        assert "Not configured" in captured.err
        assert "setup" in captured.err
        # The advice must not contaminate the listing itself.
        assert "Not configured" not in captured.out

    def test_list_warns_when_the_environment_masks_the_file(self, cfg, monkeypatch, capsys):
        main(["config", "set", "url", "your-server:8102"])
        monkeypatch.setenv("NAVIDROME_URL", "http://override:9")
        capsys.readouterr()
        main(["config", "list"])
        out = capsys.readouterr().out
        assert "http://override:9" in out
        assert "environment" in out

    def test_set_notes_an_environment_override(self, cfg, monkeypatch, capsys):
        monkeypatch.setenv("NAVIDROME_URL", "http://override:9")
        main(["config", "set", "url", "your-server:8102"])
        assert "still" in capsys.readouterr().err

    def test_path(self, cfg, capsys):
        assert main(["config", "path"]) == ExitCode.OK
        assert capsys.readouterr().out.strip().endswith(".env")

    def test_bare_config_lists(self, cfg, capsys):
        assert main(["config"]) == ExitCode.OK
        assert "url" in capsys.readouterr().out


class TestSetupCommand:
    def test_requires_a_terminal(self, cfg, monkeypatch, capsys):
        monkeypatch.setattr(sys.stdin, "isatty", lambda: False)
        assert main(["setup"]) == ExitCode.CONFIG
        err = capsys.readouterr().err
        assert "interactive terminal" in err
        assert "config set" in err, "must point at the scriptable alternative"

    def test_init_is_gone(self):
        from release_check.cli import SUBCOMMANDS

        assert "init" not in SUBCOMMANDS
        assert {"setup", "config"} <= SUBCOMMANDS


class TestHintsNameRealCommands:
    """A fresh install's first message must not point at a removed command."""

    def test_no_config_hint_points_at_setup(self, cfg, capsys):
        assert main(["check"]) == ExitCode.CONFIG
        hint = capsys.readouterr().err
        assert "setup" in hint
        assert "init" not in hint, "`init` was replaced by `setup`"

    def test_missing_env_file_hint_points_at_setup(self, cfg, capsys, tmp_path):
        assert main(["check", "--env-file", str(tmp_path / "nope.env")]) == ExitCode.CONFIG
        hint = capsys.readouterr().err
        assert "setup" in hint
        assert "init" not in hint

    def test_every_command_named_in_a_hint_exists(self, cfg, capsys):
        import re

        from release_check.cli import SUBCOMMANDS

        main(["check"])
        main(["config", "list"])
        text = capsys.readouterr()
        blob = text.out + text.err
        # Pull `release-check <word>` style references out of the guidance.
        for match in re.finditer(r"release-check ([a-z-]+)", blob):
            assert match.group(1) in SUBCOMMANDS, f"hint names unknown command {match.group(1)!r}"

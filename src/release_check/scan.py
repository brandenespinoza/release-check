"""Scan orchestration: library in, missing releases out.

Request volume is the main design constraint, so work is staged: cheap
listing data first, and expensive per-release detail only for releases that
still look missing after the cheap pass. Everything fetched is cached, so a
second run costs almost nothing.
"""

from __future__ import annotations

import logging
import sys
from dataclasses import dataclass, field

from .artist_match import ArtistResolver, Resolution
from .classify import resolve_type
from .config import Config
from .deezer import DeezerProvider
from .dedupe import dedupe_results, deduplicate
from .errors import ReleaseCheckError
from .models import (
    DeezerRelease,
    LocalAlbum,
    LocalArtist,
    LocalTrack,
    MissingRelease,
    Ownership,
    ReleaseType,
    ReviewItem,
    UnresolvedArtist,
)
from .navidrome import NavidromeClient
from .normalize import fold
from .release_match import (
    IsrcIndex,
    LocalIndex,
    determine_ownership,
    refine_with_isrc,
)
from .state import Store

log = logging.getLogger("release_check.scan")

#: Owned Deezer albums consulted when checking whether a single is an advance
#: single. Bounded because each one costs two extra requests.
MAX_ISRC_REFERENCE_ALBUMS = 4
ISRC_REFERENCE_YEAR_WINDOW = 2


@dataclass
class ScanOptions:
    artist_filters: list[str] = field(default_factory=list)
    limit: int | None = None
    since_year: int | None = None
    types: set[ReleaseType] | None = None
    progress: bool = True


@dataclass
class ArtistOutcome:
    """What one artist contributed, for the live progress line."""

    name: str
    missing: list[MissingRelease] = field(default_factory=list)
    review: int = 0
    unresolved: UnresolvedArtist | None = None
    failed: bool = False

    @property
    def worth_reporting(self) -> bool:
        """Artists with nothing to say stay off the scroll, to keep it dense."""
        return bool(self.missing) or self.review or self.unresolved or self.failed

    def describe(self) -> str:
        if self.failed:
            return "failed"
        if self.unresolved is not None:
            count = len(self.unresolved.candidates)
            return f"unresolved{f', {count} candidates' if count else ''}"
        parts: list[str] = []
        if self.missing:
            counts: dict[ReleaseType, int] = {}
            for item in self.missing:
                counts[item.release_type] = counts.get(item.release_type, 0) + 1
            breakdown = ", ".join(
                f"{n} {_LABELS[t][0] if n == 1 else _LABELS[t][1]}"
                for t, n in sorted(counts.items(), key=lambda kv: kv[0].value)
            )
            parts.append(f"{len(self.missing)} missing ({breakdown})")
        if self.review:
            parts.append(f"{self.review} to review")
        return ", ".join(parts) or "nothing new"


_LABELS = {
    ReleaseType.ALBUM: ("album", "albums"),
    ReleaseType.EP: ("EP", "EPs"),
    ReleaseType.SINGLE: ("single", "singles"),
    ReleaseType.UNKNOWN: ("unclassified", "unclassified"),
}


@dataclass
class ScanResult:
    missing: list[MissingRelease] = field(default_factory=list)
    review: list[ReviewItem] = field(default_factory=list)
    unresolved: list[UnresolvedArtist] = field(default_factory=list)
    artists_scanned: int = 0
    partial_reasons: list[str] = field(default_factory=list)


class Scanner:
    def __init__(
        self,
        client: NavidromeClient,
        provider: DeezerProvider,
        store: Store,
        config: Config,
    ) -> None:
        self.client = client
        self.provider = provider
        self.store = store
        self.config = config
        self.resolver = ArtistResolver(provider, store)

    # --- library -----------------------------------------------------------

    def read_library(self, options: ScanOptions) -> list[LocalArtist]:
        artists = self.client.get_artists()
        albums = self.client.get_all_albums()

        by_id: dict[str, LocalArtist] = {a.id: a for a in artists}
        by_name: dict[str, LocalArtist] = {fold(a.name): a for a in artists}

        orphans = 0
        for album in albums:
            artist = by_id.get(album.artist_id) or by_name.get(fold(album.artist))
            if artist is None:
                orphans += 1
                continue
            artist.albums.append(album)
        if orphans:
            log.info("%d album(s) had no matching album artist entry", orphans)

        if options.artist_filters:
            wanted = {fold(name) for name in options.artist_filters}
            artists = [a for a in artists if fold(a.name) in wanted]
            missing = wanted - {fold(a.name) for a in artists}
            for name in sorted(missing):
                log.warning("No artist named %r in the library", name)

        artists.sort(key=lambda a: fold(a.name))
        if options.limit:
            artists = artists[: options.limit]
        return artists

    def load_local_tracks(self, artist: LocalArtist) -> int:
        """Fill in track lists for the artist's albums, using the cache."""
        pending: list[LocalAlbum] = []
        for album in artist.albums:
            cached = self.store.get_local_tracks(album.id, album.fingerprint)
            if cached is not None:
                album.tracks = [LocalTrack(**row) for row in cached]
                album.tracks_loaded = True
            else:
                pending.append(album)

        if not pending:
            return 0

        failures = self.client.load_tracks(pending, workers=self.config.workers)
        for album in pending:
            if album.tracks_loaded:
                self.store.set_local_tracks(
                    album.id,
                    album.fingerprint,
                    [vars(t) for t in album.tracks],
                )
        return failures

    # --- scan --------------------------------------------------------------

    def run(self, options: ScanOptions) -> ScanResult:
        result = ScanResult()
        artists = self.read_library(options)
        total = len(artists)

        for position, artist in enumerate(artists, start=1):
            self._progress(options, position, total, artist.name)
            before = (len(result.missing), len(result.review), len(result.unresolved))
            outcome = ArtistOutcome(name=artist.name)
            try:
                self._scan_artist(artist, options, result)
            except ReleaseCheckError as exc:
                log.warning("Skipping %r: %s", artist.name, exc)
                result.partial_reasons.append(f"{artist.name} failed")
                outcome.failed = True
            except Exception as exc:  # noqa: BLE001 - one artist must not abort the run
                log.warning("Unexpected failure for %r: %s", artist.name, exc)
                result.partial_reasons.append(f"{artist.name} failed")
                outcome.failed = True
            result.artists_scanned += 1

            outcome.missing = result.missing[before[0] :]
            outcome.review = len(result.review) - before[1]
            if len(result.unresolved) > before[2]:
                outcome.unresolved = result.unresolved[-1]
            self._report_artist(options, position, total, outcome)

        self._clear_progress(options)

        result.missing = dedupe_results(result.missing, lambda item: item.release_type)
        self.store.save_unresolved(
            [
                {
                    "name": u.name,
                    "reason": u.reason,
                    "candidates": [
                        {"id": c.id, "name": c.name, "fans": c.nb_fan}
                        for c in u.candidates
                    ],
                }
                for u in result.unresolved
            ]
        )
        return result

    def _scan_artist(
        self, artist: LocalArtist, options: ScanOptions, result: ScanResult
    ) -> None:
        resolution = self.resolver.resolve(artist)

        if resolution.status is Resolution.IGNORED:
            return
        if resolution.status is Resolution.ERROR:
            result.partial_reasons.append(f"{artist.name} lookup failed")
            result.unresolved.append(
                UnresolvedArtist(artist.name, resolution.reason, resolution.candidates)
            )
            return
        if not resolution.ok:
            result.unresolved.append(
                UnresolvedArtist(artist.name, resolution.reason, resolution.candidates)
            )
            return

        if resolution.confidence < 0.7:
            log.info(
                "Low-confidence match: %r -> %r (%s)",
                artist.name,
                resolution.artist.name,
                resolution.reason,
            )

        releases = resolution.discography
        if not releases:
            releases = self.provider.get_discography(resolution.artist.id)

        if options.since_year:
            releases = [
                r
                for r in releases
                if r.release_date.year is None or r.release_date.year >= options.since_year
            ]

        canonical, _ = deduplicate(releases, artist_key=fold(artist.name))
        if not canonical:
            return

        ignored_ids = self.store.ignored_release_ids()
        index = LocalIndex(artist)

        # First pass on listing data alone: cheap, and settles anything that
        # clearly matches an owned album.
        candidates: list[DeezerRelease] = []
        for release in canonical:
            release_type, _ = resolve_type(release)
            verdict = determine_ownership(release, index, release_type, ignored_ids)
            if verdict.ownership in (Ownership.OWNED, Ownership.PROBABLY_OWNED, Ownership.IGNORED):
                continue
            candidates.append(release)

        if not candidates:
            return

        # Local tracks are only needed once something looks missing.
        track_failures = self.load_local_tracks(artist)
        if track_failures:
            result.partial_reasons.append(
                f"{track_failures} album(s) of {artist.name} could not be read"
            )
        index = LocalIndex(artist)

        judged: list[tuple[DeezerRelease, ReleaseType, object, tuple[str, ...]]] = []
        owned_albums: list[DeezerRelease] = []

        for release in candidates:
            try:
                self.provider.load_release_detail(release)
            except ReleaseCheckError as exc:
                log.debug("No detail for %r: %s", release.title, exc)
            release_type, classification = resolve_type(release)
            verdict = determine_ownership(release, index, release_type, ignored_ids)
            judged.append(
                (release, release_type, verdict, tuple(sorted(classification.traits)))
            )

        # Re-check the releases the first pass considered owned, so their
        # ISRCs can vouch for advance singles.
        needs_isrc = any(
            v.reportable and t is ReleaseType.SINGLE for _, t, v, _traits in judged
        )
        if needs_isrc:
            owned_albums = self._isrc_reference_albums(canonical, index, ignored_ids)

        isrc_index = IsrcIndex()
        for album in owned_albums:
            isrc_index.add_release(album)

        for release, release_type, verdict, traits in judged:
            if release_type is ReleaseType.SINGLE and isrc_index.codes:
                verdict = refine_with_isrc(verdict, release, isrc_index)

            if options.types and release_type not in options.types:
                continue

            if verdict.ownership is Ownership.AMBIGUOUS:
                result.review.append(
                    ReviewItem(release, artist.name, release_type, verdict.reason)
                )
            elif verdict.reportable:
                result.missing.append(
                    MissingRelease(
                        release=release,
                        local_artist=artist.name,
                        release_type=release_type,
                        ownership=verdict.ownership,
                        reason=verdict.reason,
                        traits=traits,
                    )
                )

    def _isrc_reference_albums(
        self,
        canonical: list[DeezerRelease],
        index: LocalIndex,
        ignored_ids: set[str],
    ) -> list[DeezerRelease]:
        """Fetch detail for a few owned albums to harvest their ISRCs."""
        owned: list[DeezerRelease] = []
        for release in canonical:
            if len(owned) >= MAX_ISRC_REFERENCE_ALBUMS:
                break
            release_type, _ = resolve_type(release)
            if release_type is ReleaseType.SINGLE:
                continue
            verdict = determine_ownership(release, index, release_type, ignored_ids)
            if verdict.ownership not in (Ownership.OWNED, Ownership.PROBABLY_OWNED):
                continue
            try:
                self.provider.load_release_detail(release)
            except ReleaseCheckError:
                continue
            owned.append(release)
        return owned

    # --- progress ----------------------------------------------------------

    def _show_progress(self, options: ScanOptions) -> bool:
        # Only on a terminal: piping stderr somewhere should stay clean, and
        # the transient line depends on ANSI erase.
        return options.progress and sys.stderr.isatty()

    def _progress(self, options: ScanOptions, position: int, total: int, name: str) -> None:
        """Transient line for the artist currently being scanned."""
        if not self._show_progress(options):
            return
        label = name if len(name) <= 40 else name[:39] + "…"
        sys.stderr.write(f"\r\033[2K[{position}/{total}] {label}")
        sys.stderr.flush()

    def _report_artist(
        self, options: ScanOptions, position: int, total: int, outcome: ArtistOutcome
    ) -> None:
        """Permanent line, written only when the artist yielded something.

        Findings are summarised rather than listed: printing the rows here
        would duplicate the final table, and the point of this line is to show
        the scan is alive and productive, not to be read as the report.
        """
        if not self._show_progress(options) or not outcome.worth_reporting:
            return
        label = outcome.name if len(outcome.name) <= 40 else outcome.name[:39] + "…"
        sys.stderr.write(
            f"\r\033[2K[{position}/{total}] {label} — {outcome.describe()}\n"
        )
        sys.stderr.flush()

    def _clear_progress(self, options: ScanOptions) -> None:
        if self._show_progress(options):
            sys.stderr.write("\r\033[2K")
            sys.stderr.flush()

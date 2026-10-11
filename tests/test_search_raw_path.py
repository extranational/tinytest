"""Raw-record naming: a fingerprint is not a safe filename.

Regression for the defect that aborted the first full search run on the host:
`OSError: [Errno 36] File name too long` on a CVE-roundup fingerprint carrying 19 CVE
ids. `report.search_rows` builds the same path, so `analyze` died the same way and
reported every story as missing.
"""

from __future__ import annotations

import os
import tempfile
from pathlib import Path

from tinytest import search_ab

LONG_CVE_FINGERPRINT = (
    "chrome-152-0-7977-75-vulns+CVE-2026-84323+CVE-2026-84325+CVE-2026-84326"
    "+CVE-2026-84327+CVE-2026-84328+CVE-2026-84329+CVE-2026-84330+CVE-2026-84331"
    "+CVE-2026-84332+CVE-2026-84333+CVE-2026-84334+CVE-2026-84335+CVE-2026-84347"
    "+CVE-2026-84348+CVE-2026-84349+CVE-2026-84350+CVE-2026-84351+CVE-2026-84355"
    "+CVE-2026-84356+CVE-2026-84358"
)


def test_long_fingerprint_produces_a_usable_path():
    path = search_ab.raw_path(Path("/tmp"), LONG_CVE_FINGERPRINT, "brave", 1)
    assert len(path.name.encode()) <= 255
    with tempfile.TemporaryDirectory() as d:
        real = search_ab.raw_path(Path(d), LONG_CVE_FINGERPRINT, "brave", 1)
        real.write_text("{}", encoding="utf-8")     # the call that used to raise OSError
        assert real.exists()
        assert os.stat(real).st_size == 2


def test_name_is_deterministic_and_distinguishes_similar_fingerprints():
    a = search_ab.safe_name(LONG_CVE_FINGERPRINT)
    assert a == search_ab.safe_name(LONG_CVE_FINGERPRINT)
    # Same 120-char prefix, different tail -> must not collide.
    base = "x" * 200
    assert search_ab.safe_name(base + "CVE-2026-0001") != search_ab.safe_name(base + "CVE-2026-0002")


def test_arms_and_reps_do_not_collide():
    names = {
        search_ab.raw_path(Path("/tmp"), LONG_CVE_FINGERPRINT, arm, rep).name
        for arm in search_ab.ARMS
        for rep in (1, 2, 3)
    }
    assert len(names) == 6


def test_short_fingerprint_stays_recognisable():
    name = search_ab.safe_name("some-normal-story")
    assert name.startswith("some-normal-story-")
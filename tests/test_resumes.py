"""Resume copies held by the app, not pointed at.

The pointer model lost the "before" the moment somebody acted on our advice,
and a moved file read as an empty resume - which scores every skill as
missing and is indistinguishable from a genuinely thin one.
"""
from __future__ import annotations

import pytest

from unlatched import resumes


def test_attaching_copies_the_file_in(home, tmp_path):
    src = tmp_path / "cv.txt"
    src.write_text("Customer service and troubleshooting.", encoding="utf-8")
    record = resumes.attach(src, resumes.ORIGINAL, home)
    stored = resumes.resumes_dir(home) / record["file"]
    assert stored.is_file()
    assert stored.read_text(encoding="utf-8") == src.read_text(encoding="utf-8")
    # The source is untouched - we copy, never move.
    assert src.is_file()


def test_a_new_optimized_copy_never_destroys_the_previous_one(home, tmp_path):
    """A search screened against one resume has to stay reproducible after the
    next optimisation pass."""
    for text in ("first pass", "second pass"):
        src = tmp_path / "cv.txt"
        src.write_text(text, encoding="utf-8")
        resumes.attach(src, resumes.OPTIMIZED, home)
    optimized = [v for v in resumes.versions(home) if v["role"] == resumes.OPTIMIZED]
    assert len(optimized) == 2


def test_screening_reads_the_optimized_copy_when_there_is_one(home, tmp_path):
    original = tmp_path / "original.txt"
    original.write_text("before", encoding="utf-8")
    resumes.attach(original, resumes.ORIGINAL, home)
    assert resumes.active_path({}, home).read_text(encoding="utf-8") == "before"

    optimized = tmp_path / "optimized.txt"
    optimized.write_text("after", encoding="utf-8")
    resumes.attach(optimized, resumes.OPTIMIZED, home)
    assert resumes.active_path({}, home).read_text(encoding="utf-8") == "after"


def test_a_profile_with_only_the_legacy_pointer_still_works(home, tmp_path):
    """Profiles set up before any of this existed must not silently lose their
    resume."""
    legacy = tmp_path / "legacy.txt"
    legacy.write_text("legacy", encoding="utf-8")
    active = resumes.active_path({"resume_path": str(legacy)}, home)
    assert active is not None
    assert active.read_text(encoding="utf-8") == "legacy"


def test_a_legacy_pointer_at_a_missing_file_reports_nothing(home):
    assert resumes.active_path({"resume_path": "/nope/gone.txt"}, home) is None


def test_an_unreadable_format_is_stored_but_flagged(home, tmp_path):
    """Refusing somebody's own document helps nobody, but a format we cannot
    read scores every skill missing - so they hear it from us."""
    src = tmp_path / "cv.pdf"
    src.write_bytes(b"%PDF-1.4 not really")
    record = resumes.attach(src, resumes.ORIGINAL, home)
    assert record["readable"] is False


def test_an_unknown_role_is_refused(home, tmp_path):
    src = tmp_path / "cv.txt"
    src.write_text("x", encoding="utf-8")
    with pytest.raises(ValueError, match="role"):
        resumes.attach(src, "sideways", home)
    assert not list(resumes.resumes_dir(home).glob("*")), (
        "a refused role must not leave a copy behind")


def test_a_pin_cannot_name_a_file_outside_the_profile(home, tmp_path):
    """A pin names one of the attached copies. Nothing else.

    `resumes_dir(home) / pinned` used to be returned whenever it was a file,
    and pathlib does not normalise "..", so a pin of "../secrets.txt" resolved
    outside the profile and was read as the resume - verified before the fix,
    not theorised.

    What makes it worth a guard rather than a note: resume text is MINE-class
    in this app's trust model, which attachments.py describes as offered
    freely to an assistant. A pin pointing elsewhere hands some other file's
    contents to one.
    """
    src = tmp_path / "cv.txt"
    src.write_text("the real resume", encoding="utf-8")
    resumes.attach(src, resumes.ORIGINAL, home)

    outside = home.parent / "not-a-resume.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("contents that are not a resume", encoding="utf-8")

    for escape in ("../not-a-resume.txt",
                   "../../not-a-resume.txt",
                   str(outside)):
        chosen = resumes.active_path({"resume_pinned": escape}, home)
        assert chosen is not None, "the escape should fall back, not blank out"
        assert chosen.read_text(encoding="utf-8") == "the real resume", (
            f"pin {escape!r} reached {chosen}")


def test_a_file_dropped_into_the_directory_is_not_pinnable(home, tmp_path):
    """The membership check is against what the APP attached, so a file
    somebody copies in by hand cannot be pinned either - it carries no role
    prefix and `versions` does not list it."""
    src = tmp_path / "cv.txt"
    src.write_text("the real resume", encoding="utf-8")
    resumes.attach(src, resumes.ORIGINAL, home)

    stray = resumes.resumes_dir(home) / "stray.txt"
    stray.write_text("not attached by the app", encoding="utf-8")

    chosen = resumes.active_path({"resume_pinned": "stray.txt"}, home)
    assert chosen.read_text(encoding="utf-8") == "the real resume"


def test_an_ordinary_pin_is_still_honoured(home, tmp_path):
    """The positive control. A guard that ignored every pin would satisfy both
    tests above and quietly remove the feature.

    IT PINS THE COPY THE AUTOMATIC RULE WOULD NOT CHOOSE, which is the only
    version of this test that controls anything. Pinning the optimized copy -
    what this did - is the same answer the rule gives on its own, so ignoring
    pins entirely passed it. Screening prefers optimized; the pin here names
    the ORIGINAL, so only the pin can produce this result.
    """
    first = tmp_path / "one.txt"
    first.write_text("the original", encoding="utf-8")
    original = resumes.attach(first, resumes.ORIGINAL, home)

    second = tmp_path / "two.txt"
    second.write_text("the optimised one", encoding="utf-8")
    resumes.attach(second, resumes.OPTIMIZED, home)

    # Unpinned, the optimised copy wins - which is what makes the next
    # assertion mean something.
    assert resumes.active_path({}, home).read_text(
        encoding="utf-8") == "the optimised one"

    chosen = resumes.active_path({"resume_pinned": original["file"]}, home)
    assert chosen.read_text(encoding="utf-8") == "the original", (
        "the pin was ignored in favour of the automatic rule")

from __future__ import annotations

import re

import pytest

from apps.export.password import generate_export_password, _normalize


class TestPasswordFormat:
    def test_password_is_exactly_6_chars(self):
        pw = generate_export_password(
            first_name="Sahil",
            last_name="Deshpande",
            university="APCOE",
            major="AIDS",
            minor="Data Science",
        )
        assert len(pw) == 6, f"Expected 6 chars, got {len(pw)}: {pw!r}"

    def test_password_contains_only_uppercase_alnum(self):
        pw = generate_export_password(
            first_name="Jane",
            last_name="Doe",
            university="MIT",
            major="CS",
            minor="Math",
        )
        assert re.fullmatch(r"[A-Z0-9]{6}", pw), f"Unexpected chars in: {pw!r}"

    def test_initials_are_first_two_chars(self):
        pw = generate_export_password(
            first_name="Alice",
            last_name="Brown",
            university="Harvard",
            major="Law",
            minor=None,
        )
        assert pw[0] == "A", f"Expected first initial A, got {pw[0]!r}"
        assert pw[1] == "B", f"Expected last initial B, got {pw[1]!r}"

    def test_suffix_is_4_chars(self):
        pw = generate_export_password(
            first_name="Tom",
            last_name="Smith",
            university="Stanford",
            major="Physics",
            minor="Biology",
        )
        assert len(pw[2:]) == 4


class TestPasswordDeterminism:
    def test_same_inputs_produce_same_password(self):
        kwargs = dict(
            first_name="Sahil",
            last_name="Deshpande",
            university="APCOE",
            major="AIDS",
            minor="Data Science",
        )
        pw1 = generate_export_password(**kwargs)
        pw2 = generate_export_password(**kwargs)
        assert pw1 == pw2

    def test_different_first_name_changes_password(self):
        base = dict(last_name="Doe", university="MIT", major="CS", minor="Math")
        pw1 = generate_export_password(first_name="Jane", **base)
        pw2 = generate_export_password(first_name="John", **base)
        assert pw1 != pw2

    def test_different_last_name_changes_password(self):
        base = dict(first_name="Jane", university="MIT", major="CS", minor="Math")
        pw1 = generate_export_password(last_name="Doe", **base)
        pw2 = generate_export_password(last_name="Smith", **base)
        assert pw1 != pw2

    def test_different_university_changes_password(self):
        base = dict(first_name="Jane", last_name="Doe", major="CS", minor="Math")
        pw1 = generate_export_password(university="MIT", **base)
        pw2 = generate_export_password(university="Harvard", **base)
        assert pw1 != pw2

    def test_different_major_changes_password(self):
        base = dict(first_name="Jane", last_name="Doe", university="MIT", minor="Math")
        pw1 = generate_export_password(major="CS", **base)
        pw2 = generate_export_password(major="Physics", **base)
        assert pw1 != pw2

    def test_different_minor_changes_password(self):
        base = dict(first_name="Jane", last_name="Doe", university="MIT", major="CS")
        pw1 = generate_export_password(minor="Math", **base)
        pw2 = generate_export_password(minor="Biology", **base)
        assert pw1 != pw2


class TestPasswordEdgeCases:
    def test_none_fields_produce_valid_password(self):
        pw = generate_export_password(
            first_name=None,
            last_name=None,
            university=None,
            major=None,
            minor=None,
        )
        assert len(pw) == 6
        assert re.fullmatch(r"[A-Z0-9]{6}", pw)

    def test_none_first_name_uses_x_initial(self):
        pw = generate_export_password(
            first_name=None,
            last_name="Doe",
            university="MIT",
            major="CS",
            minor="Math",
        )
        assert pw[0] == "X"

    def test_none_last_name_uses_x_initial(self):
        pw = generate_export_password(
            first_name="Jane",
            last_name=None,
            university="MIT",
            major="CS",
            minor="Math",
        )
        assert pw[1] == "X"

    def test_whitespace_is_normalised(self):
        pw1 = generate_export_password(
            first_name="  Jane  ",
            last_name="Doe",
            university="MIT",
            major="CS",
            minor="Math",
        )
        pw2 = generate_export_password(
            first_name="Jane",
            last_name="Doe",
            university="MIT",
            major="CS",
            minor="Math",
        )
        assert pw1 == pw2

    def test_case_is_normalised(self):
        pw1 = generate_export_password(
            first_name="JANE",
            last_name="DOE",
            university="MIT",
            major="CS",
            minor="Math",
        )
        pw2 = generate_export_password(
            first_name="jane",
            last_name="doe",
            university="mit",
            major="cs",
            minor="math",
        )
        assert pw1 == pw2


class TestNormalizeHelper:
    def test_normalize_empty_string_returns_empty(self):
        assert _normalize("") == ""

    def test_normalize_none_returns_empty(self):
        assert _normalize(None) == ""

    def test_normalize_collapses_spaces(self):
        assert _normalize("Data  Science") == "data science"

    def test_normalize_strips_and_lowercases(self):
        assert _normalize("  CS  ") == "cs"

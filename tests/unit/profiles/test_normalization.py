from apps.profiles.normalization import (
    collect_normalized_program_names,
    normalize_major_minor,
    normalize_named_program_list,
)


def test_normalize_major_minor_title_cases_and_collapses_whitespace() -> None:
    assert normalize_major_minor("AI") == "Ai"
    assert normalize_major_minor("aI") == "Ai"
    assert normalize_major_minor("Ai") == "Ai"
    assert normalize_major_minor("ai") == "Ai"
    assert normalize_major_minor("  Computer   Science  ") == "Computer Science"
    assert normalize_major_minor("computer science") == "Computer Science"
    assert normalize_major_minor("Computer science") == "Computer Science"
    assert normalize_major_minor("computer Science") == "Computer Science"
    assert normalize_major_minor("Computer Science") == "Computer Science"
    assert normalize_major_minor("   ") is None
    assert normalize_major_minor(None) is None


def test_normalize_named_program_list_dedupes_case_variants() -> None:
    result = normalize_named_program_list(
        [
            {"name": "AI"},
            {"name": "aI"},
            {"name": "Computer Science"},
            {"name": "computer science"},
            {"name": "  "},
        ]
    )
    assert result == [
        {"name": "Ai"},
        {"name": "Computer Science"},
    ]


def test_collect_normalized_program_names_merges_sources() -> None:
    names = collect_normalized_program_names(
        "AI",
        "Ai",
        [{"name": "aI"}, {"name": "Math"}],
        "math",
        None,
        [],
    )
    assert names == ["Ai", "Math"]

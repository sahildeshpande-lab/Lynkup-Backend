from apps.profiles.normalization import (
    collect_normalized_program_names,
    normalize_major_minor,
    normalize_named_program_list,
)


def test_normalize_major_minor_lowercases_and_collapses_whitespace() -> None:
    assert normalize_major_minor("AI") == "ai"
    assert normalize_major_minor("aI") == "ai"
    assert normalize_major_minor("Ai") == "ai"
    assert normalize_major_minor("ai") == "ai"
    assert normalize_major_minor("  Computer   Science  ") == "computer science"
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
        {"name": "ai"},
        {"name": "computer science"},
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
    assert names == ["ai", "math"]

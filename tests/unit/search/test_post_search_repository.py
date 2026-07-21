from __future__ import annotations

import uuid

from apps.search.repositories import post_search_repository as repo


def test_normalize_hashtag():
    assert repo._normalize_hashtag("#AI") == "ai"
    assert repo._normalize_hashtag(" machine-learning ") == "machine-learning"


def test_try_parse_uuid():
    value = str(uuid.uuid4())
    assert repo._try_parse_uuid(value) == uuid.UUID(value)
    assert repo._try_parse_uuid("not-a-uuid") is None
    assert repo._try_parse_uuid("#ai") is None


def test_resolve_edu_level():
    assert repo._resolve_edu_level("1") == "Bachelors"
    assert repo._resolve_edu_level("Bachelors") == "Bachelors"
    assert repo._resolve_edu_level("masters") == "Masters"
    assert repo._resolve_edu_level("99") is None
    assert repo._resolve_edu_level("UnknownLevel") is None


def test_edu_level_match_clause_ors_selected_levels():
    from sqlalchemy.dialects import postgresql

    from apps.profiles.db_models.profile_db_model import Profile

    clause = repo._edu_level_match_clause(Profile, ["1", "Masters", "Bachelors"])
    assert clause is not None
    compiled = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    ).lower()
    assert "bachelors" in compiled
    assert "masters" in compiled
    assert " or " in compiled


def test_edu_level_match_clause_unknown_values_match_nothing():
    from apps.profiles.db_models.profile_db_model import Profile

    clause = repo._edu_level_match_clause(Profile, ["not-a-real-level"])
    assert clause is not None
    # false() compiles differently by dialect; ensure we did not skip the filter.
    assert str(clause) != "None"


def test_split_filter_values_preserves_university_names_with_commas():
    assert repo._split_filter_values("University of California, Berkeley") == [
        "University of California, Berkeley"
    ]


def test_split_filter_values_splits_multiple_university_ids_and_pipes():
    uni_a = str(uuid.uuid4())
    uni_b = str(uuid.uuid4())
    assert repo._split_filter_values(f"{uni_a}|{uni_b}") == [uni_a, uni_b]
    assert repo._split_filter_values(f"{uni_a},{uni_b}") == [uni_a, uni_b]
    assert repo._split_filter_values([uni_a, uni_b]) == [uni_a, uni_b]
    assert repo._split_filter_values("ai|ml;nlp") == ["ai", "ml", "nlp"]
    assert repo._split_filter_values(
        ["Harvard University", "Stanford University"]
    ) == ["Harvard University", "Stanford University"]


def test_university_match_clause_ors_multiple_ids():
    from sqlalchemy.dialects import postgresql

    from apps.profiles.db_models.profile_db_model import Profile

    uni_a = uuid.uuid4()
    uni_b = uuid.uuid4()
    clause = repo._university_match_clause(
        [str(uni_a), str(uni_b)],
        university_id_column=Profile.university_id,
    )
    assert clause is not None
    compiled = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert str(uni_a) in compiled
    assert str(uni_b) in compiled
    assert "university_id" in compiled.lower() or "universities" in compiled.lower()


def test_university_match_clause_ors_id_and_name():
    from sqlalchemy.dialects import postgresql

    from apps.profiles.db_models.profile_db_model import Profile

    uni_a = uuid.uuid4()
    clause = repo._university_match_clause(
        [str(uni_a), "State University"],
        university_id_column=Profile.university_id,
    )
    assert clause is not None
    compiled = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False})
    ).upper()
    assert " OR " in compiled


def test_build_search_filters_query_matches_author_name():
    from sqlalchemy.dialects import postgresql
    from sqlalchemy.orm import aliased

    from apps.accounts.db_models import User
    from apps.profiles.db_models.profile_db_model import Profile

    author_profile = aliased(Profile, name="author_profile")
    author_user = aliased(User, name="author_user")
    filters = repo._build_search_filters(
        current_user_id=uuid.uuid4(),
        connected_author_ids=set(),
        query="Sahil",
        hashtag=None,
        academic_interest=None,
        university_name=None,
        major=None,
        minor=None,
        country=None,
        edu_level=None,
        author_profile=author_profile,
        author_user=author_user,
    )
    compiled = " ".join(
        str(f.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True}))
        for f in filters
    ).lower()
    assert "first_name" in compiled
    assert "last_name" in compiled
    assert "%sahil%" in compiled


def test_country_match_clause_ors_ids_and_names():
    from sqlalchemy.dialects import postgresql

    from apps.profiles.db_models.profile_db_model import Profile

    country_a = uuid.uuid4()
    clause = repo._country_match_clause(
        [str(country_a), "India", "US"],
        country_id_column=Profile.country_id,
    )
    assert clause is not None
    compiled = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": True})
    )
    assert str(country_a) in compiled
    assert "India" in compiled or "india" in compiled.lower()
    assert " OR " in compiled.upper()


def test_split_filter_values_hashtag_whitespace():
    assert repo._split_filter_values("ai ml", split_whitespace=True) == ["ai", "ml"]
    assert repo._split_filter_values("#AI,#ML", split_whitespace=True) == ["#AI", "#ML"]


def test_split_filter_values_academic_interests_pipe_or_list():
    assert repo._split_filter_values("AI|Machine Learning|NLP") == [
        "AI",
        "Machine Learning",
        "NLP",
    ]
    assert repo._split_filter_values(["1", "2", "3"]) == ["1", "2", "3"]
    assert repo._split_filter_values("10|20|30") == ["10", "20", "30"]


def test_escape_like_exact():
    assert repo._escape_like_exact("C_Programming") == "C\\_Programming"
    assert repo._escape_like_exact("100%") == "100\\%"


def test_academic_interest_match_clause_none_for_empty():
    assert repo._academic_interest_match_clause(object(), []) is None
    assert repo._academic_interest_match_clause(object(), ["", "  "]) is None


def test_academic_interest_match_clause_builds_or_across_selected_interests():
    """Selecting A|B|C must OR interest predicates so A still matches if B/C have no rows."""
    from sqlalchemy.dialects import postgresql

    from apps.profiles.db_models.profile_db_model import Profile

    clause = repo._academic_interest_match_clause(
        Profile,
        ["1", "Missing Interest", "Artificial Intelligence"],
    )
    assert clause is not None
    compiled = str(
        clause.compile(dialect=postgresql.dialect(), compile_kwargs={"literal_binds": False})
    )
    assert " OR " in compiled.upper()
    assert "academic_interests" in compiled.lower()


from __future__ import annotations

from apps.engagement.config import EngagementSettings


def test_post_recognition_milestones_parses_json_array_env_value():
    settings = EngagementSettings(POST_RECOGNITION_MILESTONES="[10,20,50]")
    assert settings.post_recognition_milestones == [10, 20, 50]


def test_post_recognition_milestones_parses_comma_separated_env_value():
    settings = EngagementSettings(POST_RECOGNITION_MILESTONES="10,20,50")
    assert settings.post_recognition_milestones == [10, 20, 50]


def test_post_recognition_milestones_defaults_to_ten():
    settings = EngagementSettings.model_construct(
        comment_max_depth=3,
        post_recognition_milestones=[10],
    )
    assert settings.post_recognition_milestones == [10]

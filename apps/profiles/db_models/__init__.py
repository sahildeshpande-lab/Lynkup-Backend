from .country_db_model import Country
from .academic_interests_db_model import AcademicInterest
from .education_level_db_model import EducationLevel
from .profile_db_model import Profile, CompletenessWeight
from .learning_recommendation_log_db_model import LearningRecommendationLog
from .profile_stats_db_model import ProfileStats
from .learning_recommendation_settings_db_model import LearningRecommendationSettings
from .learning_recommendation_settings_log_db_model import (
    LearningRecommendationSettingsLog,
)
from .university_db_model import University

__all__ = [
    "Country",
    "AcademicInterest",
    "EducationLevel",
    "Profile",
    "CompletenessWeight",
    "LearningRecommendationLog",
    "LearningRecommendationSettings",
    "LearningRecommendationSettingsLog",
    "ProfileStats",
    "University",
]

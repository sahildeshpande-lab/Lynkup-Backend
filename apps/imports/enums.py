from __future__ import annotations

from enum import Enum


class ImportType(str, Enum):
    COUNTRY = "country"
    UNIVERSITY = "university"
    MAJOR = "major"
    MINOR = "minor"
    INTEREST = "interest"
    PROFANITY_WORD = "profanity_word"

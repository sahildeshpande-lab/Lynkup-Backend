from __future__ import annotations

from unittest.mock import MagicMock

import pytest


@pytest.fixture
def db_scalar_result():
    def _build(value):
        return MagicMock(scalar_one_or_none=MagicMock(return_value=value))

    return _build


@pytest.fixture
def db_scalars_result():
    def _build(values):
        scalars = MagicMock(all=MagicMock(return_value=values))
        return MagicMock(scalars=MagicMock(return_value=scalars))

    return _build

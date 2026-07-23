from __future__ import annotations

from pydantic import BaseModel


class StreamTokenData(BaseModel):
    stream_token: str
    expires_in: int

from __future__ import annotations

from pydantic import BaseModel


class UploadData(BaseModel):
    key: str
    url: str


class UploadResponse(BaseModel):
    status: bool
    message: str
    data: UploadData

from __future__ import annotations

from pydantic import BaseModel, Field


class UploadData(BaseModel):
    key: str = Field(..., description="S3 storage key e.g. profiles/123.jpg or banners/1.png")
    url: str = Field(..., description="Full CDN download URL e.g. {S3_CDN_ENDPOINT}/{key}")


class UploadResponse(BaseModel):
    status: bool = Field(default=True)
    message: str = Field(default="image uploaded")
    data: UploadData | None = None

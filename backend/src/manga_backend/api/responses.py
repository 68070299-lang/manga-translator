"""JSON response class backed by orjson."""

from __future__ import annotations

from typing import Any

import orjson
from pydantic import BaseModel
from starlette.responses import Response


class OrjsonResponse(Response):
    """Compact UTF-8 JSON, byte-identical to Starlette's JSONResponse for plain data.

    Pydantic models are dumped in field order, so a schema fixes the key order.
    """

    media_type = "application/json"

    def render(self, content: Any) -> bytes:
        if isinstance(content, BaseModel):
            content = content.model_dump(mode="json")
        return orjson.dumps(content)

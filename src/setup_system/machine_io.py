"""Write one protocol record without exposing diagnostic or input data."""

import sys
from typing import TextIO

from pydantic import BaseModel


def write_model(
    model: BaseModel,
    stream: TextIO | None = None,
    *,
    by_alias: bool = True,
    exclude_unset: bool = True,
) -> None:
    destination = sys.stdout if stream is None else stream
    destination.write(model.model_dump_json(by_alias=by_alias, exclude_unset=exclude_unset) + "\n")

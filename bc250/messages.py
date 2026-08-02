from __future__ import annotations

from dataclasses import dataclass, field
from typing import Mapping


@dataclass(frozen=True, slots=True)
class UserMessage:
    key: str
    params: Mapping[str, object] = field(default_factory=dict)


class MessageError(ValueError):
    def __init__(self, key: str, **params: object) -> None:
        self.message = UserMessage(key, params)
        super().__init__(key)

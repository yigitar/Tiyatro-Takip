from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from zoneinfo import ZoneInfo

ISTANBUL = ZoneInfo("Europe/Istanbul")


class Status(str, Enum):
    SOLD_OUT = "sold_out"
    UPCOMING = "upcoming"
    ON_SALE = "on_sale"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class Session:
    id: str
    start: datetime
    end: datetime | None
    status: Status
    opens_at: datetime | None
    url: str
    venue: str


@dataclass
class Play:
    slug: str
    title: str
    url: str
    genre: str | None
    duration_min: int | None
    age_limit: int | None
    price_from: float | None
    sessions: list[Session] = field(default_factory=list)
    tags: set[str] = field(default_factory=set)


@dataclass
class Rule:
    days: list[int] | None = None
    from_time: str | None = None
    to_time: str | None = None
    overrides: dict[int, dict] = field(default_factory=dict)
    exclude_dates: list[str] = field(default_factory=list)


@dataclass
class Target:
    id: str
    name: str
    people: int
    rule: Rule


@dataclass
class PlayWatch:
    active: bool
    targets: list[Target] = field(default_factory=list)
    tag_overrides: dict[str, bool] = field(default_factory=dict)


@dataclass
class Config:
    plays: dict[str, PlayWatch]
    sources: list[str]
    paused: bool
    version: int


EVENT_KINDS = (
    "NEW_PLAY",
    "REMOVED_PLAY",
    "OPENING_SOON",
    "DAY_BEFORE",
    "ON_SALE",
    "UNKNOWN_STATUS",
    "PC_INACTIVE",
    "PC_DIVERGED",
    "GH_LAGGING",
    "FETCH_FAILING",
    "PLAY_FAILING",
    "NEW_SESSIONS",
    "DELIVERY_DROPPED",
    "OPENED_SOLD_OUT",
)


@dataclass
class Event:
    kind: str
    key: str
    play_slug: str
    session_id: str | None
    target_ids: list[str]
    payload: dict

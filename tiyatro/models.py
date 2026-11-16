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
    remaining: int | None = None  # site badge "Son N Bilet"; display only


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
    summary: str | None = None
    cast: list[str] = field(default_factory=list)
    credits: dict[str, str] = field(default_factory=dict)


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
    portals: list[dict] = field(default_factory=list)
    event_follow: dict[str, bool] = field(default_factory=dict)  # per source event


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
    "CONFIG_INVALID",
    "PORTAL_OPENED",
    "PORTAL_CHANGED",
    "PORTAL_FAILING",
    "SRC_NEW",
    "SRC_OPENS",
    "SRC_DAY_BEFORE",
    "SRC_SOON",
    "SRC_OPENED",
    "SRC_REOPENED",
)


@dataclass
class Event:
    kind: str
    key: str
    play_slug: str
    session_id: str | None
    target_ids: list[str]
    payload: dict

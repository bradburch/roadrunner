from dataclasses import dataclass
from datetime import datetime


@dataclass
class IdDates:
    identifier: str
    start_date: datetime
    end_date: datetime | None = None
    obs: list | None = None

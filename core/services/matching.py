import re
from .timespan import IdDates

BLOCK_START = "<!-- roadrunner -->"
BLOCK_END = "<!-- /roadrunner -->"
_BLOCK_RE = re.compile(re.escape(BLOCK_START) + r".*?" + re.escape(BLOCK_END), re.DOTALL)


def compare(a: IdDates, b: IdDates) -> bool:
    latest_start = max(a.start_date, b.start_date)
    earliest_end = min(a.end_date, b.end_date)
    return earliest_end > latest_start


def add_dict(current: dict, new: dict) -> dict:
    merged = current.copy()
    for k, v in new.items():
        if k in merged:
            if v.isnumeric() and merged[k].isnumeric():
                merged[k] = str(int(v) + int(merged[k]))
            else:
                merged[k] = "X"
        else:
            merged[k] = v
    return merged


def create_bird_description(species_num: dict) -> str:
    return "".join(f"{value} {key}\n" for key, value in species_num.items())


def upsert_block(description: str | None, bird_list: str) -> str:
    description = description or ""
    block = f"{BLOCK_START}\nBirds seen during activity:\n{bird_list.rstrip()}\n{BLOCK_END}"
    if _BLOCK_RE.search(description):
        return _BLOCK_RE.sub(block, description)
    if description.strip():
        return f"{description.rstrip()}\n\n{block}"
    return block

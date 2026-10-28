"""Frozen fixture loader. Scores are hand-specified synthetic segment estimates, not trained."""

import json
from pathlib import Path
from typing import Any, cast


def frozen_records() -> list[dict[str, Any]]:
    return cast(
        list[dict[str, Any]], json.loads(Path(__file__).with_name("prospects.json").read_text())
    )

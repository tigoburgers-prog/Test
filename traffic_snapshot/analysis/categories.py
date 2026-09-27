"""Road-user categories.

Only categories the detector can actually separate are offered. The COCO
classes used by the bundled detector do not distinguish vans from lorries
("truck" covers both), so these are reported together as
"Bestel-/vrachtwagen" instead of inventing a split.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import IntEnum


class Category(IntEnum):
    CAR = 0
    TRUCK = 1
    BUS = 2
    MOTORCYCLE = 3
    BICYCLE = 4
    PEDESTRIAN = 5
    UNKNOWN = 6


@dataclass(frozen=True)
class CategoryInfo:
    label: str
    plural: str
    color: str  # hex, used consistently in overlays, charts and reports
    is_vehicle: bool


CATEGORY_INFO: dict[Category, CategoryInfo] = {
    Category.CAR: CategoryInfo("Personenauto", "Personenauto's", "#3B82F6", True),
    Category.TRUCK: CategoryInfo("Bestel-/vrachtwagen", "Bestel-/vrachtwagens", "#F59E0B", True),
    Category.BUS: CategoryInfo("Bus", "Bussen", "#8B5CF6", True),
    Category.MOTORCYCLE: CategoryInfo("Motor", "Motoren", "#EC4899", True),
    Category.BICYCLE: CategoryInfo("Fiets", "Fietsen", "#10B981", True),
    Category.PEDESTRIAN: CategoryInfo("Voetganger", "Voetgangers", "#64748B", False),
    Category.UNKNOWN: CategoryInfo("Voertuig (type onbekend)", "Voertuigen (type onbekend)", "#94A3B8", True),
}

# COCO class index -> Category
COCO_TO_CATEGORY: dict[int, Category] = {
    0: Category.PEDESTRIAN,
    1: Category.BICYCLE,
    2: Category.CAR,
    3: Category.MOTORCYCLE,
    5: Category.BUS,
    7: Category.TRUCK,
}

VEHICLE_CATEGORIES = [c for c, i in CATEGORY_INFO.items() if i.is_vehicle]


def label(cat: int) -> str:
    return CATEGORY_INFO[Category(cat)].label


def color(cat: int) -> str:
    return CATEGORY_INFO[Category(cat)].color

"""5 个 Stage 的实现。"""

from .cleaner import Cleaner, clean
from .collector import Collector, collect
from .deepen import DeepenStage, deepen
from .extractor import Extractor, extract
from .organizer import Organizer, organize
from .reporter import Reporter, report

__all__ = [
    "Collector",
    "collect",
    "DeepenStage",
    "deepen",
    "Cleaner",
    "clean",
    "Extractor",
    "extract",
    "Organizer",
    "organize",
    "Reporter",
    "report",
]

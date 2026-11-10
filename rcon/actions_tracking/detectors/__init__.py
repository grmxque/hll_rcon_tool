"""Action detectors, run in this order on every poll.

To track a new action, write an ``ActionDetector`` subclass (``base.py``) and add it here.
"""

from rcon.actions_tracking.detectors.base import ActionDetector
from rcon.actions_tracking.detectors.garrison import GarrisonDetector
from rcon.actions_tracking.detectors.node import NodeDetector

DETECTORS: tuple[type[ActionDetector], ...] = (GarrisonDetector, NodeDetector)

__all__ = ["DETECTORS", "ActionDetector", "GarrisonDetector", "NodeDetector"]

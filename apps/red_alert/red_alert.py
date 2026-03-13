"""HACS entry point - imports from the src package."""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.realpath(__file__)), '..', '..', 'src'))

from red_alert.integrations.outputs.homeassistant.app import RedAlert  # noqa: E402, F401

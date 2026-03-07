import re

# Pre-compile regex once
CLEAN_NAME_REGEX = re.compile(r'[\(\)\'"]+')

ICONS_AND_EMOJIS = {
    0: ('mdi:alert', '❗'),
    1: ('mdi:rocket-launch', '🚀'),
    2: ('mdi:home-alert', '⚠️'),
    3: ('mdi:earth-box', '🌍'),
    4: ('mdi:chemical-weapon', '☢️'),
    5: ('mdi:waves', '🌊'),
    6: ('mdi:airplane', '🛩️'),
    7: ('mdi:skull', '💀'),
    8: ('mdi:alert', '❗'),
    9: ('mdi:alert', '❗'),
    10: ('mdi:Home-Alert', '⚠️'),
    11: ('mdi:alert', '❗'),
    12: ('mdi:alert', '❗'),
    13: ('mdi:run-fast', '👹'),
    14: ('mdi:alert', '❗'),
    15: ('mdi:alert-circle-Outline', '⭕'),
}

DAY_NAMES = {
    'Sunday': 'יום ראשון',
    'Monday': 'יום שני',
    'Tuesday': 'יום שלישי',
    'Wednesday': 'יום רביעי',
    'Thursday': 'יום חמישי',
    'Friday': 'יום שישי',
    'Saturday': 'יום שבת',
}

DEFAULT_UNKNOWN_AREA = 'ישראל'

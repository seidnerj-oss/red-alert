import re

# Pre-compile regex once
CLEAN_NAME_REGEX = re.compile(r'[\(\)\'"]+')

# Alert category codes from the Home Front Command API.
#
# === Live endpoint (alerts.json) ===
# Uses 'cat' field with codes 1-14.
#
# IMPORTANT: The live endpoint may send all-clear with cat=10 (the matrix_id)
# instead of cat=13. Title-based detection ("האירוע הסתיים") is the reliable
# fallback - see state.py for classification logic.
#
# Active threat categories:
#   1  - ירי רקטות וטילים (Missile/rocket fire)
#   2  - חדירת כלי טיס עוין (Hostile aircraft intrusion)
#   3  - רעידת אדמה (Earthquake)
#   4  - אירוע רדיולוגי (Radiological event)
#   5  - צונאמי (Tsunami)
#   6  - חדירת כלי טיס עוין (Hostile aircraft intrusion - alternate code)
#   7  - חומרים מסוכנים (Hazardous materials)
#   10 - התרעה מהירה (Flash alert)
#
# Special categories:
#   13 - האירוע הסתיים (All-clear / event ended)
#   14 - בדקות הקרובות צפויות להתקבל התרעות באזורך (Pre-alert / imminent warning)
#
# Drills: category >= 100 (100 + threat code, e.g. 101 = missile drill)
#
# === History endpoint (GetAlarmsHistory.aspx) ===
# Uses 'category' field with a DIFFERENT numbering scheme (1-26).
# Also includes 'matrix_id' which maps to the live category codes above.
# Use HISTORY_CATEGORY_TO_LIVE to convert history categories to live equivalents.
#
#   History  Live(matrix_id)  Description
#   1        1                ירי רקטות וטילים (Missiles)
#   2        2                חדירת כלי טיס עוין (Hostile aircraft)
#   3        3                רעידת אדמה (Earthquake)
#   4        4                אירוע רדיולוגי (Radiological)
#   5        5                צונאמי (Tsunami)
#   6        6                חדירת כלי טיס עוין (Hostile aircraft alt)
#   7        7                חומרים מסוכנים (Hazmat)
#   8        10               התרעה מהירה (Flash alert)
#   9        13               האירוע הסתיים (All-clear)
#   10       14               בדקות הקרובות (Pre-alert)
#   11-26    101+             Drill variants (101=missile drill, etc.)
#
# Related API endpoints:
#   GetDistricts.aspx?lang=he - City list with areaid, areaname, migun_time (shelter time in seconds)
#   GetCities.aspx?lang=he    - City list with areaid, mixname, color
#   GetAlarmsHistory.aspx      - Extended history with category, category_desc, matrix_id, alertDate

# Maps history endpoint 'category' values to equivalent live endpoint 'cat' values.
# History categories use a sequential 1-26 scheme; live uses non-sequential 1-14.
# The history endpoint also provides 'matrix_id' which directly equals the live cat,
# so prefer matrix_id when available. This mapping is a fallback for when matrix_id is absent.
HISTORY_CATEGORY_TO_LIVE: dict[int, int] = {
    1: 1,  # Missiles
    2: 2,  # Hostile aircraft
    3: 3,  # Earthquake
    4: 4,  # Radiological
    5: 5,  # Tsunami
    6: 6,  # Hostile aircraft (alt)
    7: 7,  # Hazmat
    8: 10,  # Flash alert
    9: 13,  # All-clear
    10: 14,  # Pre-alert
    11: 101,  # Missile drill
    12: 102,  # Hostile aircraft drill
    13: 103,  # Earthquake drill
    14: 104,  # Radiological drill
    15: 105,  # Tsunami drill
    16: 106,  # Hostile aircraft drill (alt)
    17: 107,  # Hazmat drill
    18: 110,  # Flash alert drill
    19: 113,  # All-clear drill
    20: 114,  # Pre-alert drill
    21: 101,  # Non-conventional missile drill
    22: 102,  # Non-conventional hostile aircraft drill
    23: 103,  # Non-conventional earthquake drill
    24: 104,  # Non-conventional radiological drill
    25: 105,  # Non-conventional tsunami drill
    26: 107,  # Non-conventional hazmat drill
}

ICONS_AND_EMOJIS = {
    0: ('mdi:alert', '❗'),
    1: ('mdi:rocket-launch', '🚀'),  # Missile/rocket fire
    2: ('mdi:airplane-alert', '🛩️'),  # Hostile aircraft intrusion
    3: ('mdi:earth-box', '🌍'),  # Earthquake
    4: ('mdi:chemical-weapon', '☢️'),  # Radiological event
    5: ('mdi:waves', '🌊'),  # Tsunami
    6: ('mdi:airplane', '🛩️'),  # Hostile aircraft intrusion (alt)
    7: ('mdi:skull', '💀'),  # Hazardous materials
    8: ('mdi:alert', '❗'),
    9: ('mdi:alert', '❗'),
    10: ('mdi:flash-alert', '⚡'),  # Flash alert
    11: ('mdi:alert', '❗'),
    12: ('mdi:alert', '❗'),
    13: ('mdi:check-circle', '✅'),  # All-clear (event ended)
    14: ('mdi:clock-alert', '⏰'),  # Pre-alert (imminent warning)
    15: ('mdi:alert-circle-outline', '⭕'),
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

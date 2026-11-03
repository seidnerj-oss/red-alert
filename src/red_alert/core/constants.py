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
# === History endpoint (www.oref.org.il/api/v1/AlertHistory/heb) ===
# Uses 'category' field with a DIFFERENT numbering scheme: the ids from the site's own
# /alerts/alertCategories.json (1-28). It also sends 'matrixId', but always as 0, so the
# live category is derived from 'category' with HISTORY_CATEGORY_TO_LIVE.
#
# alertCategories.json pairs each id with a matrix_id, but that pairing does not match the
# live codes used here (it gives terrorist infiltration matrix_id 13, our all-clear code), so
# the table below is set by category name. Update (13) and flash (14) carry both pre-alerts
# ("בדקות הקרובות", "עדכון") and all-clears ("האירוע הסתיים"); they map to 0 so the title
# phrase rules in state.py decide. Memorial day sirens (5, 6) are not threats and map to 0.
#
# Related API endpoints:
#   districts/districts_heb.json - City list with areaid, areaname, migun_time (shelter time in seconds)
#   AlertHistory/heb?period=  - Extended history (lastDay, lastWeek or lastMonth)

# Maps history endpoint 'category' values (alertCategories.json ids) to live endpoint 'cat' values.
HISTORY_CATEGORY_TO_LIVE: dict[int, int] = {
    1: 1,  # Missiles
    2: 6,  # Hostile aircraft intrusion
    3: 2,  # Non-conventional
    4: 2,  # Generic alert
    5: 0,  # Memorial day siren 1
    6: 0,  # Memorial day siren 2
    7: 3,  # Earthquake 1
    8: 3,  # Earthquake 2
    9: 4,  # Radiological
    10: 10,  # Terrorist infiltration
    11: 5,  # Tsunami
    12: 7,  # Hazardous materials
    13: 0,  # Update (title decides: pre-alert or all-clear)
    14: 0,  # Flash (title decides: pre-alert or all-clear)
    15: 101,  # Missile drill
    16: 106,  # Hostile aircraft drill
    17: 102,  # Non-conventional drill
    18: 102,  # Alert drill
    19: 102,  # Memorial day drill 1
    20: 102,  # Memorial day drill 2
    21: 103,  # Earthquake drill 1
    22: 103,  # Earthquake drill 2
    23: 104,  # Radiological drill
    24: 113,  # Terrorist infiltration drill
    25: 105,  # Tsunami drill
    26: 107,  # Hazardous materials drill
    27: 110,  # Update drill
    28: 110,  # Flash drill
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

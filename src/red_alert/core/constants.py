import re

# Pre-compile regex once
CLEAN_NAME_REGEX = re.compile(r'[\(\)\'"]+')

# Alert category codes from the Home Front Command API.
#
# Live endpoint (alerts.json) uses 'cat' field with these codes.
# History endpoint (GetAlarmsHistory.aspx) uses 'category' + 'matrix_id' fields.
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
# Related API endpoints:
#   GetDistricts.aspx?lang=he - City list with areaid, areaname, migun_time (shelter time in seconds)
#   GetCities.aspx?lang=he    - City list with areaid, mixname, color
#   GetAlarmsHistory.aspx      - Extended history with category, category_desc, matrix_id, alertDate

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

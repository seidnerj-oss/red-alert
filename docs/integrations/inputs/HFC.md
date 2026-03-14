# Home Front Command (HFC) Web API Integration

red-alert polls the Israeli Home Front Command (Pikud Ha-Oref) website API for live alerts and alert history. This is the primary alert input source.

## How It Works

1. The `HomeFrontCommandApiClient` polls the live alerts endpoint once per second
2. Each response is either empty (no active alert) or a JSON object with alert category, title, and affected cities
3. The `AlertStateTracker` classifies the alert into one of four states: ROUTINE, PRE_ALERT, ALERT, ALL_CLEAR
4. Output integrations (UniFi, Hue, Telegram, etc.) react to state changes

## API Endpoints

The HFC has two domains with different APIs:

- **`www.oref.org.il`** - the main website (Angular app), hosts the live alerts and 24h history endpoints
- **`alerts-history.oref.org.il`** - the alerts history site, hosts the extended history, city data, and district data endpoints

All endpoints require HTTPS and specific headers (see [Required HTTP Headers](#required-http-headers)).

### Endpoints We Currently Use

#### Live Alerts (polled once/second by all output integrations)

```
GET https://www.oref.org.il/WarningMessages/alert/alerts.json
```

Returns the currently active alert as a JSON object, or an empty response (`\r\n`) when no alert is active.

**Response format:**
```json
{
    "id": "1234567890",
    "cat": "1",
    "title": "ירי רקטות וטילים",
    "data": ["כפר סבא", "רעננה", "הוד השרון"],
    "desc": "היכנסו למרחב המוגן"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | string | Alert ID |
| `cat` | string | Alert category code (see [Alert Category Codes](#alert-category-codes)) |
| `title` | string | Alert title in Hebrew |
| `data` | string[] | List of affected city/area names |
| `desc` | string | Instructions text |

**IMPORTANT:** The live endpoint sends brief pulses (~30-60 seconds), not persistent state. Each alert appears for a short window then the endpoint returns empty. This applies to all alert types - pre-alerts, active alerts, and all-clears are all pulses. See [API Pulse Behavior and Hold Timers](#api-pulse-behavior-and-hold-timers).

#### Extended History (primary history source, used on startup)

```
GET https://alerts-history.oref.org.il/Shared/Ajax/GetAlarmsHistory.aspx?lang=he&fromDate=DD.MM.YYYY&toDate=DD.MM.YYYY&mode=0
```

Used by `HomeFrontCommandApiClient.get_alert_history()` as the primary history source. Returns an array of alerts for the specified date range with richer metadata than the 24h endpoint.

**Response format:**
```json
{
    "data": "כפר סבא",
    "date": "14.03.2026",
    "time": "02:30:30",
    "alertDate": "2026-03-14T02:30:00",
    "category": 14,
    "category_desc": "בדקות הקרובות צפויות להתקבל התרעות באזורך",
    "matrix_id": 10,
    "rid": 479300
}
```

Note: `data` is a single city name string (not an array like in the live endpoint). Uses `category_desc` instead of `title`. The `HistoryManager` handles both field names for backward compatibility.

#### District Data (primary city data source, fetched on startup)

```
GET https://alerts-history.oref.org.il/Shared/Ajax/GetDistricts.aspx?lang=he
```

Used by `CityDataManager.load_data()` as the primary city-to-area mapping source. Returns all 1526 cities/districts with the HFC's own area groupings and shelter times (`migun_time`). Supports `lang` parameter: `he`, `en`, `ar`, `ru`.

```json
{
    "label": "כפר סבא",
    "value": "0CEE1E2AF27EAB33A284BA82A3ACF789",
    "id": "840",
    "areaid": 27,
    "areaname": "שרון",
    "label_he": "כפר סבא",
    "migun_time": 90
}
```

| Field | Type | Description |
|-------|------|-------------|
| `label` | string | City name (in requested language) |
| `label_he` | string | City name in Hebrew (always present) |
| `value` | string | City GUID |
| `id` | string | City numeric ID |
| `areaid` | int | Area group ID (see [Alert Areas](#alert-areas)) |
| `areaname` | string | Area group name (in requested language) |
| `migun_time` | int | Time to reach shelter in seconds (0, 15, 30, 45, 60, or 90) |

The `CityDataManager` uses this as primary source, with the static ICBS `city_data.json` file providing only lat/long coordinate overlays for the Home Assistant GeoJSON integration. If the HFC endpoint is unavailable, it falls back to ICBS data only.

The HFC area groupings (33 areas with names like "דן", "חוף הכרמל") differ from the ICBS groupings (31 areas with names like "גוש דן", "הכרמל") - only 20 area names overlap. The HFC groupings are authoritative because they match the live alert system.

**City name coverage (verified against real alert data from March 2026):**

| | GetDistricts | ICBS |
|---|---|---|
| Total unique city names | 1,486 | 1,453 |
| Coverage of live alert city names (688 tested) | **100%** (688/688) | 93.3% (642/688) |
| Overlap between the two sources | 1,330 names in common ||

The 46 alert city names missing from ICBS are farms (חוות), merged alert zones (e.g., "דור, נחשולים"), and newer settlements. GetDistricts is a perfect superset of all city names that appear in live alerts.

**Coordinate gap:** GetDistricts does not include lat/long coordinates. The ICBS file provides coordinates for ~1,330 of 1,486 GetDistricts cities (matched by standardized name). The remaining 156 cities have no coordinates and are skipped by the GeoJSON map feature. See [GitHub issue #3](https://github.com/seidnerj/red-alert/issues/3) for plans to close this gap.

#### 24-Hour History (unreliable, used as fallback only)

```
GET https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json
```

Fallback for `get_alert_history()` when the extended endpoint is unavailable.

**Response format** (array of entries, one per city per alert):
```json
[
    {
        "alertDate": "2024-01-15 10:30:45",
        "title": "ירי רקטות וטילים",
        "data": "תל אביב - מרכז העיר"
    }
]
```

Note: `data` is a single city name string (not an array like in the live endpoint).

**Known issue:** This endpoint is unreliable - it returns `\r\n` (empty) even when alerts occurred in the past 24 hours. We observed this on March 14, 2026: the endpoint returned empty at 04:25 UTC despite alerts occurring between 02:21-05:16. The extended history endpoint returned all 1614 entries for the same period. The 24h endpoint appears to have a shorter effective window or different caching behavior than its name suggests.

### Endpoints We Don't Currently Use (But Could)

#### City Data

```
GET https://alerts-history.oref.org.il/Shared/Ajax/GetCities.aspx?lang=he
```

Similar city list with `areaid`, `mixname` (HTML-formatted label with area in `<span>` tag), and `color` fields. Less useful than GetDistricts since it lacks `migun_time` and `areaname`.

## Alert Category Codes

Categories observed in real production data (March 2026):

| Code | Hebrew Title | English | State |
|------|-------------|---------|-------|
| 1 | ירי רקטות וטילים | Missile/rocket fire | ALERT |
| 2 | חדירת כלי טיס עוין | Hostile aircraft intrusion | ALERT |
| 3 | רעידת אדמה | Earthquake | ALERT |
| 4 | אירוע רדיולוגי | Radiological event | ALERT |
| 5 | צונאמי | Tsunami | ALERT |
| 6 | חדירת כלי טיס עוין | Hostile aircraft intrusion (alt) | ALERT |
| 7 | חומרים מסוכנים | Hazardous materials | ALERT |
| 10 | התרעה מהירה | Flash alert | ALERT |
| 13 | האירוע הסתיים | All-clear (event ended) | ALL_CLEAR |
| 14 | בדקות הקרובות צפויות להתקבל התרעות באזורך | Pre-alert (imminent warning) | PRE_ALERT |
| 100+ | (drill variants) | Drill (100 + threat code) | ROUTINE |

### Category vs matrix_id Discrepancy

The extended history endpoint uses separate `category` and `matrix_id` fields. All-clear entries consistently show `category=13, matrix_id=10`. However, the live alerts endpoint may send all-clear with `cat=10` (the matrix_id value) instead of `cat=13`.

To handle this, `AlertStateTracker` checks the alert title for Hebrew all-clear phrases ("האירוע הסתיים") **before** checking the category code. This title-based detection is the reliable fallback. See `state.py` for the classification priority logic.

Similarly, pre-alerts are detected by both category 14 and Hebrew title phrases ("בדקות הקרובות", "עדכון", "שהייה בסמיכות למרחב מוגן").

## Alert Areas

33 geographic areas (from the GetDistricts endpoint), each containing multiple cities:

| areaid | Area Name |
|--------|-----------|
| 1 | אילת (Eilat) |
| 2 | גולן (Golan) |
| 3 | בקעה (Bik'a) |
| 4 | בקעת בית שאן (Beit She'an Valley) |
| 5 | בית שמש (Beit Shemesh) |
| 6 | גליל עליון (Upper Galilee) |
| 7 | גליל תחתון (Lower Galilee) |
| 8 | דן (Dan) |
| 9 | דרום הנגב (Southern Negev) |
| 10 | שומרון (Shomron) |
| 11 | חוף הכרמל (Carmel Coast) |
| 12 | השפלה (Shfela) |
| 13 | ואדי ערה (Wadi Ara) |
| 14 | דרום השפלה (Southern Shfela) |
| 15 | מנשה (Menashe) |
| 16 | מערב הנגב (Western Negev) |
| 17 | יהודה (Yehuda) |
| 18 | ים המלח (Dead Sea) |
| 19 | מערב לכיש (Western Lachish) |
| 20 | ירושלים (Jerusalem) |
| 21 | ירקון (Yarkon) |
| 22 | לכיש (Lachish) |
| 23 | חפר (Hefer) |
| 24 | יערות הכרמל (Carmel Forests) |
| 25 | מרכז הנגב (Central Negev) |
| 26 | עוטף עזה (Gaza Envelope) |
| 27 | שרון (Sharon) |
| 29 | ערבה (Arava) |
| 30 | קו העימות (Confrontation Line) |
| 34 | חיפה (Haifa) |
| 35 | קצרין (Katzrin) |
| 36 | קריות (Krayot) |
| 37 | תבור (Tavor) |

## Shelter Times (migun_time)

Fetched from the GetDistricts endpoint on startup and stored per-city in the `CityDataManager`. The `migun_time` field indicates how many seconds residents have to reach shelter after an alert sounds, based on proximity to threat sources:

| Seconds | Description | Example Areas |
|---------|-------------|---------------|
| 0 | Immediate | Golan border communities |
| 15 | Very close | Golan interior |
| 30 | Close | Eilat, some border areas |
| 45 | Moderate | Southern Shfela |
| 60 | Standard | Beit Shemesh, some Galilee |
| 90 | Extended | Central Israel (Sharon, Jerusalem, etc.) |

This data is available to output integrations via `CityDataManager.get_city_details(city)['migun_time']` and could be used for shelter time countdowns or location-based hold timer adjustments.

## API Pulse Behavior and Hold Timers

The live alerts endpoint does NOT maintain persistent state. Instead, each alert phase is a brief pulse:

**Real-world timeline observed (March 14, 2026):**

```
02:21:53  PRE_ALERT  (cat=14, 504 cities - northern Israel)
02:25-02:35  ALERT    (cat=1, rockets - multiple salvos, 119+8+6+7+94+14 cities)
02:30:30  PRE_ALERT  (cat=14, 165 cities - central Israel including כפר סבא)
02:45:57  ALL_CLEAR  (cat=13, 669 cities)
03:45:59  ALERT      (cat=1, מטולה - rocket fire)
03:56:05  ALL_CLEAR  (cat=13, מטולה)
04:22:19  ALERT      (cat=1, מטולה + כפר גלעדי + כפר יובל)
04:32:00  ALL_CLEAR  (cat=13, מטולה + כפר יובל)
```

Key observations:
- **Pre-alert to all-clear gap: ~15 minutes** (כפר סבא: 02:30 to 02:45)
- **Alert to all-clear gap: ~10 minutes** (מטולה: 03:45 to 03:56, 04:22 to 04:32)
- **Multiple salvos**: A city can get alert -> all-clear -> alert -> all-clear in rapid succession
- **Each pulse lasts ~30-60 seconds on the live endpoint**, then the API returns empty

To bridge these gaps, `AlertStateTracker` uses configurable hold timers:

| State | Default Hold | Rationale |
|-------|-------------|-----------|
| ALERT | 1800s (30 min) | Bridges gap until all-clear arrives (~10-15 min observed) |
| PRE_ALERT | 1800s (30 min) | Bridges gap until all-clear arrives (~15 min observed) |
| ALL_CLEAR | 300s (5 min) | Terminal state, brief display before returning to ROUTINE |

Hold timers reset each time the API confirms the same state is still active. A transition to a different state (e.g., all-clear arriving during alert hold) happens immediately.

## Required HTTP Headers

The API requires specific headers to return data:

```python
{
    'Referer': 'https://www.oref.org.il/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json',
}
```

The `Referer` and `X-Requested-With` headers are mandatory. Without them, the API may return empty or blocked responses.

For the `alerts-history.oref.org.il` endpoints, use `Referer: https://alerts-history.oref.org.il/` instead.

## Encoding

API responses may include a UTF-8 BOM (Byte Order Mark). The client decodes with `utf-8-sig` first, falling back to `utf-8`, then strips any remaining BOM via `check_bom()`.

## Comparison with Cell Broadcast (CBS)

| | Home Front Command API | Cell Broadcast (CBS) |
|---|---|---|
| **Transport** | Internet (HTTPS polling) | Cellular network (push) |
| **Latency** | Polling interval + API delay | Near-instant (network push) |
| **Internet required** | Yes | No |
| **Location granularity** | Per-city (via `data` field) | Per-cell tower coverage area |
| **Message content** | JSON with category, cities, instructions | Multi-language text (HE/EN/AR/RU) |
| **Hardware** | Any device with internet | QMI-capable LTE modem |
| **Category data** | Structured (cat code + title) | Message ID mapping |

The two sources complement each other. The API provides structured data with city-level granularity; CBS provides faster, internet-independent delivery. See [CBS integration docs](CBS.md) for details.

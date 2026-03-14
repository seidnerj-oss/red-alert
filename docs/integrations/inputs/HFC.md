# Home Front Command (HFC) Web API Integration

red-alert polls the Israeli Home Front Command (Pikud Ha-Oref) website API for live alerts and alert history. This is the primary alert input source.

## How It Works

1. The `HomeFrontCommandApiClient` polls the live alerts endpoint once per second
2. Each response is either empty (no active alert) or a JSON object with alert category, title, and affected cities
3. The `AlertStateTracker` classifies the alert into one of four states: ROUTINE, PRE_ALERT, ALERT, ALL_CLEAR
4. Output integrations (UniFi, Hue, Telegram, etc.) react to state changes

## API Endpoints

The HFC uses three domains:

- **`www.oref.org.il`** - the main website (Angular app), hosts the live alerts, 24h history, and metadata endpoints (categories, translations, display config)
- **`alerts-history.oref.org.il`** - the alerts history site, hosts the extended history, district data, and static assets (alarm sound)
- **`api.oref.org.il`** - the API gateway, hosts global configuration

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

### Metadata & Configuration Endpoints

These endpoints provide supplementary data about alert types, translations, and display configuration. They are served from `www.oref.org.il` and `api.oref.org.il`.

**NOTE:** The `www.oref.org.il/alerts/*.json` endpoints have been observed returning 404 outside of active alert periods. They may only be served when the HFC website is actively displaying alerts. The API wrappers handle this gracefully (returning None).

#### Alert Categories (category definitions)

```
GET https://www.oref.org.il/alerts/alertCategories.json
```

Maps category IDs to English category slugs, matrix IDs, and display priorities. This is the authoritative source for understanding the relationship between `cat` (from live alerts), `category` (English slug), `matrix_id` (used in history), and display `priority` (higher = more urgent in website display). Covers all 28 real and drill alert types.

**Response format:**
```json
[
    {"id": 1, "category": "missilealert", "matrix_id": 1, "priority": 120, "queue": false},
    {"id": 2, "category": "uav", "matrix_id": 6, "priority": 130, "queue": false},
    {"id": 9, "category": "cbrne", "matrix_id": 4, "priority": 170, "queue": false},
    {"id": 10, "category": "terrorattack", "matrix_id": 13, "priority": 160, "queue": false},
    {"id": 13, "category": "update", "matrix_id": 10, "priority": 0, "queue": false},
    {"id": 14, "category": "flash", "matrix_id": 10, "priority": 0, "queue": false},
    {"id": 15, "category": "missilealertdrill", "matrix_id": 101, "priority": 40, "queue": false}
]
```

| Field | Type | Description |
|-------|------|-------------|
| `id` | int | Category ID (matches `cat` in live alerts) |
| `category` | string | English category slug (e.g., `missilealert`, `uav`, `cbrne`) |
| `matrix_id` | int | Matrix ID (used in extended history `category`/`matrix_id` fields) |
| `priority` | int | Display priority (higher = more urgent; 0 = no priority, e.g., drills) |
| `queue` | bool | Whether alerts of this type are queued (always false in observed data) |

**Full category mapping (28 entries):**

| id | category | matrix_id | priority | Description |
|----|----------|-----------|----------|-------------|
| 1 | missilealert | 1 | 120 | Missile/rocket fire |
| 2 | uav | 6 | 130 | Hostile aircraft intrusion |
| 3 | nonconventional | 2 | 180 | Non-conventional (highest priority) |
| 4 | warning | 8 | 140 | General warning |
| 5 | memorialday1 | 2 | 0 | Memorial day siren 1 |
| 6 | memorialday2 | 2 | 0 | Memorial day siren 2 |
| 7 | earthquakealert1 | 3 | 90 | Earthquake (preliminary) |
| 8 | earthquakealert2 | 3 | 110 | Earthquake (confirmed) |
| 9 | cbrne | 4 | 170 | Chemical/biological/radiological/nuclear |
| 10 | terrorattack | 13 | 160 | Terrorist infiltration |
| 11 | tsunami | 5 | 100 | Tsunami |
| 12 | hazmat | 7 | 150 | Hazardous materials |
| 13 | update | 10 | 0 | Update/all-clear |
| 14 | flash | 10 | 0 | Flash alert (pre-alert, stay in shelter, etc.) |
| 15-28 | *drill | 101-113 | 10-80 | Drill variants (100 + threat matrix_id) |

#### Alert Translations (multi-language alert text)

```
GET https://www.oref.org.il/alerts/alertsTranslation.json
```

4-language translations (Hebrew, English, Russian, Arabic) for all alert types. Each entry provides translated titles, instruction text, and is keyed by `catId`, `matrixCatId`, and `updateType`. Contains 66 entries covering all alert types, drills, and sub-types (e.g., different phases of terrorist infiltration or hazmat events).

**Response format:**
```json
[
    {
        "heb": "זמן ההגעה למרחב המוגן {0} {1}, היכנסו למרחב המוגן",
        "eng": "Time of arrival in the protected space {0} {1}, Enter the Protected Space",
        "rus": "Время входа в защищённое пространство {0} {1}, Вход в убежище",
        "arb": "المدة المتاحة للوصول الى المكان المحمي {0} {1}, ادخلوا فورا الى الحيّز المحمي",
        "catId": 1,
        "matrixCatId": 1,
        "hebTitle": "ירי רקטות וטילים",
        "engTitle": "Rocket and Missile Attack",
        "rusTitle": "Ракетный обстрел",
        "arbTitle": "إطلاق قذائف وصواريخ",
        "updateType": "-"
    }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `heb` | string | Hebrew instruction text (`{0}` and `{1}` are shelter time placeholders) |
| `eng` | string | English instruction text |
| `rus` | string | Russian instruction text |
| `arb` | string | Arabic instruction text |
| `catId` | int | Category ID (from alertCategories) |
| `matrixCatId` | int | Matrix category ID |
| `hebTitle` / `engTitle` / `rusTitle` / `arbTitle` | string | Alert title in each language |
| `updateType` | string | Sub-type identifier (`-` for primary alerts, numeric for sub-types) |

This endpoint is useful for implementing multi-language alert display without hardcoding translations.

#### Alert Display Config (website/app display settings)

```
GET https://www.oref.org.il/alerts/RemainderConfig_heb.json
```

Display configuration used by the HFC website and mobile app. Each entry defines the Hebrew title, shelter instructions, TTL (time-to-live in minutes for the alert display), and links to life-saving guidelines for a specific alert type/sub-type. Contains 37 entries covering all alert variations including sub-phases (e.g., "enter shelter", "stay in shelter", "you may leave shelter").

**Response format:**
```json
[
    {
        "title": "ירי רקטות וטילים",
        "cat": "missilealert",
        "instructions": "היכנסו למרחב המוגן",
        "eventManagementLink": null,
        "lifeSavingGuidelinesLink": {
            "target": "_self",
            "name": "ירי רקטות וטילים מעודכן",
            "url": "https://www.oref.org.il/heb/life-saving-guidelines/rocket-and-missile-attacks/",
            "Type": "Content"
        },
        "ttlInMinutes": 10,
        "updateType": "0"
    }
]
```

| Field | Type | Description |
|-------|------|-------------|
| `title` | string | Hebrew display title for this alert sub-type |
| `cat` | string | Category slug (from alertCategories: `missilealert`, `uav`, `update`, `flash`, etc.) |
| `instructions` | string | Hebrew shelter/safety instructions |
| `eventManagementLink` | object/null | Link to event management page |
| `lifeSavingGuidelinesLink` | object/null | Link to life-saving guidelines page |
| `ttlInMinutes` | int | How long to display the alert (usually 10 minutes, 6 for pre-alerts) |
| `updateType` | string | Sub-type identifier matching alertsTranslation (0=primary, 1-39=sub-types) |

The `updateType` field distinguishes sub-phases within a category. For example, terrorist infiltration (`terrorattack`) has:
- `updateType=0`: Initial alert ("enter shelter")
- `updateType=11`: Threat removed
- `updateType=12`: Flash - "do not leave shelter"
- `updateType=13`: Event ended - "you may leave"

#### Global Config (site-wide settings)

```
GET https://api.oref.org.il/api/v1/global
```

Global configuration for the HFC website. The `alertsTimeout` field indicates the recommended polling interval in seconds (currently 10). This is served from a different domain (`api.oref.org.il`) than the other endpoints.

**Response format:**
```json
{
    "alertsTimeout": 10,
    "isSettlementStatusNeeded": false,
    "feedbackForm": {
        "articles": true,
        "guidelines": true,
        "contactUs": false,
        "emergencies": true,
        "eventsManagement": true,
        "questionsAnswers": true,
        "updatesNewsflashes": true,
        "recommendations": true,
        "alertHistory": true
    },
    "defaultOgImage": "/media/kvpohwhe/8480-1.jpg"
}
```

| Field | Type | Description |
|-------|------|-------------|
| `alertsTimeout` | int | Recommended polling interval in seconds (10) |
| `isSettlementStatusNeeded` | bool | Whether to show settlement status |
| `feedbackForm` | object | Feature flags for the website feedback form |
| `defaultOgImage` | string | Default Open Graph image path |

Note: Our integrations poll every 1 second (not 10) because we prioritize alert latency over bandwidth.

### Endpoints We Don't Currently Use

#### City Data

```
GET https://alerts-history.oref.org.il/Shared/Ajax/GetCities.aspx?lang=he
```

Similar city list with `areaid`, `mixname` (HTML-formatted label with area in `<span>` tag), and `color` fields. Less useful than GetDistricts since it lacks `migun_time` and `areaname`.

#### Leftovers (purpose unclear)

```
GET https://alerts-history.oref.org.il/WarningMessages/Leftovers/HE.Leftovers.json
```

The HFC website polls this endpoint every 5 seconds. In our captures it returned "Not found". The purpose is unclear - it may be a legacy endpoint for leftover/trailing alerts or a feature that is no longer active.

#### Alarm Sound (static audio file)

```
https://alerts-history.oref.org.il/Style/Shared/alarmSound.mp4
```

The official HFC alarm siren sound, served as an MP4 audio file. Used by the website and mobile app for audible alerts. This could be used by audio output integrations (e.g., HomePod) as an alternative to custom alert sounds.

## Alert Category Codes

Categories observed in real production data (March 2026). See also the [Alert Categories endpoint](#alert-categories-category-definitions) for the authoritative machine-readable mapping with priorities and matrix IDs.

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

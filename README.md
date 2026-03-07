# RedAlert
***Not Official - Israeli Home Front Command***

> Real-time Israeli Home Front Command alert monitoring library with Home Assistant and Homebridge integrations

RedAlert is a Python library that connects to the official Israeli Home Front Command (Pikud HaOref) API to fetch real-time alerts. The core library is framework-agnostic and can be integrated into any platform. Currently supported integrations: **Home Assistant** (AppDaemon), **Homebridge** (HTTP contact sensor), and **UniFi** (AP LED color control).

The library monitors all alert types issued by the Home Front Command, including:
- Missile and rocket fire
- Hostile aircraft intrusion
- Earthquakes
- Tsunami warnings
- Terrorist infiltration
- Hazardous materials incidents
- Radiological events
- General alerts and notifications

Upon receiving an alert, the specific threat type is indicated (e.g., "Rocket and missile fire", "Hostile aircraft intrusion").

---

## Architecture

RedAlert is designed as a **multi-consumer library**:

```
src/red_alert/
  core/                    # Framework-agnostic core (no HA dependency)
    api_client.py          # HomeFrontCommandApiClient
    alert_processor.py     # Alert data processing
    city_data.py           # CityDataManager (ICBS geographic data)
    constants.py           # Icons, emojis, defaults
    history.py             # Alert history management
    i18n.py                # Internationalization (en/he)
    state.py               # AlertState enum + AlertStateTracker
    utils.py               # Shared utilities
  integrations/
    homeassistant/         # Home Assistant AppDaemon integration
      app.py               # RedAlert(Hass) main class
      file_manager.py      # CSV, TXT, JSON file management
      geojson.py           # GeoJSON map data generation
    homebridge/            # Homebridge HTTP server integration
      server.py            # AlertMonitor + HTTP endpoints
      __main__.py          # CLI entry point
    unifi/                 # UniFi AP LED control integration
      led_controller.py    # SSH LED color control via asyncssh
      server.py            # UnifiAlertMonitor + poll loop
      __main__.py          # CLI entry point
```

The **core** package has zero Home Assistant dependencies and can be used by any Python application. Integrations import from core and adapt it to their specific platform.

---

## Key Features

*   **Polls** the official Israeli Home Front Command API every few seconds for all alert types.
*   **Multi-language support**: English (default) and Hebrew via standard Python gettext i18n.
*   **Framework-agnostic core**: Use the alert monitoring library in any Python project.

### Homebridge Integration

*   **Runs** as a standalone HTTP server exposing alert state for Homebridge HTTP plugins.
*   **Exposes** `/contact` (all alerts), `/city` (filtered by configured cities), and `/state` (routine/pre_alert/alert) endpoints.
*   **Works** with `homebridge-http-contact-sensor` to create HomeKit contact sensor accessories.

See [Homebridge setup guide](docs/integrations/homebridge.md) for full instructions.

### UniFi LED Integration

*   **Controls** the RGB LED bar on UniFi U6/U7 access points based on alert state.
*   **Three states**: white/green (routine), yellow (pre-alert), red (active alert).
*   **Supports** area-of-interest filtering - only react to alerts in your configured cities.
*   **Connects** via SSH using asyncssh for non-blocking parallel updates to all APs.

See [UniFi setup guide](docs/integrations/unifi.md) for full instructions.

### Home Assistant Integration

*   **Creates** dedicated Home Assistant entities: main binary sensors, a text helper, a test boolean, and three detailed history sensors.
*   **Offers** flexible alert notification publishing via **MQTT** and native **Home Assistant events** triggered by new alert payloads.
*   **Saves** alert history (TXT, CSV) and the last active state (JSON) for persistence across restarts (optional).
*   **Generates** GeoJSON files for visualizing active and historical alert locations on the Home Assistant map (optional).
*   **Provides** specific binary sensors to indicate if an alert affects *your configured cities* or if it's a special "Pre-Alert" type.

---

## Installation (Home Assistant)

### HACS Download
1. In Home Assistant: Navigate to `HACS > Automation`
   * If this option is not available, go to `Settings > Integrations > HACS > Configure` and enable `AppDaemon apps discovery & tracking`. After enabling, return to the main HACS screen and select `Automation`
2. Navigate to the `Custom Repositories` page and add the following repository as `Appdaemon`: `https://github.com/idodov/RedAlert/`
3. Return to the `HACS Automation` screen, search for `Red Alert`, click on `Download`

### Manual Download
1. Download the contents of `apps/red_alert/` and `src/red_alert/` from this repository.
2. Place them inside the `appdaemon/apps` directory, preserving the directory structure.

### Configuration

In the `/appdaemon/apps/apps.yaml` file, add the following code. **Make sure to replace the city_names values as the Home Front Command defines them:**

```yaml
# /appdaemon/apps/apps.yaml
red_alert:
  module: red_alert
  class: RedAlert
  interval: 1
  timer: 120
  sensor_name: "red_alert"
  language: "en"            # "en" (default) or "he"
  save_to_file: True
  hours_to_show: 4
  mqtt: False
  event: True
  city_names:
    - "tel aviv - city center"
    - "kisufim"
```

| Parameter | Description | Example |
|---|---|---|
| `interval` | Polling interval in seconds (official site uses 1s) | `1` |
| `timer` | Duration (seconds) for which the sensor remains on after an alert | `120` |
| `sensor_name` | Name of the primary binary sensor (`binary_sensor.<name>`) | `red_alert` |
| `language` | Language for user-facing strings: `en` (default) or `he` | `en` |
| `save_to_file` | Save alert history to TXT/CSV files | `True` |
| `hours_to_show` | Number of hours of history to display | `4` |
| `mqtt` | MQTT topic to publish alerts (or `False` to disable) | `False` |
| `event` | Fire Home Assistant events on new alerts | `True` |
| `city_names` | Cities that activate the city-specific binary sensor | see [CITIES.md](docs/CITIES.md) |

---

## Entities Created

Upon restarting the AppDaemon add-on, Home Assistant will create several entities:

* **`binary_sensor.red_alert`** - Main sensor. ON when there's an active alert of any type in Israel, OFF otherwise. Includes attributes: category, ID, title, data, description, alert count, emojis, and more.
* **`binary_sensor.red_alert_city`** - Activates only if a configured city is included in the alert.
* **`input_text.red_alert`** - Records alert data for logbook display (255 char limit).
* **`input_boolean.red_alert_test`** - Toggle to send test data to the sensor.
* **`sensor.red_alert_history_cities`** - Cities from recent alerts.
* **`sensor.red_alert_history_list`** - Detailed list of recent alerts.
* **`sensor.red_alert_history_group`** - Alerts grouped by type and area.

---

## Documentation

- [Installation Overview](docs/INSTALL.md)
- [Home Assistant Integration](docs/integrations/homeassistant.md)
- [Homebridge Integration](docs/integrations/homebridge.md)
- [UniFi LED Integration](docs/integrations/unifi.md)
- [English Documentation](docs/ENGLISH.md)
- [Hebrew Documentation](docs/HEBREW.md)
- [City Names Reference](docs/CITIES.md)

---

## License

This project is licensed under the MIT License - see the [LICENSE](LICENSE) file for details.

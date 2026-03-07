# Installation Instructions

## Prerequisites

1. Install the **AppDaemon** addon in Home Assistant by going to Settings > Add-ons > Add-on store and search for **AppDaemon**.
2. Once AppDaemon is installed, enable the **Auto-Start** and **Watchdog** options.
3. **Start** the add-on
4. In file editor open **\addon_configs\appdaemon\appdaemon.yaml** and make the changes under *appdaemon* section as described:

> [!IMPORTANT]
> You can locate your own coordinates (latitude & longitude) here: https://www.latlong.net/
> *  `latitude: 31.9837528`
> *  `longitude: 34.7359077`
> *  `time_zone: Asia/Jerusalem`.
> *   If you install this script via HACS - **Specify the apps directory in `app_dir: /homeassistant/appdaemon/apps/`.**
>     * Also, remember to **transfer** all files from `/addon_configs/a0d7b954_appdaemon/apps/` to `/homeassistant/appdaemon/apps/`.
>   ```yaml
>     #/addon_configs/a0d7b954_appdaemon/appdaemon.yaml
>     ---
>     secrets: /homeassistant/secrets.yaml
>     appdaemon:
>         app_dir: /homeassistant/appdaemon/apps/ # If you install this script via HACS
>         latitude: 31.9837528
>         longitude: 34.7359077
>         elevation: 2
>         time_zone: Asia/Jerusalem
>         plugins:
>           HASS:
>             type: hass
>     http:
>         url: http://127.0.0.1:5050
>     admin:
>     api:
>     hadashboard:

## Manual Download
1. Download the `apps/red_alert/` and `src/red_alert/` directories from [this repository](https://github.com/idodov/RedAlert).
2. Place them inside the `appdaemon/apps` directory preserving the structure, then proceed to the final step.

## HACS Download
1. In Home Assistant: Navigate to `HACS > Automation`
   * If this option is not available, go to `Settings > Integrations > HACS > Configure` and enable `AppDaemon apps discovery & tracking`. After enabling, return to the main HACS screen and select `Automation`
2. Navigate to the `Custom Repositories` page and add the following repository as `Appdaemon`: `https://github.com/idodov/RedAlert/`
3. Return to the `HACS Automation` screen, search for `Red Alert`, click on `Download` and proceed to the final step

## Final Step
In the `/appdaemon/apps/apps.yaml` file, add the following code. **Make sure to replace the city_names values as the Home Front Command defines them and save the file:**

```yaml
#/appdaemon/apps/apps.yaml
red_alert:
  module: red_alert
  class: RedAlert
  interval: 1
  timer: 120
  sensor_name: "red_alert"
  language: "en"
  save_to_file: True
  city_names:
    - "tel aviv - city center"
    - "kisufim"
```

| Parameter | Description | Example |
|---|---|---|
| `interval` | The interval in seconds at which the script polls for alerts (official site uses 1s) | `1` |
| `timer` | The duration, in seconds, for which the sensor remains on after an alert | `120` |
| `sensor_name` | The name of the primary binary sensor in Home Assistant (`binary_sensor.#sensor_name#`) | `red_alert` |
| `language` | Language for user-facing strings: `en` (default) or `he` | `en` |
| `save_to_file` | An option to save the alerts information to files | `True` |
| `city_names` | The names of the cities that activate the city-specific binary sensor (`binary_sensor.#sensor_name#_city`). You can add as many cities as you want | see [CITIES.md](CITIES.md) |

## You Are All Set!
Upon restarting the AppDaemon add-on, Home Assistant will create several entities:

* The primary entity, `binary_sensor.red_alert`, activates when there's any active alert in Israel (missile fire, hostile aircraft intrusion, earthquake, etc.) and deactivates otherwise. This sensor also includes various attributes such as category, ID, title, data, description, the count of active alerts, and emojis.
* The city entity, `binary_sensor.red_alert_city`, stores Home Front Command data and only activates if the defined city is included in the alert cities.
* The text entity, `input_text.red_alert`, is mainly for recording historical alert data on the logbook screen. Note that Home Assistant has a character limit of 255 for text entities.
* The test entity, `input_boolean.red_alert_test`, when toggled on, sends test data to the sensor, which activates it for the period you defined in the `timer` value.
* History sensors (`sensor.red_alert_history_*`) provide detailed alert history data.

![red-alerts-sensors](https://github.com/idodov/RedAlert/assets/19820046/e0e779fc-ed92-4f4e-8e36-4116324cd089)

> [!TIP]
> To ensure the history of sensors is maintained after a restart in Home Assistant, it's advisable to establish input text and boolean helpers. It's best to do this prior to installation. Here's how you can proceed:
> 1. Open `configuration.yaml`.
> 2. Add this lines and restart Home Assistant:
> ```yaml
> #/homeassistant/configuration.yaml
> input_text:
>   red_alert:
>     name: Last Alert in Israel
>     min: 0
>     max: 255
>
> input_boolean:
>   red_alert_test:
>     name: Test Alert
>     icon: mdi:alert-circle
> ```

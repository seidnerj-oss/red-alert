# Home Assistant Integration (AppDaemon)

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
1. Download the `apps/red_alert/` and `src/red_alert/` directories from [this repository](https://github.com/seidnerj/red-alert).
2. Place them inside the `appdaemon/apps` directory preserving the structure, then proceed to the final step.

## HACS Download
1. In Home Assistant: Navigate to `HACS > Automation`
   * If this option is not available, go to `Settings > Integrations > HACS > Configure` and enable `AppDaemon apps discovery & tracking`. After enabling, return to the main HACS screen and select `Automation`
2. Navigate to the `Custom Repositories` page and add the following repository as `Appdaemon`: `https://github.com/seidnerj/red-alert/`
3. Return to the `HACS Automation` screen, search for `Red Alert`, click on `Download` and proceed to the final step

## Final Step
In the `/appdaemon/apps/apps.yaml` file, add the following code. **Make sure to replace the areas_of_interest values as the Home Front Command defines them and save the file:**

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
  areas_of_interest:
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
| `areas_of_interest` | The names of the areas/cities that activate the city-specific binary sensor (`binary_sensor.#sensor_name#_city`). You can add as many areas as you want | see [CITIES.md](../CITIES.md) |

## You Are All Set!
Upon restarting the AppDaemon add-on, Home Assistant will create several entities:

* The primary entity, `binary_sensor.red_alert`, activates when there's any active alert in Israel (missile fire, hostile aircraft intrusion, earthquake, etc.) and deactivates otherwise. This sensor also includes various attributes such as category, ID, title, data, description, the count of active alerts, and emojis.
* The city entity, `binary_sensor.red_alert_city`, stores Home Front Command data and only activates if the defined city is included in the alert cities.
* The text entity, `input_text.red_alert`, is mainly for recording historical alert data on the logbook screen. Note that Home Assistant has a character limit of 255 for text entities.
* The test entity, `input_boolean.red_alert_test`, when toggled on, sends test data to the sensor, which activates it for the period you defined in the `timer` value.
* History sensors (`sensor.red_alert_history_*`) provide detailed alert history data.

![red-alerts-sensors](https://github.com/seidnerj/red-alert/assets/19820046/e0e779fc-ed92-4f4e-8e36-4116324cd089)

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

## Sensor Attributes

You can use any attribute from the sensor. For example, to show the title on a Lovelace card:

```
{{ state_attr('binary_sensor.red_alert', 'title') }}
```

| Attribute | Description | Example |
|-----------|-------------|---------|
| `cat` | Category number (1-13) | `1` |
| `title` | Alert type text | Rocket and missile fire |
| `data` | List of cities | tel aviv - city center |
| `areas` | List of metropolitan areas | Gush Dan |
| `desc` | Instructions (e.g., enter safe room) | Enter protected space and remain for 10 minutes |
| `duration` | Seconds to stay in safe room | `600` |
| `id` | Alert ID from Home Front Command | `133413399870000000` |
| `data_count` | Number of cities under alert | `1` |
| `emoji` | Emoji for alert type | `🚀` |
| `prev_*` | Previous alert values | Stored from the most recent alert |

## City and Area Filtering

### Specific City

To trigger only for an exact city name (e.g., "Yavne" but not "Gan Yavne"), use `split`:

```
{{ "yavne" in state_attr('binary_sensor.red_alert', 'data').split(', ') }}
```

### Multiple Cities

```
{{ "irous" in state_attr('binary_sensor.red_alert', 'data').split(', ')
 or "beit hanan" in state_attr('binary_sensor.red_alert', 'data').split(', ')
 or "gan sorek" in state_attr('binary_sensor.red_alert', 'data').split(', ') }}
```

### Cities with Multiple Alert Zones

11 cities in Israel are divided into multiple alert zones (Ashkelon, Beersheba, Ashdod, Herzliya, Hadera, Haifa, Jerusalem, Netanya, Rishon Lezion, Ramat Gan, Tel Aviv-Yafo). To match any zone within the city, use `regex_search`:

```
{{ state_attr('binary_sensor.red_alert', 'data') | regex_search("tel aviv") }}
```

To match a specific zone, use `split`:

```
{{ "tel aviv - city center" in state_attr('binary_sensor.red_alert', 'data').split(', ') }}
```

### Metropolitan Areas

Israel is segmented into 30 metropolitan areas. You can filter by area using the `areas` attribute:

```
{{ "gush dan" in state_attr('binary_sensor.red_alert', 'areas').split(', ') }}
```

### Alert Type

The `cat` attribute defines the alert type (1-13):

| Cat | Type |
|-----|------|
| 1 | Missile/rocket fire |
| 6 | Hostile aircraft intrusion |
| 13 | Terrorist infiltration |

```
{{ state_attr('binary_sensor.red_alert', 'cat') == '6' }}
```

Combined with city filter:
```yaml
{{ state_attr('binary_sensor.red_alert', 'cat') == '6'
and "nahal oz" in state_attr('binary_sensor.red_alert', 'data').split(', ') }}
```

## Creating Sub-Sensors

You can create a binary sensor for your city via the UI: **Settings > Devices and Services > Helpers > Create Helper > Template > Template binary sensor**

![QQQ](https://github.com/seidnerj/red-alert/assets/19820046/3d5e93ab-d698-4ce0-b341-6bee0e641e05)

## Lovelace Card Examples

### Alert Status Card

Displays whether there is an alert, the number of active alerts, and their locations.

![TILIM](https://github.com/seidnerj/red-alert/assets/19820046/f8ad780b-7e64-4c54-ab74-79e7ff56b780)

```yaml
type: markdown
content: >-
  <center><h3>{% if state_attr('binary_sensor.red_alert', 'data_count') > 0 %}
  There {% if state_attr('binary_sensor.red_alert', 'data_count') > 1 %}are {{
  state_attr('binary_sensor.red_alert', 'data_count') }} active alerts{% elif
  state_attr('binary_sensor.red_alert', 'data_count') == 1 %}is 1 active alert{%
  endif %}{% else %}No active alerts{% endif %}</h3>

  {% if state_attr('binary_sensor.red_alert', 'data_count') > 0 %}<h2>{{
  state_attr('binary_sensor.red_alert', 'emoji') }} {{
  state_attr('binary_sensor.red_alert', 'title') }}</h2>
  <h3>{{ state_attr('binary_sensor.red_alert', 'data') }}</h3>
  **{{ state_attr('binary_sensor.red_alert', 'desc') }}** {% endif %} </center>
title: Red Alert
```

### Alert Card with Styled Elements

![3333](https://github.com/seidnerj/red-alert/assets/19820046/438c0870-56e8-461b-a1e5-aa24122a71bc)

```yaml
type: markdown
content: >-
  <ha-icon icon="{{ state_attr('binary_sensor.red_alert', 'icon')
  }}"></ha-icon> {% if state_attr('binary_sensor.red_alert', 'data_count') > 0
  %}There {% if state_attr('binary_sensor.red_alert', 'data_count') > 1 %}are {{
  state_attr('binary_sensor.red_alert', 'data_count') }} active alerts{% elif
  state_attr('binary_sensor.red_alert', 'data_count') == 1 %}is 1 active alert{%
  endif %}{% else %}No active alerts{% endif %}{% if
  state_attr('binary_sensor.red_alert', 'data_count') > 0 %}

  <ha-alert alert-type="error" title="{{ state_attr('binary_sensor.red_alert',
  'title') }}">{{ state_attr('binary_sensor.red_alert', 'data') }}</ha-alert>

  <ha-alert alert-type="warning">{{ state_attr('binary_sensor.red_alert',
  'desc') }}</ha-alert>

  {% endif %}
```

## Automation Examples

### Phone Notification on Any Alert

*(Change `#your phone#` to your entity name)*

```yaml
alias: Notify attack
description: "Real-time Attack Notification"
trigger:
  - platform: state
    entity_id:
      - binary_sensor.red_alert
    from: "off"
    to: "on"
condition: []
action:
  - service: notify.mobile_app_#your phone#
    data:
      message: "{{ state_attr('binary_sensor.red_alert', 'data') }}"
      title: "{{ state_attr('binary_sensor.red_alert', 'title') }} - {{ state_attr('binary_sensor.red_alert', 'areas') }}"
mode: single
```

### Change Light Color on Alert

Cycles lights between red and blue for 30 seconds when an alert is active in Tel Aviv, then restores the previous state.

*(Change `light.#light-1#` to your entity names)*

![20231013_221552](https://github.com/seidnerj/red-alert/assets/19820046/6e60d5ca-12a9-4fd2-9b10-bcb19bf38a6d)

```yaml
alias: Alert in TLV
description: "When an alert occurs in Tel Aviv, lights cycle red/blue for 30 seconds then revert"
trigger:
  - platform: template
    id: TLV
    value_template: >-
      {{ state_attr('binary_sensor.red_alert', 'data') | regex_search("tel aviv") }}
condition: []
action:
  - service: scene.create
    data:
      scene_id: before_red_alert
      snapshot_entities:
        - light.#light-1#
        - light.#light-2#
        - light.#light-3#
  - repeat:
      count: 30
      sequence:
        - service: light.turn_on
          data:
            color_name: blue
          target:
            entity_id:
            - light.#light-1#
            - light.#light-2#
            - light.#light-3#
        - delay:
            milliseconds: 500
        - service: light.turn_on
          data:
            color_name: red
          target:
            entity_id:
            - light.#light-1#
            - light.#light-2#
            - light.#light-3#
        - delay:
            milliseconds: 500
  - service: scene.turn_on
    data: {}
    target:
      entity_id: scene.before_red_alert
mode: single
```

### Safe-to-Go-Out Timer

Uses the `duration` attribute to start a timer, then notifies when it's safe.

Before using this, create a **Timer helper**: Settings > Devices and Services > Helpers > Create Helper > Timer, named "Red Alert".

*(Change `#your phone#` to your entity name)*

```yaml
alias: Safe to go out
description: "Notify on phone that it's safe to go outside"
mode: single
trigger:
  - platform: template
    value_template: >-
      {{ "tel aviv - city center" in state_attr('binary_sensor.red_alert',
      'data').split(', ') }}
condition: []
action:
  - service: timer.start
    data:
      duration: >-
        {{ state_attr('binary_sensor.red_alert', 'duration') }}
    target:
      entity_id: timer.red_alert
  - service: notify.mobile_app_#your phone#
    data:
      title: Alert cleared
      message: Safe to resume normal activity
```

## Verifying Sensor Functionality

To ensure the sensor is functioning correctly:

1. Access the AppDaemon web interface at http://homeassistant.local:5050/aui/index.html#/state?tab=apps (replace `homeassistant.local` with your HA IP if needed)
2. Within the state page, monitor the sensor to check if it is working as expected

![Untitled-1](https://github.com/seidnerj/red-alert/assets/19820046/664ece42-52bb-498b-8b3c-12edf41aaedb)

If the sensor isn't functioning properly, review the AppDaemon logs from its main page.

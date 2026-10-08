# CLAUDE.md

## Project Overview

red-alert is a Python library for monitoring the Israeli Home Front Command (Pikud Ha-Oref) alert API. It covers all alert types: missile/rocket fire, hostile aircraft intrusion, earthquakes, tsunamis, terrorist infiltration, hazardous materials, radiological events, and more. The core library is framework-agnostic and can be integrated into any consumer platform. Currently supported integrations:

- **Home Assistant** (AppDaemon) - the primary integration
- **Homebridge** - HTTP server exposing alert state for HomeKit contact sensors
- **UniFi** - LED color/brightness control via UniFi Network controller REST API
- **Philips Hue** - Hue Bridge REST API light color control
- **Telegram** - Bot API notifications on alert state changes
- **HomePod** - AirPlay audio playback via pyatv on alert state changes
- Other consumers can be added under `src/red_alert/integrations/`

Code provenance: write code independently; never copy license-incompatible or proprietary code, and preserve notices on any compatible reuse.

## Quick Setup

```bash
uv sync --group dev --extra homebridge --extra unifi
uv run pre-commit install
uv run pytest       # tests in tests/ mirroring src; every change ships with tests, bug fixes with a reproducing test
```

## Architecture

```
src/red_alert/
  core/              # Pure Python - ZERO framework dependencies
    alert_processor.py
    city_data.py     # CityDataManager (ICBS geographic data)
    constants.py
    history.py
    i18n.py          # gettext-based translations
    state.py         # AlertState enum + AlertStateTracker (4-state: routine/pre_alert/alert/all_clear)
    utils.py         # standardize_name, check_bom, parse_datetime_str
  locale/            # gettext .po translation files (en, he)
  integrations/
    inputs/              # Alert sources (input data)
      hfc/               # Home Front Command website API
        api_client.py    # HomeFrontCommandApiClient (httpx)
      cbs/               # Cell Broadcast System via QMI modem
        parser.py        # CbsPageParser, CbsMessageAssembler
        server.py        # CbsAlertMonitor + qmicli subprocess
        __main__.py      # python -m red_alert.integrations.inputs.cbs
    outputs/             # Alert consumers (output destinations)
      homeassistant/
        app.py           # RedAlert(Hass) - AppDaemon class
        file_manager.py
        geojson.py
      homebridge/
        server.py        # AlertMonitor + HTTP endpoints (uses AlertStateTracker)
        __main__.py      # python -m red_alert.integrations.outputs.homebridge
      unifi/
        led_controller.py  # UnifiLedController - LED control via aiounifi
        server.py          # UnifiAlertMonitor + poll loop
        __main__.py        # python -m red_alert.integrations.outputs.unifi
      hue/
        light_controller.py  # HueLightController - Hue Bridge REST API via httpx
        server.py            # HueAlertMonitor + poll loop
        __main__.py          # python -m red_alert.integrations.outputs.hue (--register)
      telegram/
        bot.py               # TelegramBot - Bot API via httpx
        server.py            # TelegramAlertMonitor + poll loop
        __main__.py          # python -m red_alert.integrations.outputs.telegram
      homepod/
        audio_controller.py  # HomepodController - pyatv AirPlay streaming
        server.py            # HomepodAlertMonitor + poll loop
        __main__.py          # python -m red_alert.integrations.outputs.homepod (--scan, --pair)
apps/red_alert/      # HACS entry point (imports from src/)
data/                # city_data.json (ICBS geographic data), cities.json
```

## Code Style Guidelines

- **Formatting**: Enforced by ruff (line-length=150, single quotes, py311)
- **Naming**: Constants in ALL_CAPS, classes in CamelCase, variables/functions in snake_case
- **Language**: All code-facing strings (logs, comments, variable names) in English. User-facing strings use gettext i18n (`_('English string')`) with Hebrew translations in `.po` files
- **Import ordering**: ALL imports at the top of the file, never mid-file
  - Standard library first, then third-party, then local modules
- **Core vs Integration**: Core modules (`src/red_alert/core/`) must have ZERO Home Assistant dependencies. They accept a `logger` callable, not a framework-specific logger
- **Type hints**: Use for function parameters and return values
- **Deduplication**: Extract shared logic into helper functions. Single source of truth (e.g., one `parse_datetime_str`, not three copies)

## i18n

- Uses Python stdlib `gettext` - no external dependencies
- English is the source language (msgid = English text)
- Hebrew translations in `src/red_alert/locale/he/LC_MESSAGES/messages.po`
- Mark translatable user-facing strings with `_('...')`
- Log messages are always in English and never translated
- Config: `language: en` (default) or `language: he` in `apps.yaml`

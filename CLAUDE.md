# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Project Overview

RedAlert is a Python library for monitoring the Israeli Home Front Command (Pikud Ha-Oref) alert API. It covers all alert types: missile/rocket fire, hostile aircraft intrusion, earthquakes, tsunamis, terrorist infiltration, hazardous materials, radiological events, and more. The core library is framework-agnostic and can be integrated into any consumer platform. Currently supported integrations:

- **Home Assistant** (AppDaemon) - the primary integration
- **Homebridge** - HTTP server exposing alert state for HomeKit contact sensors
- **UniFi** - LED color/brightness control via UniFi Network controller REST API
- **Philips Hue** - Hue Bridge REST API light color control
- Other consumers can be added under `src/red_alert/integrations/`

## Quick Setup

```bash
pip install pre-commit
pre-commit install
```

## Architecture

```
src/red_alert/
  core/              # Pure Python - ZERO framework dependencies
    api_client.py    # HomeFrontCommandApiClient (aiohttp)
    alert_processor.py
    city_data.py     # CityDataManager (ICBS geographic data)
    constants.py
    history.py
    i18n.py          # gettext-based translations
    state.py         # AlertState enum + AlertStateTracker (3-state model)
    utils.py         # standardize_name, check_bom, parse_datetime_str
  locale/            # gettext .po translation files (en, he)
  integrations/
    homeassistant/
      app.py         # RedAlert(Hass) - AppDaemon class
      file_manager.py
      geojson.py
    homebridge/
      server.py      # AlertMonitor + HTTP endpoints (uses AlertStateTracker)
      __main__.py    # python -m red_alert.integrations.homebridge
    unifi/
      led_controller.py  # UnifiLedController - LED control via aiounifi
      server.py          # UnifiAlertMonitor + poll loop
      __main__.py        # python -m red_alert.integrations.unifi
    hue/
      light_controller.py  # HueLightController - Hue Bridge REST API via httpx
      server.py            # HueAlertMonitor + poll loop
      __main__.py          # python -m red_alert.integrations.hue (--register)
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

## CRITICAL: FORMATTING RULES

- **NEVER use em dashes anywhere** - not in console output, code, commit messages, PR descriptions, comments, user messages, API calls, or any other output
- **ALWAYS use regular hyphens/dashes (-) instead of em dashes**

## CRITICAL: TESTING REQUIREMENTS

**MANDATORY**: Every feature addition or code change MUST include corresponding tests:
- **ALWAYS add tests for new features** - every new feature must include corresponding test coverage
- **ALWAYS update existing tests** when modifying behavior - if a change breaks or alters existing functionality, update the relevant tests to match
- When fixing a bug, add a test that reproduces the bug and verifies the fix
- **Test coverage is not optional** - do not consider a feature or change complete until tests are in place
- **Match existing test patterns** - follow the conventions and frameworks already used in the codebase
- **Include edge cases** - tests should cover happy paths, error cases, and boundary conditions where relevant
- Tests should be placed in the appropriate `tests/` directory mirroring the source structure

## CRITICAL: COMMIT AND PUSH RULES

**When creating git commits, Claude MUST follow these rules without exception:**
- **NEVER COMMIT WITHOUT EXPLICIT USER CONSENT** - user must explicitly say "commit" or "commit this" or similar
- **NEVER note Claude as a user on any commit** - no author, co-author, or attribution to Claude
- NEVER include "Generated with [Claude Code]" or "Co-Authored-By: Claude" in commit messages
- NEVER execute `git add` without explicit user permission
- NEVER execute `git commit` without explicit user permission (e.g. "commit this", "commit and push")
- NEVER execute `git push` unless explicitly instructed by the user
- NEVER use `git commit --no-verify` or `git commit -n` to bypass pre-commit hooks without EXPLICIT user confirmation
- ALWAYS show the complete commit message to user and ask for confirmation before executing `git commit`
- ALWAYS ask for explicit permission before pushing changes to remote repository
- ALWAYS let pre-commit hooks run - if they fail, fix the issues rather than bypassing
- Keep commit messages clean and professional without AI-generated footers
- Do NOT auto-commit after making code changes - wait for explicit user instruction

## Git Guidelines

**File Operations:**
- **ALWAYS use `git mv` instead of `mv`** when moving or renaming files in the repository
- This preserves git history and makes it easier to track file evolution

**Commit Guidelines:**
- Never commit OR push without explicit user permission
- Do NOT add ANY AI attribution messages or tool references to commit messages
- **ALWAYS split unrelated changes into separate commits** - group files by logical change (e.g., a config file change in one commit, a code refactor in another). Do not glob everything into a single commit unless all changes are part of the same logical unit of work

## i18n

- Uses Python stdlib `gettext` - no external dependencies
- English is the source language (msgid = English text)
- Hebrew translations in `src/red_alert/locale/he/LC_MESSAGES/messages.po`
- Mark translatable user-facing strings with `_('...')`
- Log messages are always in English and never translated
- Config: `language: en` (default) or `language: he` in `apps.yaml`

"""
Unified entry point: python -m red_alert --config config.json

Runs all configured inputs and outputs as concurrent async tasks
in a single process with a central event router.
"""

import argparse
import asyncio
import json
import logging
import sys

import httpx

from red_alert.core.orchestrator import AlertInput, AlertOutput, Orchestrator, PeriodicTask
from red_alert.integrations.inputs.hfc.api_client import HomeFrontCommandApiClient
from red_alert.integrations.inputs.hfc.input import HfcInput

logger = logging.getLogger('red_alert')

API_URLS = {
    'live': 'https://www.oref.org.il/WarningMessages/alert/alerts.json',
    'history': 'https://www.oref.org.il/WarningMessages/alert/History/AlertsHistory.json',
}

SESSION_HEADERS = {
    'User-Agent': 'Mozilla/5.0 (compatible; red-alert/4.0)',
    'Referer': 'https://www.oref.org.il/',
    'X-Requested-With': 'XMLHttpRequest',
    'Accept': 'application/json',
    'Accept-Encoding': 'gzip, deflate, br',
    'Accept-Language': 'he,en;q=0.9',
    'Connection': 'keep-alive',
    'Pragma': 'no-cache',
    'Cache-Control': 'no-cache',
}


def _build_inputs(config: dict, api_client: HomeFrontCommandApiClient) -> list[AlertInput]:
    inputs: list[AlertInput] = []
    inputs_cfg = config.get('inputs', {})

    if inputs_cfg.get('hfc', {}).get('enabled', True):
        hfc_cfg = inputs_cfg.get('hfc', {})
        max_hold = _compute_max_hold(config)
        inputs.append(
            HfcInput(
                api_client=api_client,
                poll_interval=hfc_cfg.get('poll_interval', config.get('interval', 1)),
                max_hold_seconds=max_hold,
            )
        )

    if inputs_cfg.get('cbs', {}).get('enabled', False):
        from red_alert.integrations.inputs.cbs.input import CbsInput

        inputs.append(CbsInput(config=inputs_cfg['cbs']))

    return inputs


def _build_outputs(config: dict) -> list[AlertOutput]:
    outputs: list[AlertOutput] = []
    outputs_cfg = config.get('outputs', {})

    if outputs_cfg.get('unifi', {}).get('enabled', False):
        from red_alert.integrations.outputs.unifi.output import UnifiOutput

        unifi_cfg = {**outputs_cfg['unifi']}
        unifi_cfg.setdefault('interval', config.get('interval', 1))
        outputs.append(UnifiOutput(config=unifi_cfg))

    if outputs_cfg.get('telegram', {}).get('enabled', False):
        from red_alert.integrations.outputs.telegram.output import TelegramOutput

        outputs.append(TelegramOutput(config=outputs_cfg['telegram']))

    if outputs_cfg.get('hue', {}).get('enabled', False):
        from red_alert.integrations.outputs.hue.output import HueOutput

        outputs.append(HueOutput(config=outputs_cfg['hue']))

    if outputs_cfg.get('homebridge', {}).get('enabled', False):
        from red_alert.integrations.outputs.homebridge.output import HomebridgeOutput

        outputs.append(HomebridgeOutput(config=outputs_cfg['homebridge']))

    if outputs_cfg.get('homepod', {}).get('enabled', False):
        from red_alert.integrations.outputs.homepod.output import HomepodOutput

        outputs.append(HomepodOutput(config=outputs_cfg['homepod']))

    return outputs


def _build_periodic_tasks(api_client: HomeFrontCommandApiClient) -> list[PeriodicTask]:
    from red_alert.core.city_data import CITY_DATA_REFRESH_INTERVAL, CityDataManager, _DEFAULT_CITY_DATA_PATH

    city_data_mgr = CityDataManager(_DEFAULT_CITY_DATA_PATH, '', api_client, logger.info)
    return [
        PeriodicTask('city-data-refresh', CITY_DATA_REFRESH_INTERVAL, city_data_mgr.refresh),
    ]


def _compute_max_hold(config: dict) -> float:
    """Compute the maximum hold time across all outputs for history seeding."""
    max_hold = 1800.0
    for output_cfg in config.get('outputs', {}).values():
        if isinstance(output_cfg, dict):
            hold = output_cfg.get('hold_seconds', {})
            if isinstance(hold, dict):
                for v in hold.values():
                    try:
                        max_hold = max(max_hold, float(v))
                    except (TypeError, ValueError):
                        pass
    return max_hold


def _validate_config(config: dict) -> None:
    inputs_cfg = config.get('inputs', {})
    outputs_cfg = config.get('outputs', {})

    has_input = any(isinstance(v, dict) and v.get('enabled', k == 'hfc') for k, v in inputs_cfg.items())
    has_output = any(isinstance(v, dict) and v.get('enabled', False) for v in outputs_cfg.values())

    if not has_input:
        raise ValueError('At least one input must be enabled')
    if not has_output:
        raise ValueError('At least one output must be enabled')


async def run(config: dict) -> None:
    _validate_config(config)

    http_client = httpx.AsyncClient(headers=SESSION_HEADERS, timeout=15.0)
    api_client = HomeFrontCommandApiClient(http_client, API_URLS, logger)

    try:
        inputs = _build_inputs(config, api_client)
        outputs = _build_outputs(config)
        periodic = _build_periodic_tasks(api_client)

        logger.info(
            'Starting orchestrator: %d input(s) [%s], %d output(s) [%s]',
            len(inputs),
            ', '.join(i.name for i in inputs),
            len(outputs),
            ', '.join(o.name for o in outputs),
        )

        orchestrator = Orchestrator(inputs, outputs, periodic)
        await orchestrator.run()
    finally:
        await http_client.aclose()


def main() -> None:
    parser = argparse.ArgumentParser(
        description='red-alert unified service - monitors alerts and controls outputs',
    )
    parser.add_argument('--config', '-c', type=str, required=True, help='Path to JSON config file')
    args = parser.parse_args()

    try:
        with open(args.config) as f:
            config = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError) as e:
        print(f'Error loading config file: {e}', file=sys.stderr)
        sys.exit(1)

    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s %(name)s %(levelname)s %(message)s',
    )
    logging.getLogger('httpx').setLevel(logging.WARNING)
    logging.getLogger('aiounifi.interfaces.connectivity').setLevel(logging.ERROR)

    asyncio.run(run(config))


if __name__ == '__main__':
    main()

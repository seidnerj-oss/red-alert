"""Tests for CBS alert monitor."""

import json
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.core.state import AlertState
from red_alert.integrations.inputs.cbs.parser import CbsMessage
from red_alert.integrations.inputs.cbs.server import (
    DEFAULT_MESSAGE_ID_MAP,
    CbsAlertMonitor,
    _create_bridge,
    _resolve_location,
    _resolve_via_centroids,
    run_monitor,
)

FIXTURES_DIR = Path(__file__).parent / 'fixtures'


class TestMessageIdMapping:
    def test_presidential_alert_is_pre_alert(self):
        assert DEFAULT_MESSAGE_ID_MAP[4370] == AlertState.PRE_ALERT

    def test_extreme_alerts_are_alert(self):
        assert DEFAULT_MESSAGE_ID_MAP[4371] == AlertState.ALERT
        assert DEFAULT_MESSAGE_ID_MAP[4372] == AlertState.ALERT

    def test_severe_alert_is_all_clear(self):
        assert DEFAULT_MESSAGE_ID_MAP[4373] == AlertState.ALL_CLEAR

    def test_test_exercise_is_routine(self):
        assert DEFAULT_MESSAGE_ID_MAP[4380] == AlertState.ROUTINE
        assert DEFAULT_MESSAGE_ID_MAP[4381] == AlertState.ROUTINE
        assert DEFAULT_MESSAGE_ID_MAP[4382] == AlertState.ROUTINE

    def test_eu_alert_is_alert(self):
        assert DEFAULT_MESSAGE_ID_MAP[4383] == AlertState.ALERT


class TestCbsAlertMonitor:
    def test_initial_state_is_routine(self):
        monitor = CbsAlertMonitor(qmicli_path='/tmp/qmicli', device='/dev/cdc-wdm0')
        assert monitor.alert_state == AlertState.ROUTINE

    def test_classify_pre_alert(self):
        monitor = CbsAlertMonitor(qmicli_path='/tmp/qmicli', device='/dev/cdc-wdm0')
        msg = CbsMessage(serial_number=0x59C0, message_id=4370, dcs=0x59, total_pages=1, pages={1: '00410042'})
        assert monitor.classify_message(msg) == AlertState.PRE_ALERT

    def test_classify_all_clear(self):
        monitor = CbsAlertMonitor(qmicli_path='/tmp/qmicli', device='/dev/cdc-wdm0')
        msg = CbsMessage(serial_number=0x57E0, message_id=4373, dcs=0x59, total_pages=1, pages={1: '00410042'})
        assert monitor.classify_message(msg) == AlertState.ALL_CLEAR

    def test_classify_unknown_id_is_routine(self):
        monitor = CbsAlertMonitor(qmicli_path='/tmp/qmicli', device='/dev/cdc-wdm0')
        msg = CbsMessage(serial_number=0x0000, message_id=9999, dcs=0x59, total_pages=1, pages={1: '00410042'})
        assert monitor.classify_message(msg) == AlertState.ROUTINE

    def test_default_areas_of_interest_empty(self):
        monitor = CbsAlertMonitor(qmicli_path='/tmp/qmicli', device='/dev/cdc-wdm0')
        assert monitor.areas_of_interest == []

    def test_areas_of_interest_from_config(self):
        monitor = CbsAlertMonitor(
            qmicli_path='/tmp/qmicli',
            device='/dev/cdc-wdm0',
            areas_of_interest=['תל אביב - יפו', 'חולון'],
        )
        assert monitor.areas_of_interest == ['תל אביב - יפו', 'חולון']

    def test_location_not_set(self):
        monitor = CbsAlertMonitor(qmicli_path='/tmp/qmicli', device='/dev/cdc-wdm0')
        assert monitor.location is None

    def test_location_from_config(self):
        monitor = CbsAlertMonitor(
            qmicli_path='/tmp/qmicli',
            device='/dev/cdc-wdm0',
            latitude=32.0853,
            longitude=34.7818,
        )
        assert monitor.location == (32.0853, 34.7818)

    def test_location_requires_both_lat_lon(self):
        monitor = CbsAlertMonitor(qmicli_path='/tmp/qmicli', device='/dev/cdc-wdm0', latitude=32.0)
        assert monitor.location is None

    def test_custom_message_id_map(self):
        custom_map = {919: AlertState.ALERT}
        monitor = CbsAlertMonitor(qmicli_path='/tmp/qmicli', device='/dev/cdc-wdm0', message_id_map=custom_map)
        msg = CbsMessage(serial_number=0x0000, message_id=919, dcs=0x59, total_pages=1, pages={1: '00410042'})
        assert monitor.classify_message(msg) == AlertState.ALERT

    @pytest.mark.asyncio
    async def test_state_change_callback(self):
        callback = AsyncMock()
        monitor = CbsAlertMonitor(
            qmicli_path='/tmp/qmicli',
            device='/dev/cdc-wdm0',
            on_state_change=callback,
        )

        msg = CbsMessage(serial_number=0x59C0, message_id=4370, dcs=0x59, total_pages=1, pages={1: '00410042'})
        await monitor._handle_message(msg)

        callback.assert_called_once()
        args = callback.call_args[0]
        assert args[0] == AlertState.ROUTINE
        assert args[1] == AlertState.PRE_ALERT

    @pytest.mark.asyncio
    async def test_no_callback_when_state_unchanged(self):
        callback = AsyncMock()
        monitor = CbsAlertMonitor(
            qmicli_path='/tmp/qmicli',
            device='/dev/cdc-wdm0',
            on_state_change=callback,
        )
        # Set state to PRE_ALERT first
        monitor._state = AlertState.PRE_ALERT

        msg = CbsMessage(serial_number=0x59C0, message_id=4370, dcs=0x59, total_pages=1, pages={1: '00410042'})
        await monitor._handle_message(msg)

        callback.assert_not_called()

    @pytest.mark.asyncio
    async def test_on_message_callback(self):
        callback = AsyncMock()
        monitor = CbsAlertMonitor(
            qmicli_path='/tmp/qmicli',
            device='/dev/cdc-wdm0',
            on_message=callback,
        )

        msg = CbsMessage(serial_number=0x59C0, message_id=4370, dcs=0x59, total_pages=1, pages={1: '00410042'})
        await monitor._handle_message(msg)

        callback.assert_called_once_with(msg, AlertState.PRE_ALERT)

    @pytest.mark.asyncio
    async def test_process_line_pipeline(self):
        """Test the full pipeline: lines -> pages -> messages -> state change."""
        callback = AsyncMock()
        monitor = CbsAlertMonitor(
            qmicli_path='/tmp/qmicli',
            device='/dev/cdc-wdm0',
            on_state_change=callback,
        )

        lines = [
            '[/dev/cdc-wdm0] Received WMS event report indication:',
            '  Transfer Route MT Message:',
            '    Raw Data (88 bytes):',
            '      59 c0 11 12 59 11 00 41 00 42 ',
            '    CBS Header:',
            '      Serial Number: 0x59c0 (GS: 1, Message Code: 412, Update: 0)',
            '      Message ID:    4370 (0x1112)',
            '      DCS:           0x59',
            '      Page:          1 of 1',
        ]

        for line in lines:
            await monitor._process_line(line)

        callback.assert_called_once()
        assert monitor.alert_state == AlertState.PRE_ALERT


class TestLocationResolution:
    """Tests for location resolution in run_monitor."""

    @pytest.mark.asyncio
    async def test_no_location_raises_error(self):
        with pytest.raises(ValueError, match='requires device location'):
            await _resolve_location({'areas_of_interest': []})

    @pytest.mark.asyncio
    async def test_no_location_no_areas_raises_error(self):
        with pytest.raises(ValueError, match='requires device location'):
            await _resolve_location({})

    @pytest.mark.asyncio
    async def test_explicit_areas_without_coords(self):
        areas = await _resolve_location({'areas_of_interest': ['חיפה']})
        assert areas == ['חיפה']

    @pytest.mark.asyncio
    async def test_polygon_resolution_primary(self, tmp_path):
        polygon_cache = tmp_path / 'polygons.json'

        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - יפו']

        with patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr):
            areas = await _resolve_location(
                {
                    'latitude': 32.0853,
                    'longitude': 34.7818,
                    'polygon_cache_path': str(polygon_cache),
                }
            )

        assert areas == ['תל אביב - יפו']

    @pytest.mark.asyncio
    async def test_falls_back_to_centroid_when_polygon_fails(self, tmp_path):
        city_data = {
            'areas': {
                'דן': {'תל אביב - מרכז': {'lat': 32.0853, 'long': 34.7818}},
            }
        }
        city_data_path = tmp_path / 'city_data.json'
        city_data_path.write_text(json.dumps(city_data), encoding='utf-8')

        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {}

        with patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr):
            areas = await _resolve_location(
                {
                    'latitude': 32.0853,
                    'longitude': 34.7818,
                    'city_data_path': str(city_data_path),
                    'location_radius_km': 5.0,
                    'polygon_cache_path': str(tmp_path / 'polygons.json'),
                }
            )

        assert 'תל אביב - מרכז' in areas

    @pytest.mark.asyncio
    async def test_falls_back_to_centroid_when_polygon_empty(self, tmp_path):
        city_data = {
            'areas': {
                'דן': {'תל אביב - מרכז': {'lat': 32.0853, 'long': 34.7818}},
            }
        }
        city_data_path = tmp_path / 'city_data.json'
        city_data_path.write_text(json.dumps(city_data), encoding='utf-8')

        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = []

        with patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr):
            areas = await _resolve_location(
                {
                    'latitude': 32.0853,
                    'longitude': 34.7818,
                    'city_data_path': str(city_data_path),
                    'location_radius_km': 5.0,
                    'polygon_cache_path': str(tmp_path / 'polygons.json'),
                }
            )

        assert 'תל אביב - מרכז' in areas

    @pytest.mark.asyncio
    async def test_explicit_areas_take_precedence_over_coords(self, tmp_path):
        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - יפו']

        with patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr):
            areas = await _resolve_location(
                {
                    'latitude': 32.0853,
                    'longitude': 34.7818,
                    'areas_of_interest': ['חיפה', 'חדרה'],
                    'polygon_cache_path': str(tmp_path / 'polygons.json'),
                }
            )

        assert areas == ['חיפה', 'חדרה']

    @pytest.mark.asyncio
    async def test_warns_on_no_overlap(self, tmp_path, caplog):
        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - יפו']

        import logging

        with (
            patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr),
            caplog.at_level(logging.WARNING, logger='red_alert.cbs'),
        ):
            await _resolve_location(
                {
                    'latitude': 32.0853,
                    'longitude': 34.7818,
                    'areas_of_interest': ['חיפה'],
                    'polygon_cache_path': str(tmp_path / 'polygons.json'),
                }
            )

        assert any('none overlap' in r.message for r in caplog.records)

    @pytest.mark.asyncio
    async def test_coords_resolve_to_nothing_raises_error(self, tmp_path):
        city_data = {'areas': {'test': {'far_city': {'lat': 40.0, 'long': 30.0}}}}
        city_data_path = tmp_path / 'city_data.json'
        city_data_path.write_text(json.dumps(city_data), encoding='utf-8')

        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {}

        with (
            patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr),
            pytest.raises(ValueError, match='did not resolve to any known cities'),
        ):
            await _resolve_location(
                {
                    'latitude': 32.0853,
                    'longitude': 34.7818,
                    'city_data_path': str(city_data_path),
                    'location_radius_km': 1.0,
                    'polygon_cache_path': str(tmp_path / 'polygons.json'),
                }
            )

    @pytest.mark.asyncio
    async def test_logs_overlap_confirmation(self, tmp_path, caplog):
        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - מרכז']

        import logging

        with (
            patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr),
            caplog.at_level(logging.INFO, logger='red_alert.cbs'),
        ):
            await _resolve_location(
                {
                    'latitude': 32.0853,
                    'longitude': 34.7818,
                    'areas_of_interest': ['תל אביב - מרכז'],
                    'polygon_cache_path': str(tmp_path / 'polygons.json'),
                }
            )

        assert any('confirm overlap' in r.message for r in caplog.records)


class TestResolveViaCentroids:
    def test_resolves_nearby_cities(self, tmp_path):
        city_data = {
            'areas': {
                'דן': {'תל אביב - מרכז': {'lat': 32.0853, 'long': 34.7818}},
                'שרון': {'נתניה': {'lat': 32.32, 'long': 34.85}},
            }
        }
        city_data_path = tmp_path / 'city_data.json'
        city_data_path.write_text(json.dumps(city_data), encoding='utf-8')

        result = _resolve_via_centroids(
            32.0853,
            34.7818,
            {
                'city_data_path': str(city_data_path),
                'location_radius_km': 5.0,
            },
        )

        assert 'תל אביב - מרכז' in result
        assert 'נתניה' not in result

    def test_returns_empty_when_no_match(self, tmp_path):
        city_data = {'areas': {'test': {'far_city': {'lat': 33.0, 'long': 35.0}}}}
        city_data_path = tmp_path / 'city_data.json'
        city_data_path.write_text(json.dumps(city_data), encoding='utf-8')

        result = _resolve_via_centroids(
            32.0853,
            34.7818,
            {
                'city_data_path': str(city_data_path),
                'location_radius_km': 1.0,
            },
        )

        assert result == []


class TestRunMonitorIntegration:
    @pytest.mark.asyncio
    async def test_run_monitor_creates_monitor_with_resolved_areas(self, tmp_path):
        captured_monitor = {}

        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - יפו']

        original_init = CbsAlertMonitor.__init__

        def capture_init(self, **kwargs):
            captured_monitor['areas'] = kwargs.get('areas_of_interest', [])
            original_init(self, **kwargs)

        config = {
            'latitude': 32.0853,
            'longitude': 34.7818,
            'polygon_cache_path': str(tmp_path / 'polygons.json'),
            'history_path': str(tmp_path / 'cbs_history.json'),
        }

        with (
            patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr),
            patch.object(CbsAlertMonitor, 'run_subprocess', new_callable=AsyncMock, side_effect=KeyboardInterrupt),
            patch.object(CbsAlertMonitor, '__init__', lambda self, **kw: capture_init(self, **kw)),
        ):
            try:
                await run_monitor(config)
            except (KeyboardInterrupt, Exception):
                pass

        assert 'תל אביב - יפו' in captured_monitor.get('areas', [])


class TestBridgeIntegration:
    """Tests for bridge mode integration in run_monitor."""

    def test_create_bridge_returns_none_without_lte_host(self):
        cfg = {'device': '/dev/cdc-wdm0'}
        assert _create_bridge(cfg) is None

    def test_create_bridge_returns_none_when_lte_host_is_none(self):
        cfg = {'device': '/dev/cdc-wdm0', 'lte_host': None}
        assert _create_bridge(cfg) is None

    def test_create_bridge_returns_bridge_when_lte_host_set(self):
        cfg = {
            'device': '/dev/cdc-wdm0',
            'lte_host': '192.168.1.100',
            'bridge_port': 18222,
        }
        bridge = _create_bridge(cfg)
        assert bridge is not None
        assert bridge.lte_host == '192.168.1.100'
        assert bridge.bridge_port == 18222
        assert bridge.device == '/dev/cdc-wdm0'

    def test_create_bridge_passes_ssh_config(self):
        cfg = {
            'device': '/dev/cdc-wdm0',
            'lte_host': '192.168.1.100',
            'ssh_key_path': '/home/user/.ssh/id_ed25519',
            'ssh_username': 'admin',
            'socat_remote_binary': '/cache/socat-mips',
        }
        bridge = _create_bridge(cfg)
        assert bridge is not None
        assert bridge._ssh_key_path == '/home/user/.ssh/id_ed25519'
        assert bridge._ssh_username == 'admin'

    @pytest.mark.asyncio
    async def test_run_monitor_bridge_mode_calls_ensure_bridge(self, tmp_path):
        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - יפו']

        mock_bridge = MagicMock()
        mock_bridge.lte_host = '192.168.1.100'
        mock_bridge.bridge_port = 18222
        mock_bridge.ensure_bridge = AsyncMock(return_value=True)
        mock_bridge.configure_cbs = AsyncMock(return_value=True)
        mock_bridge.close = AsyncMock()

        config = {
            'latitude': 32.0853,
            'longitude': 34.7818,
            'lte_host': '192.168.1.100',
            'polygon_cache_path': str(tmp_path / 'polygons.json'),
            'history_path': str(tmp_path / 'cbs_history.json'),
            'health_check_interval': 0,
        }

        with (
            patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr),
            patch('red_alert.integrations.inputs.cbs.server._create_bridge', return_value=mock_bridge),
            patch.object(CbsAlertMonitor, 'run_subprocess', new_callable=AsyncMock, side_effect=KeyboardInterrupt),
        ):
            try:
                await run_monitor(config)
            except (KeyboardInterrupt, Exception):
                pass

        mock_bridge.ensure_bridge.assert_called()
        mock_bridge.configure_cbs.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_monitor_bridge_failure_raises(self, tmp_path):
        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - יפו']

        mock_bridge = MagicMock()
        mock_bridge.lte_host = '192.168.1.100'
        mock_bridge.bridge_port = 18222
        mock_bridge.ensure_bridge = AsyncMock(return_value=False)
        mock_bridge.close = AsyncMock()

        config = {
            'latitude': 32.0853,
            'longitude': 34.7818,
            'lte_host': '192.168.1.100',
            'polygon_cache_path': str(tmp_path / 'polygons.json'),
            'history_path': str(tmp_path / 'cbs_history.json'),
        }

        with (
            patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr),
            patch('red_alert.integrations.inputs.cbs.server._create_bridge', return_value=mock_bridge),
            pytest.raises(RuntimeError, match='Failed to establish socat bridge'),
        ):
            await run_monitor(config)

        mock_bridge.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_monitor_no_bridge_when_lte_host_absent(self, tmp_path):
        """Backward compat: no bridge when lte_host is not configured."""
        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - יפו']

        config = {
            'latitude': 32.0853,
            'longitude': 34.7818,
            'polygon_cache_path': str(tmp_path / 'polygons.json'),
            'history_path': str(tmp_path / 'cbs_history.json'),
        }

        with (
            patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr),
            patch.object(CbsAlertMonitor, 'run_subprocess', new_callable=AsyncMock, side_effect=KeyboardInterrupt),
            patch('red_alert.integrations.inputs.cbs.server._create_bridge', return_value=None) as mock_create,
        ):
            try:
                await run_monitor(config)
            except (KeyboardInterrupt, Exception):
                pass

        mock_create.assert_called_once()

    @pytest.mark.asyncio
    async def test_run_monitor_bridge_closes_on_exit(self, tmp_path):
        mock_polygon_mgr = MagicMock()
        mock_polygon_mgr._fetch_polygons = AsyncMock()
        mock_polygon_mgr._polygons = {'dummy': [[]]}
        mock_polygon_mgr.find_cities_at_point.return_value = ['תל אביב - יפו']

        mock_bridge = MagicMock()
        mock_bridge.lte_host = '192.168.1.100'
        mock_bridge.bridge_port = 18222
        mock_bridge.ensure_bridge = AsyncMock(return_value=True)
        mock_bridge.configure_cbs = AsyncMock(return_value=True)
        mock_bridge.close = AsyncMock()

        config = {
            'latitude': 32.0853,
            'longitude': 34.7818,
            'lte_host': '192.168.1.100',
            'polygon_cache_path': str(tmp_path / 'polygons.json'),
            'history_path': str(tmp_path / 'cbs_history.json'),
            'health_check_interval': 0,
        }

        with (
            patch('red_alert.core.city_data.CityDataManager', return_value=mock_polygon_mgr),
            patch('red_alert.integrations.inputs.cbs.server._create_bridge', return_value=mock_bridge),
            patch.object(CbsAlertMonitor, 'run_subprocess', new_callable=AsyncMock, side_effect=KeyboardInterrupt),
        ):
            try:
                await run_monitor(config)
            except (KeyboardInterrupt, Exception):
                pass

        mock_bridge.close.assert_called_once()


class TestFixtureIntegration:
    """Full integration test using real CBS fixture data."""

    @pytest.mark.asyncio
    async def test_fixture_state_transitions(self):
        fixture_path = FIXTURES_DIR / 'cbs_sample.log'
        if not fixture_path.exists():
            pytest.skip('CBS fixture not available')

        state_changes = []

        async def on_change(old, new, msg):
            state_changes.append((old, new, msg.message_id))

        monitor = CbsAlertMonitor(
            qmicli_path='/tmp/qmicli',
            device='/dev/cdc-wdm0',
            on_state_change=on_change,
        )

        with open(fixture_path) as f:
            for line in f:
                await monitor._process_line(line)

        assert len(state_changes) >= 2

        # First transition should be to PRE_ALERT (message 4370)
        assert state_changes[0][1] == AlertState.PRE_ALERT
        assert state_changes[0][2] == 4370

        # Second transition should be to ALL_CLEAR (message 4373)
        assert state_changes[1][1] == AlertState.ALL_CLEAR
        assert state_changes[1][2] == 4373

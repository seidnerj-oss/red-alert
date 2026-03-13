"""Tests for CBS alert monitor."""

from pathlib import Path
from unittest.mock import AsyncMock

import pytest

from red_alert.core.state import AlertState
from red_alert.integrations.inputs.cbs.parser import CbsMessage
from red_alert.integrations.inputs.cbs.server import DEFAULT_MESSAGE_ID_MAP, CbsAlertMonitor

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

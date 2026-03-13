from unittest.mock import AsyncMock

import pytest

from red_alert.core.state import AlertState, AlertStateTracker
from red_alert.integrations.telegram.server import (
    TelegramAlertMonitor,
    format_alert_ended_message,
    format_alert_message,
    format_all_clear_message,
)


class TestFormatAlertMessage:
    def test_basic_alert(self):
        data = {'cat': '1', 'data': ['City A', 'City B'], 'title': 'Rockets', 'desc': 'Enter shelter'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert '<b>Rockets</b>' in msg
        assert 'City A' in msg
        assert 'City B' in msg
        assert '<i>Enter shelter</i>' in msg
        assert '🚀' in msg  # rocket emoji for cat 1

    def test_category_3_earthquake(self):
        data = {'cat': '3', 'data': ['City A'], 'title': 'Earthquake'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert '🌍' in msg  # earth emoji for cat 3

    def test_unknown_category(self):
        data = {'cat': '999', 'data': ['City A'], 'title': 'Unknown'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert '❗' in msg  # default emoji

    def test_no_description(self):
        data = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert '<i>' not in msg

    def test_no_cities(self):
        data = {'cat': '1', 'data': [], 'title': 'Rockets'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert '<b>Rockets</b>' in msg

    def test_escapes_html_in_title(self):
        data = {'cat': '1', 'data': [], 'title': 'Alert <test>'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert '&lt;test&gt;' in msg

    def test_escapes_html_in_cities(self):
        data = {'cat': '1', 'data': ['City <A>'], 'title': 'Alert'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert 'City &lt;A&gt;' in msg

    def test_hebrew_content(self):
        data = {'cat': '1', 'data': ['תל אביב', 'חיפה'], 'title': 'ירי רקטות וטילים', 'desc': 'היכנסו למרחב מוגן'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert 'ירי רקטות וטילים' in msg
        assert 'תל אביב' in msg
        assert 'חיפה' in msg

    def test_invalid_category_uses_default(self):
        data = {'cat': 'abc', 'data': ['City A'], 'title': 'Alert'}
        msg = format_alert_message(data, AlertState.ALERT)
        assert '❗' in msg


class TestFormatAllClearMessage:
    def test_contains_all_clear(self):
        msg = format_all_clear_message()
        assert 'All clear' in msg
        assert '✅' in msg
        assert '<b>' in msg


class TestFormatAlertEndedMessage:
    def test_contains_alert_ended(self):
        msg = format_alert_ended_message()
        assert 'Alert ended' in msg
        assert '✅' in msg


class TestTelegramAlertMonitor:
    def _make_monitor(self, areas_of_interest=None, hold_seconds=None):
        api_client = AsyncMock()
        bot = AsyncMock()
        bot.send_message = AsyncMock(return_value=True)
        state_tracker = AlertStateTracker(areas_of_interest=areas_of_interest, hold_seconds=hold_seconds)
        monitor = TelegramAlertMonitor(api_client, bot, state_tracker)
        return monitor, api_client, bot

    @pytest.mark.asyncio
    async def test_no_message_on_routine(self):
        monitor, api_client, bot = self._make_monitor()
        api_client.get_live_alerts.return_value = None

        await monitor.poll()
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_alert_on_state_change(self):
        monitor, api_client, bot = self._make_monitor()
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}

        await monitor.poll()
        bot.send_message.assert_called_once()
        msg = bot.send_message.call_args.args[0]
        assert 'Rockets' in msg
        assert 'City A' in msg

    @pytest.mark.asyncio
    async def test_sends_pre_alert(self):
        monitor, api_client, bot = self._make_monitor()
        api_client.get_live_alerts.return_value = {'cat': '14', 'data': ['City A'], 'title': 'Pre-alert'}

        await monitor.poll()
        bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_no_duplicate_message_same_state(self):
        monitor, api_client, bot = self._make_monitor()
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}

        await monitor.poll()  # ROUTINE -> ALERT
        await monitor.poll()  # ALERT -> ALERT (same)
        assert bot.send_message.call_count == 1

    @pytest.mark.asyncio
    async def test_sends_alert_ended_on_routine(self):
        no_hold = {'alert': 0, 'pre_alert': 0, 'all_clear': 0}
        monitor, api_client, bot = self._make_monitor(hold_seconds=no_hold)

        # First: alert
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()

        # Then: routine
        api_client.get_live_alerts.return_value = None
        await monitor.poll()

        assert bot.send_message.call_count == 2
        ended_msg = bot.send_message.call_args_list[1].args[0]
        assert 'Alert ended' in ended_msg

    @pytest.mark.asyncio
    async def test_sends_all_clear(self):
        monitor, api_client, bot = self._make_monitor()

        # First: alert
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()

        # Then: all-clear
        api_client.get_live_alerts.return_value = {'cat': '13', 'data': ['City A']}
        await monitor.poll()

        assert bot.send_message.call_count == 2
        all_clear_msg = bot.send_message.call_args_list[1].args[0]
        assert 'All clear' in all_clear_msg

    @pytest.mark.asyncio
    async def test_no_message_routine_to_routine(self):
        monitor, api_client, bot = self._make_monitor()
        api_client.get_live_alerts.return_value = None

        await monitor.poll()
        await monitor.poll()
        bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_alert_state_property(self):
        monitor, api_client, bot = self._make_monitor()
        assert monitor.alert_state == AlertState.ROUTINE

        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()
        assert monitor.alert_state == AlertState.ALERT

    @pytest.mark.asyncio
    async def test_areas_of_interest_filtering(self):
        monitor, api_client, bot = self._make_monitor(areas_of_interest=['כפר סבא'])

        # Non-matching area - no notification
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['תל אביב'], 'title': 'Rockets'}
        await monitor.poll()
        bot.send_message.assert_not_called()

        # Matching area - notification sent
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['כפר סבא'], 'title': 'Rockets'}
        await monitor.poll()
        bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_pre_alert_to_alert_sends_message(self):
        monitor, api_client, bot = self._make_monitor()

        # Pre-alert
        api_client.get_live_alerts.return_value = {'cat': '14', 'data': ['City A'], 'title': 'Pre-alert'}
        await monitor.poll()
        assert bot.send_message.call_count == 1

        # Escalate to alert
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()
        assert bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_all_clear_to_routine_no_extra_message(self):
        """ALL_CLEAR -> ROUTINE should not send 'alert ended' since all-clear was already sent."""
        monitor, api_client, bot = self._make_monitor()

        # Alert
        api_client.get_live_alerts.return_value = {'cat': '1', 'data': ['City A'], 'title': 'Rockets'}
        await monitor.poll()

        # All-clear
        api_client.get_live_alerts.return_value = {'cat': '13', 'data': ['City A']}
        await monitor.poll()

        # Routine (after all-clear)
        api_client.get_live_alerts.return_value = None
        await monitor.poll()

        # Should only have 2 messages: alert + all-clear (no extra "alert ended")
        assert bot.send_message.call_count == 2

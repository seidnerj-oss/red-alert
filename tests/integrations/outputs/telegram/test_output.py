from unittest.mock import AsyncMock

import pytest

from red_alert.core.orchestrator import AlertEvent
from red_alert.core.state import AlertState
from red_alert.integrations.outputs.telegram.output import TelegramOutput


class TestTelegramOutput:
    def _make_output(self, areas=None, hold=None):
        cfg = {
            'bot_token': 'test-token',
            'chat_id': '12345',
            'areas_of_interest': areas or [],
            'hold_seconds': hold or {'alert': 0, 'pre_alert': 0, 'all_clear': 0},
        }
        return TelegramOutput(config=cfg)

    def _make_event(self, source='hfc', state=AlertState.ALERT, cat='1', cities=None):
        data = {'cat': cat, 'title': 'ירי רקטות', 'data': cities or ['כפר סבא'], 'desc': 'Shelter'} if state != AlertState.ROUTINE else None
        return AlertEvent(source=source, state=state, data=data)

    def test_name(self):
        out = self._make_output()
        assert out.name == 'telegram'

    @pytest.mark.asyncio
    async def test_start_requires_bot_token(self):
        out = TelegramOutput(config={'chat_id': '123'})
        with pytest.raises(ValueError, match='bot_token'):
            await out.start()

    @pytest.mark.asyncio
    async def test_start_requires_chat_id(self):
        out = TelegramOutput(config={'bot_token': 'tok'})
        with pytest.raises(ValueError, match='chat_id'):
            await out.start()

    @pytest.mark.asyncio
    async def test_sends_message_on_alert(self):
        out = self._make_output()
        await out.start()
        out._bot.send_message = AsyncMock(return_value=True)

        event = self._make_event()
        await out.handle_event(event)

        out._bot.send_message.assert_called_once()
        msg = out._bot.send_message.call_args[0][0]
        assert 'ירי רקטות' in msg
        assert 'כפר סבא' in msg

    @pytest.mark.asyncio
    async def test_no_message_on_routine(self):
        out = self._make_output()
        await out.start()
        out._bot.send_message = AsyncMock(return_value=True)

        event = self._make_event(state=AlertState.ROUTINE)
        await out.handle_event(event)

        out._bot.send_message.assert_not_called()

    @pytest.mark.asyncio
    async def test_sends_all_clear_message(self):
        out = self._make_output()
        await out.start()
        out._bot.send_message = AsyncMock(return_value=True)

        alert_event = self._make_event()
        await out.handle_event(alert_event)
        out._bot.send_message.reset_mock()

        all_clear_data = {'cat': '13', 'title': 'האירוע הסתיים', 'data': ['כפר סבא'], 'desc': ''}
        clear_event = AlertEvent(source='hfc', state=AlertState.ALL_CLEAR, data=all_clear_data)
        await out.handle_event(clear_event)

        out._bot.send_message.assert_called_once()
        msg = out._bot.send_message.call_args[0][0]
        assert 'All clear' in msg

    @pytest.mark.asyncio
    async def test_sends_alert_ended_on_hold_expiry(self):
        out = self._make_output()
        await out.start()
        out._bot.send_message = AsyncMock(return_value=True)

        alert_event = self._make_event()
        await out.handle_event(alert_event)
        out._bot.send_message.reset_mock()

        routine_event = self._make_event(state=AlertState.ROUTINE)
        await out.handle_event(routine_event)

        out._bot.send_message.assert_called_once()
        msg = out._bot.send_message.call_args[0][0]
        assert 'Alert ended' in msg

    @pytest.mark.asyncio
    async def test_area_filtering(self):
        out = self._make_output(areas=['כפר סבא'])
        await out.start()
        out._bot.send_message = AsyncMock(return_value=True)

        event = self._make_event(cities=['תל אביב'])
        await out.handle_event(event)
        out._bot.send_message.assert_not_called()

        event = self._make_event(cities=['כפר סבא'])
        await out.handle_event(event)
        out._bot.send_message.assert_called_once()

    @pytest.mark.asyncio
    async def test_multi_source_escalation(self):
        out = self._make_output()
        await out.start()
        out._bot.send_message = AsyncMock(return_value=True)

        cbs_event = AlertEvent(
            source='cbs',
            state=AlertState.PRE_ALERT,
            data={'cat': '14', 'title': 'CBS pre-alert', 'data': ['כפר סבא'], 'desc': ''},
        )
        await out.handle_event(cbs_event)
        assert out._bot.send_message.call_count == 1

        hfc_event = AlertEvent(
            source='hfc',
            state=AlertState.ALERT,
            data={'cat': '1', 'title': 'ירי רקטות', 'data': ['כפר סבא'], 'desc': 'Shelter'},
        )
        await out.handle_event(hfc_event)
        assert out._bot.send_message.call_count == 2

    @pytest.mark.asyncio
    async def test_stop_closes_bot(self):
        out = self._make_output()
        await out.start()
        out._bot.close = AsyncMock()
        await out.stop()
        out._bot.close.assert_called_once()

    @pytest.mark.asyncio
    async def test_handle_event_before_start(self):
        out = self._make_output()
        event = self._make_event()
        await out.handle_event(event)

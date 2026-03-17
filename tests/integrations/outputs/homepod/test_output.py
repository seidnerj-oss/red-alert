import pytest

from red_alert.core.orchestrator import AlertEvent
from red_alert.core.state import AlertState
from red_alert.integrations.outputs.homepod.output import HomepodOutput


class TestHomepodOutput:
    def test_name(self):
        out = HomepodOutput(config={})
        assert out.name == 'homepod'

    @pytest.mark.asyncio
    async def test_start_requires_devices(self):
        out = HomepodOutput(config={})
        with pytest.raises(ValueError, match='devices'):
            await out.start()

    @pytest.mark.asyncio
    async def test_handle_event_before_start(self):
        out = HomepodOutput(config={})
        event = AlertEvent(source='hfc', state=AlertState.ALERT)
        await out.handle_event(event)

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        out = HomepodOutput(config={})
        await out.stop()

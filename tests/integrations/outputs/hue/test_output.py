import pytest

from red_alert.core.orchestrator import AlertEvent
from red_alert.core.state import AlertState
from red_alert.integrations.outputs.hue.output import HueOutput


class TestHueOutput:
    def test_name(self):
        out = HueOutput(config={})
        assert out.name == 'hue'

    @pytest.mark.asyncio
    async def test_start_requires_bridge_ip(self):
        out = HueOutput(config={'api_key': 'key', 'lights': ['1']})
        with pytest.raises(ValueError, match='Bridge IP'):
            await out.start()

    @pytest.mark.asyncio
    async def test_start_requires_api_key(self):
        out = HueOutput(config={'bridge_ip': '1.2.3.4', 'lights': ['1']})
        with pytest.raises(ValueError, match='API key'):
            await out.start()

    @pytest.mark.asyncio
    async def test_start_requires_lights_or_groups(self):
        out = HueOutput(config={'bridge_ip': '1.2.3.4', 'api_key': 'key'})
        with pytest.raises(ValueError, match='lights or groups'):
            await out.start()

    @pytest.mark.asyncio
    async def test_stop_without_start(self):
        out = HueOutput(config={})
        await out.stop()

    @pytest.mark.asyncio
    async def test_handle_event_before_start(self):
        out = HueOutput(config={})
        event = AlertEvent(source='hfc', state=AlertState.ALERT)
        await out.handle_event(event)

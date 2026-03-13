import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.integrations.outputs.homepod.audio_controller import HomepodController


def _mock_atv():
    """Create a mock pyatv AppleTV device."""
    atv = MagicMock()
    atv.audio = MagicMock()
    atv.audio.set_volume = AsyncMock()
    atv.remote_control = MagicMock()
    atv.remote_control.stop = AsyncMock()
    atv.stream = MagicMock()
    atv.stream.play_url = AsyncMock()
    atv.stream.stream_file = AsyncMock()
    atv.metadata = MagicMock()
    atv.metadata.playing = AsyncMock()
    atv.close = MagicMock()
    return atv


class TestProperties:
    def test_name_defaults_to_identifier(self):
        controller = HomepodController('my-id')
        assert controller.name == 'my-id'
        assert controller.identifier == 'my-id'

    def test_name_custom(self):
        controller = HomepodController('my-id', name='Living Room')
        assert controller.name == 'Living Room'
        assert controller.identifier == 'my-id'


class TestConnect:
    @pytest.mark.asyncio
    @patch('red_alert.integrations.outputs.homepod.audio_controller._PROTOCOL_MAP', {'airplay': 'FakeProtocol.AirPlay'})
    @patch('red_alert.integrations.outputs.homepod.audio_controller._pyatv')
    async def test_scans_and_connects(self, mock_pyatv):
        config = MagicMock()
        mock_pyatv.scan = AsyncMock(return_value=[config])
        mock_pyatv.connect = AsyncMock(return_value=_mock_atv())

        controller = HomepodController('test-id', credentials={'airplay': 'creds123'})
        await controller.connect()

        mock_pyatv.scan.assert_called_once_with(identifier='test-id', timeout=5)
        mock_pyatv.connect.assert_called_once_with(config)
        config.set_credentials.assert_called_once_with('FakeProtocol.AirPlay', 'creds123')
        assert controller._atv is not None

    @pytest.mark.asyncio
    @patch('red_alert.integrations.outputs.homepod.audio_controller._pyatv')
    async def test_device_not_found_raises(self, mock_pyatv):
        mock_pyatv.scan = AsyncMock(return_value=[])

        controller = HomepodController('missing-id', name='Missing')
        with pytest.raises(ConnectionError, match='HomePod not found'):
            await controller.connect()

    @pytest.mark.asyncio
    @patch('red_alert.integrations.outputs.homepod.audio_controller._pyatv', None)
    async def test_pyatv_not_installed_raises(self):
        controller = HomepodController('test-id')
        with pytest.raises(ImportError, match='pyatv is not installed'):
            await controller.connect()

    @pytest.mark.asyncio
    @patch('red_alert.integrations.outputs.homepod.audio_controller._PROTOCOL_MAP', {})
    @patch('red_alert.integrations.outputs.homepod.audio_controller._pyatv')
    async def test_unknown_protocol_ignored(self, mock_pyatv):
        config = MagicMock()
        mock_pyatv.scan = AsyncMock(return_value=[config])
        mock_pyatv.connect = AsyncMock(return_value=_mock_atv())

        controller = HomepodController('test-id', credentials={'unknown_proto': 'creds'})
        await controller.connect()

        config.set_credentials.assert_not_called()


class TestPlay:
    @pytest.mark.asyncio
    async def test_play_url(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('http://example.com/siren.mp3')

        controller._atv.stream.play_url.assert_called_once_with('http://example.com/siren.mp3')
        controller._atv.stream.stream_file.assert_not_called()

    @pytest.mark.asyncio
    async def test_play_https_url(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('https://example.com/siren.mp3')

        controller._atv.stream.play_url.assert_called_once_with('https://example.com/siren.mp3')

    @pytest.mark.asyncio
    async def test_play_local_file(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3')

        controller._atv.stream.stream_file.assert_called_once_with('/path/to/siren.mp3')
        controller._atv.stream.play_url.assert_not_called()

    @pytest.mark.asyncio
    async def test_sets_volume(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3', volume=80)

        controller._atv.audio.set_volume.assert_called_once_with(80)

    @pytest.mark.asyncio
    async def test_clamps_volume_high(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3', volume=150)

        controller._atv.audio.set_volume.assert_called_once_with(100)

    @pytest.mark.asyncio
    async def test_clamps_volume_low(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3', volume=-10)

        controller._atv.audio.set_volume.assert_called_once_with(0)

    @pytest.mark.asyncio
    async def test_no_volume_change_when_none(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3')

        controller._atv.audio.set_volume.assert_not_called()

    @pytest.mark.asyncio
    async def test_not_connected_raises(self):
        controller = HomepodController('test-id', name='Test')

        with pytest.raises(RuntimeError, match='Not connected'):
            await controller.play('/path/to/siren.mp3')

    @pytest.mark.asyncio
    async def test_loop_starts_task(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3', loop=True)

        assert controller._loop_task is not None
        assert not controller._loop_task.done()
        controller._cancel_loop()

    @pytest.mark.asyncio
    async def test_no_loop_by_default(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3')

        assert controller._loop_task is None

    @pytest.mark.asyncio
    async def test_cancels_previous_loop(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3', loop=True)
        first_task = controller._loop_task

        await controller.play('/path/to/other.mp3')
        await asyncio.sleep(0)  # Let cancellation propagate

        assert first_task.cancelled()


class TestStop:
    @pytest.mark.asyncio
    async def test_stops_playback(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.stop()

        controller._atv.remote_control.stop.assert_called_once()

    @pytest.mark.asyncio
    async def test_handles_error(self):
        controller = HomepodController('test-id', name='Test')
        atv = _mock_atv()
        atv.remote_control.stop = AsyncMock(side_effect=Exception('Not supported'))
        controller._atv = atv

        await controller.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_when_not_connected(self):
        controller = HomepodController('test-id', name='Test')
        await controller.stop()  # Should not raise

    @pytest.mark.asyncio
    async def test_cancels_loop(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3', loop=True)
        loop_task = controller._loop_task

        await controller.stop()
        await asyncio.sleep(0)  # Let cancellation propagate

        assert loop_task.cancelled()
        assert controller._loop_task is None


class TestSetVolume:
    @pytest.mark.asyncio
    async def test_sets_volume(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.set_volume(50)

        controller._atv.audio.set_volume.assert_called_once_with(50)

    @pytest.mark.asyncio
    async def test_clamps_low(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.set_volume(-10)

        controller._atv.audio.set_volume.assert_called_once_with(0)

    @pytest.mark.asyncio
    async def test_clamps_high(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.set_volume(200)

        controller._atv.audio.set_volume.assert_called_once_with(100)

    @pytest.mark.asyncio
    async def test_when_not_connected(self):
        controller = HomepodController('test-id', name='Test')
        await controller.set_volume(50)  # Should not raise


class TestClose:
    @pytest.mark.asyncio
    async def test_closes_connection(self):
        controller = HomepodController('test-id', name='Test')
        atv = _mock_atv()
        controller._atv = atv

        await controller.close()

        atv.close.assert_called_once()
        assert controller._atv is None

    @pytest.mark.asyncio
    async def test_when_not_connected(self):
        controller = HomepodController('test-id', name='Test')
        await controller.close()  # Should not raise

    @pytest.mark.asyncio
    async def test_cancels_loop(self):
        controller = HomepodController('test-id', name='Test')
        controller._atv = _mock_atv()

        await controller.play('/path/to/siren.mp3', loop=True)
        loop_task = controller._loop_task

        await controller.close()
        await asyncio.sleep(0)  # Let cancellation propagate

        assert loop_task.cancelled()

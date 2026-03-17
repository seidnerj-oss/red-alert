import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from red_alert.integrations.outputs.homepod.audio_controller import HomepodController


def _mock_subprocess(returncode=0, stdout=b'', stderr=b''):
    proc = MagicMock()
    proc.returncode = returncode
    proc.communicate = AsyncMock(return_value=(stdout, stderr))
    proc.kill = MagicMock()
    return proc


class TestProperties:
    def test_name_defaults_to_identifier(self):
        controller = HomepodController('my-id')
        assert controller.name == 'my-id'
        assert controller.identifier == 'my-id'

    def test_name_custom(self):
        controller = HomepodController('my-id', name='Living Room')
        assert controller.name == 'Living Room'
        assert controller.identifier == 'my-id'

    def test_host(self):
        controller = HomepodController('my-id', host='172.16.1.219')
        assert controller._host == '172.16.1.219'


class TestConnect:
    @pytest.mark.asyncio
    async def test_connect_success(self):
        proc = _mock_subprocess(stdout=b'Device state: Playing')
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id', host='172.16.1.100', name='Test')
            await controller.connect()

    @pytest.mark.asyncio
    async def test_connect_failure(self):
        proc = _mock_subprocess(returncode=1, stderr=b'Device not found')
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id', name='Test')
            with pytest.raises(ConnectionError, match='Cannot reach'):
                await controller.connect()


class TestRunAtvremote:
    @pytest.mark.asyncio
    async def test_builds_command_with_id(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc) as mock_exec:
            controller = HomepodController('test-id')
            await controller._run_atvremote('playing')

            cmd = mock_exec.call_args[0]
            assert '--id' in cmd
            assert 'test-id' in cmd
            assert 'playing' in cmd

    @pytest.mark.asyncio
    async def test_includes_scan_hosts_when_host_set(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc) as mock_exec:
            controller = HomepodController('test-id', host='172.16.1.100')
            await controller._run_atvremote('playing')

            cmd = mock_exec.call_args[0]
            assert '--scan-hosts' in cmd
            assert '172.16.1.100' in cmd

    @pytest.mark.asyncio
    async def test_no_scan_hosts_without_host(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc) as mock_exec:
            controller = HomepodController('test-id')
            await controller._run_atvremote('playing')

            cmd = mock_exec.call_args[0]
            assert '--scan-hosts' not in cmd

    @pytest.mark.asyncio
    async def test_returns_stdout_stderr(self):
        proc = _mock_subprocess(stdout=b'output here', stderr=b'error here')
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id')
            rc, stdout, stderr = await controller._run_atvremote('playing')
            assert rc == 0
            assert stdout == 'output here'
            assert stderr == 'error here'

    @pytest.mark.asyncio
    async def test_timeout_kills_process(self):
        proc = _mock_subprocess()
        proc.communicate = AsyncMock(side_effect=asyncio.TimeoutError())
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id')
            rc, _, stderr = await controller._run_atvremote('playing', timeout=0.1)
            assert rc == 1
            assert stderr == 'timeout'


class TestPlay:
    @pytest.mark.asyncio
    async def test_streams_file(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc) as mock_exec:
            controller = HomepodController('test-id')
            await controller.play('/path/to/siren.wav')

            calls = [c[0] for c in mock_exec.call_args_list]
            stream_call = [c for c in calls if 'stream_file=/path/to/siren.wav' in c]
            assert len(stream_call) == 1

    @pytest.mark.asyncio
    async def test_sets_volume_before_streaming(self):
        proc = _mock_subprocess()
        call_order = []

        async def tracking_create(*args, **kwargs):
            cmd = args
            for arg in cmd:
                if 'set_volume' in str(arg):
                    call_order.append('volume')
                if 'stream_file' in str(arg):
                    call_order.append('stream')
            return proc

        with patch('asyncio.create_subprocess_exec', side_effect=tracking_create):
            controller = HomepodController('test-id')
            await controller.play('/path/to/siren.wav', volume=80)

        assert call_order == ['volume', 'stream']

    @pytest.mark.asyncio
    async def test_no_volume_when_none(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc) as mock_exec:
            controller = HomepodController('test-id')
            await controller.play('/path/to/siren.wav')

            calls = [c[0] for c in mock_exec.call_args_list]
            volume_calls = [c for c in calls if any('set_volume' in str(arg) for arg in c)]
            assert len(volume_calls) == 0

    @pytest.mark.asyncio
    async def test_loop_starts_task(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id')
            await controller.play('/path/to/siren.wav', loop=True)

            assert controller._loop_task is not None
            assert not controller._loop_task.done()
            controller._cancel_loop()

    @pytest.mark.asyncio
    async def test_no_loop_by_default(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id')
            await controller.play('/path/to/siren.wav')
            assert controller._loop_task is None

    @pytest.mark.asyncio
    async def test_stream_failure_logs_error(self):
        proc = _mock_subprocess(returncode=1, stderr=b'stream failed')
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id')
            await controller.play('/path/to/siren.wav')


class TestStop:
    @pytest.mark.asyncio
    async def test_sends_stop_command(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc) as mock_exec:
            controller = HomepodController('test-id')
            await controller.stop()

            calls = [c[0] for c in mock_exec.call_args_list]
            stop_calls = [c for c in calls if 'stop' in c]
            assert len(stop_calls) == 1

    @pytest.mark.asyncio
    async def test_cancels_loop(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id')
            await controller.play('/path/to/siren.wav', loop=True)
            loop_task = controller._loop_task

            await controller.stop()
            await asyncio.sleep(0)

            assert loop_task.cancelled()
            assert controller._loop_task is None


class TestClose:
    @pytest.mark.asyncio
    async def test_cancels_loop(self):
        proc = _mock_subprocess()
        with patch('asyncio.create_subprocess_exec', new_callable=AsyncMock, return_value=proc):
            controller = HomepodController('test-id')
            await controller.play('/path/to/siren.wav', loop=True)
            loop_task = controller._loop_task

            await controller.close()
            await asyncio.sleep(0)

            assert loop_task.cancelled()

    @pytest.mark.asyncio
    async def test_noop_when_no_loop(self):
        controller = HomepodController('test-id')
        await controller.close()

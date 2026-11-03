import importlib

import pytest
from httpx._decoders import SUPPORTED_DECODERS

HEADER_MODULES = [
    'red_alert.__main__',
    'red_alert.integrations.outputs.homebridge.server',
    'red_alert.integrations.outputs.hue.server',
    'red_alert.integrations.outputs.unifi.server',
    'red_alert.integrations.outputs.homepod.server',
    'red_alert.integrations.outputs.telegram.server',
]


@pytest.mark.parametrize('module_name', HEADER_MODULES)
def test_accept_encoding_only_lists_decodable_encodings(module_name):
    # Advertising an encoding httpx cannot decode (e.g. br without the brotli package)
    # makes the CDN send compressed bytes that then fail to parse as JSON.
    headers = importlib.import_module(module_name).SESSION_HEADERS
    encodings = [e.strip() for e in headers['Accept-Encoding'].split(',')]
    assert all(e in SUPPORTED_DECODERS for e in encodings), encodings

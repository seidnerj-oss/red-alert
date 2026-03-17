import pytest

from red_alert.__main__ import _compute_max_hold, _validate_config


class TestValidateConfig:
    def test_no_inputs_raises(self):
        config = {'inputs': {}, 'outputs': {'unifi': {'enabled': True}}}
        with pytest.raises(ValueError, match='input'):
            _validate_config(config)

    def test_no_outputs_raises(self):
        config = {'inputs': {'hfc': {}}, 'outputs': {}}
        with pytest.raises(ValueError, match='output'):
            _validate_config(config)

    def test_valid_config(self):
        config = {
            'inputs': {'hfc': {'enabled': True}},
            'outputs': {'unifi': {'enabled': True}},
        }
        _validate_config(config)

    def test_hfc_enabled_by_default(self):
        config = {
            'inputs': {'hfc': {}},
            'outputs': {'unifi': {'enabled': True}},
        }
        _validate_config(config)

    def test_cbs_not_enabled_by_default(self):
        config = {
            'inputs': {'cbs': {}},
            'outputs': {'unifi': {'enabled': True}},
        }
        with pytest.raises(ValueError, match='input'):
            _validate_config(config)

    def test_all_outputs_disabled(self):
        config = {
            'inputs': {'hfc': {}},
            'outputs': {'unifi': {'enabled': False}, 'telegram': {'enabled': False}},
        }
        with pytest.raises(ValueError, match='output'):
            _validate_config(config)


class TestComputeMaxHold:
    def test_default_value(self):
        assert _compute_max_hold({}) == 1800.0

    def test_extracts_from_outputs(self):
        config = {
            'outputs': {
                'unifi': {'hold_seconds': {'alert': 3600}},
                'telegram': {'hold_seconds': {'alert': 600}},
            },
        }
        assert _compute_max_hold(config) == 3600.0

    def test_handles_non_numeric(self):
        config = {
            'outputs': {
                'unifi': {'hold_seconds': {'alert': 'bad'}},
            },
        }
        assert _compute_max_hold(config) == 1800.0

    def test_handles_missing_hold_seconds(self):
        config = {'outputs': {'unifi': {}}}
        assert _compute_max_hold(config) == 1800.0

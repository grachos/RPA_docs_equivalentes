import os

import pytest
from dotenv import dotenv_values

from app import config


def test_save_credential_creates_and_updates_env(tmp_path, monkeypatch):
    env = tmp_path / ".env"
    monkeypatch.delenv("CADI_PASSWORD", raising=False)
    config.save_credential("CADI_PASSWORD", 'a b#c"d', env_path=env)
    config.save_credential("MYE_USER", "contabilidad", env_path=env)
    config.save_credential("CADI_PASSWORD", "nueva", env_path=env)
    values = dotenv_values(env)
    assert values == {"CADI_PASSWORD": "nueva", "MYE_USER": "contabilidad"}
    assert config.get_credential("CADI_PASSWORD") == "nueva"
    os.environ.pop("MYE_USER", None)


def test_save_credential_rejects_bad_input(tmp_path):
    with pytest.raises(ValueError):
        config.save_credential("OTRA", "x", env_path=tmp_path / ".env")
    with pytest.raises(ValueError):
        config.save_credential("MYE_USER", "a\nb", env_path=tmp_path / ".env")

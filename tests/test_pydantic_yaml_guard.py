# tests/test_pydantic_yaml_guard.py

import textwrap
from pathlib import Path

import pytest
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings

from oscar_hooks.pydantic_yaml_guard import _import_module_from_path, find_settings_classes, get_secret_dotpaths, check_files

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def tmp_settings_file(tmp_path):
    """Create a temporary settings file that fails to import cleanly
    (simulates the YamlConfigSettingsSource crash) but has classes defined before the crash."""
    content = textwrap.dedent("""
        from pydantic import Field, SecretStr
        from pydantic_settings import BaseSettings

        class PostgresConfig(BaseSettings):
            user: str = Field(alias="POSTGRES_USER")
            password: SecretStr = Field(alias="POSTGRES_PASSWORD")
            host: str = Field(default="localhost", alias="POSTGRES_HOST")
            port: int = Field(default=5432, alias="POSTGRES_PORT")
            db: str = Field(alias="POSTGRES_DB")

        # Simulate a crash after the class is defined (e.g. YamlConfigSettingsSource)
        raise RuntimeError("simulated import error")
    """)
    f = tmp_path / "settings.py"
    f.write_text(content)
    return f


@pytest.fixture
def tmp_yaml_with_secret(tmp_path):
    content = textwrap.dedent("""
        postgres:
          user: root_admin
          password: mypassword
          host: localhost
          port: 5432
          db: mydb
    """)
    f = tmp_path / "config.yaml"
    f.write_text(content)
    return f


@pytest.fixture
def tmp_yaml_without_secret(tmp_path):
    content = textwrap.dedent("""
        postgres:
          user: root_admin
          host: localhost
          port: 5432
          db: mydb
    """)
    f = tmp_path / "config.yaml"
    f.write_text(content)
    return f


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------

def test_import_partial_module_on_crash(tmp_settings_file):
    """Module that crashes mid-import should still be returned with classes defined before the crash."""
    module = _import_module_from_path(tmp_settings_file)
    assert module is not None, "Expected partial module, got None"
    assert hasattr(module, "PostgresConfig"), "PostgresConfig should be present on the partial module"


def test_find_settings_classes_on_crashing_module(tmp_settings_file):
    """find_settings_classes should find BaseSettings subclasses even in a crashing module."""
    classes = find_settings_classes([tmp_settings_file])
    names = [cls.__name__ for cls in classes]
    assert "PostgresConfig" in names


def test_secret_dotpaths_nested():
    """get_secret_dotpaths should detect nested secret fields like postgres.password."""
    class PostgresConfig(BaseSettings):
        user: str = Field(alias="POSTGRES_USER")
        password: SecretStr = Field(alias="POSTGRES_PASSWORD")

    class APIConfig(BaseSettings):
        postgres: PostgresConfig = Field(default_factory=PostgresConfig)

    paths = get_secret_dotpaths(APIConfig)
    assert "postgres.password" in paths
    assert "postgres.POSTGRES_PASSWORD" in paths


def test_check_files_detects_secret(tmp_yaml_with_secret):
    """check_files should flag postgres.password when it appears in YAML."""
    secret_paths = {"postgres.password", "postgres.POSTGRES_PASSWORD"}
    errors = check_files([tmp_yaml_with_secret], secret_paths)
    assert any("postgres.password" in e for e in errors)


def test_check_files_clean_yaml(tmp_yaml_without_secret):
    """check_files should pass when no secret fields are present in YAML."""
    secret_paths = {"postgres.password", "postgres.POSTGRES_PASSWORD"}
    errors = check_files([tmp_yaml_without_secret], secret_paths)
    assert errors == []
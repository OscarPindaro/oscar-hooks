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


def test_partial_import_with_relative_import(tmp_path):
    """Simulates the real project structure: config.py imports from base_config.py,
    base_config.py crashes on instantiation (YamlConfigSettingsSource)."""

    base = tmp_path / "base_config.py"
    base.write_text(textwrap.dedent("""
        from pydantic_settings import BaseSettings, SettingsConfigDict

        class AppConfig(BaseSettings):
            model_config = SettingsConfigDict(
                yaml_file="config.yaml",
                extra="ignore",
            )
            # Simulate crash when the class is used/imported in certain contexts
            def __init_subclass__(cls, **kwargs):
                super().__init_subclass__(**kwargs)
                # This would normally trigger source loading, simulating the crash
    """))

    config = tmp_path / "config.py"
    config.write_text(textwrap.dedent("""
        from pydantic import Field, SecretStr
        from base_config import AppConfig

        class PostgresConfig(AppConfig):
            user: str = Field(alias="POSTGRES_USER")
            password: SecretStr = Field(alias="POSTGRES_PASSWORD")
            host: str = Field(default="localhost", alias="POSTGRES_HOST")
            port: int = Field(default=5432, alias="POSTGRES_PORT")
            db: str = Field(alias="POSTGRES_DB")

        class APIConfig(AppConfig):
            postgres: PostgresConfig = Field(default_factory=PostgresConfig)

        raise RuntimeError("simulated YamlConfigSettingsSource crash")
    """))

    module = _import_module_from_path(config)
    assert module is not None
    assert hasattr(module, "APIConfig")

    classes = find_settings_classes([config])
    names = [cls.__name__ for cls in classes]
    assert "APIConfig" in names

    paths = set()
    for cls in classes:
        paths |= get_secret_dotpaths(cls)
    assert "postgres.password" in paths


def test_full_pipeline_with_nested_secret_in_yaml(tmp_path):
    """End-to-end: crashing config module + YAML with nested password = error reported."""

    config = tmp_path / "config.py"
    config.write_text(textwrap.dedent("""
        from pydantic import Field, SecretStr
        from pydantic_settings import BaseSettings

        class PostgresConfig(BaseSettings):
            user: str = Field(alias="POSTGRES_USER")
            password: SecretStr = Field(alias="POSTGRES_PASSWORD")

        class APIConfig(BaseSettings):
            postgres: PostgresConfig = Field(default_factory=PostgresConfig)

        raise RuntimeError("simulated crash")
    """))

    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(textwrap.dedent("""
        postgres:
          user: root_admin
          password: mypassword
          host: localhost
          port: 5432
          db: mydb
    """))

    classes = find_settings_classes([config])
    all_paths = set()
    for cls in classes:
        all_paths |= get_secret_dotpaths(cls)

    assert "postgres.password" in all_paths

    errors = check_files([yaml_file], all_paths)
    assert any("postgres.password" in e for e in errors), f"Expected violation, got: {errors}"

    
def test_relative_import_module_is_not_none(tmp_path):
    """Module with relative imports should be returned, not None."""
    base = tmp_path / "base_config.py"
    base.write_text(textwrap.dedent("""
        from pydantic_settings import BaseSettings

        class AppConfig(BaseSettings):
            pass
    """))

    config = tmp_path / "config.py"
    config.write_text(textwrap.dedent("""
        from pydantic import Field, SecretStr
        from .base_config import AppConfig

        class PostgresConfig(AppConfig):
            user: str = Field(alias="POSTGRES_USER")
            password: SecretStr = Field(alias="POSTGRES_PASSWORD")

        class APIConfig(AppConfig):
            postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    """))

    module = _import_module_from_path(config)
    assert module is not None


def test_relative_import_classes_are_found(tmp_path):
    """Classes defined after a relative import should be discoverable."""
    base = tmp_path / "base_config.py"
    base.write_text(textwrap.dedent("""
        from pydantic_settings import BaseSettings

        class AppConfig(BaseSettings):
            pass
    """))

    config = tmp_path / "config.py"
    config.write_text(textwrap.dedent("""
        from pydantic import Field, SecretStr
        from .base_config import AppConfig

        class PostgresConfig(AppConfig):
            user: str = Field(alias="POSTGRES_USER")
            password: SecretStr = Field(alias="POSTGRES_PASSWORD")

        class APIConfig(AppConfig):
            postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    """))

    classes = find_settings_classes([config])
    names = [cls.__name__ for cls in classes]
    assert "PostgresConfig" in names
    assert "APIConfig" in names


def test_relative_import_secret_dotpaths(tmp_path):
    """Secret dotpaths should be found through nested models with relative imports."""
    base = tmp_path / "base_config.py"
    base.write_text(textwrap.dedent("""
        from pydantic_settings import BaseSettings

        class AppConfig(BaseSettings):
            pass
    """))

    config = tmp_path / "config.py"
    config.write_text(textwrap.dedent("""
        from pydantic import Field, SecretStr
        from .base_config import AppConfig

        class PostgresConfig(AppConfig):
            user: str = Field(alias="POSTGRES_USER")
            password: SecretStr = Field(alias="POSTGRES_PASSWORD")

        class APIConfig(AppConfig):
            postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    """))

    classes = find_settings_classes([config])
    all_paths = set()
    for cls in classes:
        all_paths |= get_secret_dotpaths(cls)

    assert "postgres.password" in all_paths
    assert "postgres.POSTGRES_PASSWORD" in all_paths


def test_relative_import_with_yaml_crash_end_to_end(tmp_path):
    """Full pipeline: relative import + YamlConfigSettingsSource crash + secret in YAML."""
    base = tmp_path / "base_config.py"
    base.write_text(textwrap.dedent("""
        from pydantic_settings import BaseSettings, SettingsConfigDict, YamlConfigSettingsSource, PydanticBaseSettingsSource

        class AppConfig(BaseSettings):
            model_config = SettingsConfigDict(
                yaml_file="config.yaml",
                extra="ignore",
            )

            @classmethod
            def settings_customise_sources(
                cls,
                settings_cls,
                init_settings: PydanticBaseSettingsSource,
                env_settings: PydanticBaseSettingsSource,
                dotenv_settings: PydanticBaseSettingsSource,
                file_secret_settings: PydanticBaseSettingsSource,
            ):
                return (
                    init_settings,
                    env_settings,
                    dotenv_settings,
                    YamlConfigSettingsSource(settings_cls),
                    file_secret_settings,
                )
    """))

    config = tmp_path / "config.py"
    config.write_text(textwrap.dedent("""
        from pydantic import Field, SecretStr
        from .base_config import AppConfig

        class PostgresConfig(AppConfig):
            user: str = Field(alias="POSTGRES_USER")
            password: SecretStr = Field(alias="POSTGRES_PASSWORD")
            host: str = Field(default="localhost", alias="POSTGRES_HOST")
            port: int = Field(default=5432, alias="POSTGRES_PORT")
            db: str = Field(alias="POSTGRES_DB")

        class APIConfig(AppConfig):
            postgres: PostgresConfig = Field(default_factory=PostgresConfig)
    """))

    yaml_file = tmp_path / "config.yaml"
    yaml_file.write_text(textwrap.dedent("""
        postgres:
          user: root_admin
          password: mypassword
          host: localhost
          port: 5432
          db: mydb
    """))

    classes = find_settings_classes([config])
    names = [cls.__name__ for cls in classes]
    assert "APIConfig" in names, f"APIConfig not found, got: {names}"

    all_paths = set()
    for cls in classes:
        all_paths |= get_secret_dotpaths(cls)
    assert "postgres.password" in all_paths, f"postgres.password not in paths: {all_paths}"

    errors = check_files([yaml_file], all_paths)
    assert any("postgres.password" in e for e in errors), f"Expected violation, got: {errors}"
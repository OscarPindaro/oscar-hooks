"""Pre-commit hook that checks YAML config files for pydantic secret field values."""

from __future__ import annotations

import importlib.util
import signal
import sys
import typing
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
import yaml
from pydantic import BaseModel, SecretBytes, SecretStr
from pydantic.fields import FieldInfo
from pydantic_settings import BaseSettings
from rich.console import Console

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

# ---------------------------------------------------------------------------
# Signal handling
# ---------------------------------------------------------------------------


def _handle_sigterm(signum: int, frame: Any) -> None:
    raise SystemExit(EXIT_ERROR)


signal.signal(signal.SIGTERM, _handle_sigterm)

# ---------------------------------------------------------------------------
# Consoles & output helpers
# ---------------------------------------------------------------------------

console = Console()
err_console = Console(stderr=True)

_use_color: bool = False


def print_error(msg: str) -> None:
    if _use_color:
        err_console.print(f"[bold red]{msg}[/bold red]")
    else:
        err_console.print(msg, highlight=False, style=None)


def print_warning(msg: str) -> None:
    if _use_color:
        err_console.print(f"[yellow]{msg}[/yellow]")
    else:
        err_console.print(msg, highlight=False, style=None)


def print_info(msg: str) -> None:
    if _use_color:
        err_console.print(f"[cyan]{msg}[/cyan]")
    else:
        err_console.print(msg, highlight=False, style=None)


# ---------------------------------------------------------------------------
# Secret type detection
# ---------------------------------------------------------------------------

try:
    from pydantic import Secret as _SecretBase

    SECRET_BASES: tuple[type, ...] = (SecretStr, SecretBytes, _SecretBase)
except ImportError:
    SECRET_BASES = (SecretStr, SecretBytes)


def _is_secret_type(annotation: Any) -> bool:
    """Return True if *annotation* is or wraps a Secret type."""
    origin = typing.get_origin(annotation)

    if isinstance(annotation, type) and issubclass(annotation, SECRET_BASES):
        return True

    # Generic alias like Secret[int]
    if origin is not None and isinstance(origin, type) and issubclass(origin, SECRET_BASES):
        return True

    # Annotated[SecretStr, ...]
    if origin is typing.Annotated:
        args = typing.get_args(annotation)
        if args:
            return _is_secret_type(args[0])

    # Optional[SecretStr] i.e. Union[SecretStr, None]
    if origin is typing.Union:
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            if _is_secret_type(arg):
                return True

    return False


# ---------------------------------------------------------------------------
# BaseModel introspection
# ---------------------------------------------------------------------------


def _is_basemodel(annotation: Any) -> bool:
    """Return True if annotation is a BaseModel subclass."""
    origin = typing.get_origin(annotation)

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return True

    if origin is typing.Annotated:
        args = typing.get_args(annotation)
        if args:
            return _is_basemodel(args[0])

    if origin is typing.Union:
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            if _is_basemodel(arg):
                return True

    return False


def _unwrap_model_class(annotation: Any) -> type[BaseModel] | None:
    """Extract the BaseModel subclass from an annotation, if present."""
    origin = typing.get_origin(annotation)

    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        return annotation

    if origin is typing.Annotated:
        args = typing.get_args(annotation)
        if args:
            return _unwrap_model_class(args[0])

    if origin is typing.Union:
        for arg in typing.get_args(annotation):
            if arg is type(None):
                continue
            result = _unwrap_model_class(arg)
            if result is not None:
                return result

    return None


def _get_yaml_keys(field_name: str, field_info: FieldInfo) -> list[str]:
    """Return all possible YAML keys for a field (name + aliases)."""
    keys = [field_name]
    if field_info.alias is not None:
        keys.append(field_info.alias)
    if field_info.validation_alias is not None:
        alias = field_info.validation_alias
        if isinstance(alias, str):
            keys.append(alias)
    return keys


def get_secret_dotpaths(
    model: type[BaseModel],
    prefix: str = "",
    _visited: set[int] | None = None,
) -> set[str]:
    """Recursively collect dot-separated YAML paths that point to secret fields."""
    if _visited is None:
        _visited = set()

    if id(model) in _visited:
        return set()
    _visited.add(id(model))

    paths: set[str] = set()

    for field_name, field_info in model.model_fields.items():
        annotation = field_info.annotation
        yaml_keys = _get_yaml_keys(field_name, field_info)

        if _is_secret_type(annotation):
            for key in yaml_keys:
                full = f"{prefix}{key}" if prefix else key
                paths.add(full)
        elif _is_basemodel(annotation):
            sub_model = _unwrap_model_class(annotation)
            if sub_model is not None:
                for key in yaml_keys:
                    sub_prefix = f"{prefix}{key}." if prefix else f"{key}."
                    paths |= get_secret_dotpaths(sub_model, sub_prefix, _visited)

    return paths


# ---------------------------------------------------------------------------
# Discovery — import Python files and find BaseSettings subclasses
# ---------------------------------------------------------------------------


def _iter_python_files(paths: list[Path]) -> list[Path]:
    """Yield all .py files from a list of file/directory paths."""
    result: list[Path] = []
    for p in paths:
        if p.is_file() and p.suffix == ".py":
            result.append(p)
        elif p.is_dir():
            for child in sorted(p.rglob("*.py")):
                if not child.is_symlink():
                    result.append(child)
    return result


def _import_module_from_path(filepath: Path) -> object | None:
    """Import a Python module from a file path. Returns None on failure."""
    filepath = filepath.resolve()
    module_name = f"_pydantic_yaml_guard_.{filepath.stem}_{id(filepath)}"
    spec = importlib.util.spec_from_file_location(module_name, filepath)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    parent_dir = str(filepath.parent)
    added = False
    if parent_dir not in sys.path:
        sys.path.insert(0, parent_dir)
        added = True
    try:
        spec.loader.exec_module(module)  # type: ignore[union-attr]
    except Exception as e:
        print_warning(f"Could not fully import {filepath}: {e} — attempting partial inspection")
        return module  # <-- return partial module instead of None
    finally:
        if added and parent_dir in sys.path:
            sys.path.remove(parent_dir)
    return module
def find_settings_classes(paths: list[Path]) -> list[type[BaseSettings]]:
    """Return all BaseSettings subclasses found in the given paths."""
    classes: list[type[BaseSettings]] = []
    seen_ids: set[int] = set()

    for pyfile in _iter_python_files(paths):
        module = _import_module_from_path(pyfile)
        if module is None:
            continue
        for attr_name in dir(module):
            obj = getattr(module, attr_name, None)
            if (
                isinstance(obj, type)
                and issubclass(obj, BaseSettings)
                and obj is not BaseSettings
                and id(obj) not in seen_ids
            ):
                seen_ids.add(id(obj))
                classes.append(obj)

    return classes


# ---------------------------------------------------------------------------
# YAML checking
# ---------------------------------------------------------------------------


def flatten_yaml(data: Any, prefix: str = "") -> set[str]:
    """Flatten a parsed YAML dict into a set of dot-separated leaf key paths."""
    paths: set[str] = set()
    if not isinstance(data, dict):
        return paths
    for key, value in data.items():
        full_key = f"{prefix}{key}" if prefix else str(key)
        if isinstance(value, dict):
            paths.update(flatten_yaml(value, f"{full_key}."))
        else:
            paths.add(full_key)
    return paths


def check_yaml_for_secrets(yaml_path: Path, secret_dotpaths: set[str]) -> list[str]:
    """Check a YAML file for secret field paths. Returns list of violations."""
    if not yaml_path.is_file():
        return []

    with open(yaml_path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)

    if data is None:
        return []

    yaml_keys = flatten_yaml(data)
    return sorted(yaml_keys & secret_dotpaths)


# ---------------------------------------------------------------------------
# Core check logic
# ---------------------------------------------------------------------------


def check_files(
    filenames: list[Path],
    all_secret_paths: set[str],
) -> list[str]:
    """Check all YAML files and return a list of error messages."""
    errors: list[str] = []

    for filepath in filenames:
        if filepath.suffix not in (".yaml", ".yml"):
            continue

        try:
            violations = check_yaml_for_secrets(filepath, all_secret_paths)
        except FileNotFoundError:
            errors.append(f"{filepath}: file not found")
            continue
        except PermissionError:
            errors.append(f"{filepath}: permission denied")
            continue
        except UnicodeDecodeError:
            errors.append(f"{filepath}: not a valid UTF-8 text file")
            continue
        except yaml.YAMLError as exc:
            errors.append(f"{filepath}: invalid YAML ({exc})")
            continue

        for v in violations:
            errors.append(f"{filepath}: secret field '{v}' must not be defined in YAML")

    return errors


# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(
    add_completion=False,
    help="Check YAML config files for pydantic secret field values that should not be committed.",
)


@app.callback(invoke_without_command=True)
def main(
    filenames: Annotated[
        Optional[list[Path]],
        typer.Argument(help="YAML files to check (passed by pre-commit)."),
    ] = None,
    config_files: Annotated[
        Optional[list[Path]],
        typer.Option(
            "--config-files",
            help="Python files or directories containing BaseSettings subclasses.",
        ),
    ] = None,
    color: Annotated[
        bool,
        typer.Option("--color", help="Enable colored output."),
    ] = False,
) -> None:
    """Check YAML config files for pydantic secret field values."""
    global _use_color
    _use_color = color

    if not filenames:
        raise typer.Exit(EXIT_OK)

    if not config_files:
        print_error("--config-files is required")
        raise typer.Exit(EXIT_USAGE)

    # 1. Discover all BaseSettings subclasses
    settings_classes = find_settings_classes(config_files)
    if not settings_classes:
        print_warning(f"no BaseSettings subclasses found in {[str(p) for p in config_files]}")
        raise typer.Exit(EXIT_OK)

    # 2. Collect all secret dotpaths across all settings classes
    all_secret_paths: set[str] = set()
    for cls in settings_classes:
        all_secret_paths |= get_secret_dotpaths(cls)

    if not all_secret_paths:
        raise typer.Exit(EXIT_OK)

    # 3. Check each YAML file
    errors = check_files(filenames, all_secret_paths)

    if errors:
        for err in errors:
            print_error(err)
        raise typer.Exit(EXIT_ERROR)

    raise typer.Exit(EXIT_OK)


# ---------------------------------------------------------------------------
# Entrypoint
# ---------------------------------------------------------------------------


def cli() -> None:
    try:
        app()
    except KeyboardInterrupt:
        raise
    except BrokenPipeError:
        sys.stderr.close()
        sys.exit(EXIT_OK)
    except SystemExit as exc:
        sys.exit(exc.code)
    except Exception as exc:
        print_error(f"Unexpected error: {exc}")
        sys.exit(EXIT_ERROR)

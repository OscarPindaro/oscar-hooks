# oscar-hooks

A collection of pre-commit hooks for security and config validation.

## Hooks

### pydantic-yaml-guard

Checks YAML config files for pydantic secret field values that should not be committed.

**Usage in `.pre-commit-config.yaml`:**

```yaml
repos:
  - repo: https://github.com/OscarPindaro/oscar-hooks
    rev: v0.1.0
    hooks:
      - id: pydantic-yaml-guard
        args: ['--config-files', 'src/config.py']
```

**Options:**
- `--config-files`: Python files or directories containing `BaseSettings` subclasses (required)
- `--color`: Enable colored output

### telegram-token-guard

Detects Telegram bot tokens in committed files to prevent accidental token leaks.

**Usage in `.pre-commit-config.yaml`:**

```yaml
repos:
  - repo: https://github.com/OscarPindaro/oscar-hooks
    rev: v0.1.0
    hooks:
      - id: telegram-token-guard
```

**Options:**
- `--color`: Enable colored output

## Installation

Install locally for development:

```bash
uv sync
uv pip install -e .
```

## Running Hooks Manually

```bash
pydantic-yaml-guard config.yaml --config-files src/config.py
telegram-token-guard file.py
```

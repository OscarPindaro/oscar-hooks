"""Pre-commit hook that detects Telegram bot tokens in committed files."""

from __future__ import annotations

import re
import signal
import sys
from pathlib import Path
from typing import Annotated, Any, Optional

import typer
from rich.console import Console

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

EXIT_OK = 0
EXIT_ERROR = 1
EXIT_USAGE = 2

# Telegram bot token: 8-10 digits, colon, 35 alphanumeric/underscore/hyphen chars.
# Uses word boundaries to avoid matching inside larger strings like URLs with
# unrelated colons. Negative lookbehind/lookahead for common non-token contexts.
_TOKEN_PATTERN = re.compile(
    r"(?<![0-9])"  # not preceded by another digit (rejects 11+ digit bot IDs)
    r"[0-9]{8,10}"  # 8-10 digit bot ID
    r":"  # separator
    r"[a-zA-Z0-9_-]{35}"  # 35-char alphanumeric hash
    r"(?![a-zA-Z0-9_-])",  # not followed by valid hash char (rejects 36+ char hashes)
)


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
# Detection logic
# ---------------------------------------------------------------------------


def _mask_token(token: str) -> str:
    """Mask a token for safe display: show first 4 and last 4 chars."""
    if len(token) < 12:
        return "***"
    return f"{token[:4]}...{token[-4:]}"


def check_single_file(filepath: Path, content: str) -> list[str]:
    """Check a single file for Telegram bot tokens.

    Returns error messages in 'file:line: description' format.
    """
    errors: list[str] = []

    for line_num, line in enumerate(content.splitlines(), start=1):
        for match in _TOKEN_PATTERN.finditer(line):
            token = match.group()
            masked = _mask_token(token)
            errors.append(f"{filepath}:{line_num}: Telegram bot token found ({masked})")

    return errors


def check_files(filenames: list[Path]) -> list[str]:
    """Check all files and return a list of error messages."""
    errors: list[str] = []

    for filepath in filenames:
        try:
            content = filepath.read_text(encoding="utf-8")
        except FileNotFoundError:
            errors.append(f"{filepath}: file not found")
            continue
        except PermissionError:
            errors.append(f"{filepath}: permission denied")
            continue
        except UnicodeDecodeError:
            # Binary file — skip silently, tokens are text.
            continue

        file_errors = check_single_file(filepath, content)
        errors.extend(file_errors)

    return errors


# ---------------------------------------------------------------------------
# Typer app
# ---------------------------------------------------------------------------

app = typer.Typer(
    add_completion=False,
    help="Detect Telegram bot tokens in committed files.",
)


@app.callback(invoke_without_command=True)
def main(
    filenames: Annotated[
        Optional[list[Path]],
        typer.Argument(help="Files to check (passed by pre-commit)."),
    ] = None,
    color: Annotated[
        bool,
        typer.Option("--color", help="Enable colored output."),
    ] = False,
) -> None:
    """Check files for Telegram bot tokens that should not be committed."""
    global _use_color
    _use_color = color

    if not filenames:
        raise typer.Exit(EXIT_OK)

    errors = check_files(filenames)

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

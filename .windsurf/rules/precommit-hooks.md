---
trigger: manual
---
You are an expert at writing git hook scripts for use with the **pre-commit** framework (https://pre-commit.com). When I ask you to create a hook, assume it is a **pre-commit** stage hook (runs before the commit is finalized) unless I explicitly say otherwise.

**How the hook will be invoked:**

- The hook executable will be called with any configured `args` first, followed by a list of staged filenames. For example: `your-script --myarg=value file1.py file2.py dir/file3.py`
- If no args are configured, it will just receive the filenames: `your-script file1.py file2.py`
- Only staged files are passed. The pre-commit framework handles stashing unstaged changes so the hook always operates on exactly what will be committed.

**Exit codes and signaling success/failure:**

- Exit with code **0** if the check passes (no issues found).
- Exit with a **nonzero code** (typically 1) if the check fails. This will prevent the commit.
- Alternatively, the hook can **modify files in place** to auto-fix issues (e.g. formatting). The pre-commit framework treats modified files as a failure so the user can review and re-stage.
- Never exit with code 130 (reserved for user interrupt via Ctrl+C) or code 3 (reserved by pre-commit for unexpected errors).

**Output and messaging guidelines:**

- **On success:** Print nothing. Silent success is the convention.
- **On failure:** Print clear, actionable messages to **stderr**. Each message should include:
  - The filename and line number where the issue was found (when applicable).
  - A brief description of the problem.
  - A suggestion for how to fix it, if possible.
  - Use a consistent format such as: `filename:line: description of problem`
- Do not use color codes or ANSI escape sequences — the pre-commit framework manages color output itself.
- Keep output concise. No banners, decorative separators, or verbose progress indicators.

**Script structure best practices:**

- Process every filename passed as an argument (after parsing any flags/args). Iterate over all files rather than stopping at the first failure so the user gets a complete report.
- Collect all errors and report them together, then exit nonzero if any were found.
- Handle missing or unreadable files gracefully with a clear error message rather than a traceback.
- If the hook auto-fixes files (e.g. formatting), write the corrected content back to the original file path and let pre-commit detect the modification.

**Hook metadata (.pre-commit-hooks.yaml):**

When providing a hook, also provide the corresponding `.pre-commit-hooks.yaml` entry with appropriate values for:

- `id`: a short, descriptive kebab-case identifier.
- `name`: a human-readable name shown during execution.
- `entry`: the command to run.
- `language`: the language the hook is written in (e.g. `python`, `node`, `golang`, `rust`, `ruby`, etc.).
- `files`: a regex pattern to match relevant files (e.g. `\\.py$` for Python files).
- `types` or `types_or`: file type filters if more appropriate than a regex pattern.
- `stages`: for linters and formatters, a good default is `[pre-commit, pre-merge-commit, pre-push, manual]`.

Also provide a sample `.pre-commit-config.yaml` snippet showing how a user would configure the hook.

**Language-specific notes:**

- **Python hooks:** The repository must be pip-installable. Use `console_scripts` in `setup.py` or `pyproject.toml` to expose the entry point.
- **Local hooks:** If writing a `repo: local` hook, put all arguments directly in `entry` rather than in `args`, since nothing can override them.

**Other hook stages:**

If I ask for a hook that targets a different stage (e.g. `commit-msg`, `post-checkout`, `pre-push`), adapt accordingly:
- `commit-msg` and `prepare-commit-msg` hooks receive a single filename containing the commit message instead of a list of staged files.
- `post-checkout`, `post-commit`, `post-merge`, `post-rewrite`, and `pre-rebase` hooks do not receive files. They get context through environment variables (e.g. `PRE_COMMIT_FROM_REF`, `PRE_COMMIT_TO_REF`) and must be configured with `always_run: true`.
- `pre-push` hooks receive context through environment variables such as `PRE_COMMIT_REMOTE_NAME` and `PRE_COMMIT_REMOTE_URL`.

Linting & Formatting Guide (Python 3.10+)

This guide standardizes tooling for formatting, import management, and static analysis, and shows how to install, configure, and run them manually or automatically.

## Tools

- **Black**: code formatter (100 char line length)
- **isort**: import sorting and grouping
- **Ruff**: fast linter (pycodestyle, pyflakes), import order, pyupgrade, bugbear, comprehensions
- **Pylint**: comprehensive static analysis (10.00/10 rating required)

## Quick Start

**Run all checks at once:**

```bash
python run_all_tests.py
```

This unified script executes all linters and provides a consolidated report.

## Installation

Use your project interpreter (Python 3.10+). Optionally create a venv first.

```bash
python -m pip install --upgrade pip
python -m pip install -r dev-requirements.txt
```

Or install individually:

```bash
python -m pip install black isort ruff pylint pre-commit
```

## Configuration

The repo contains a `pyproject.toml` with unified settings. Key excerpts:

```toml
[tool.black]
line-length = 100
target-version = ["py310"]

[tool.isort]
profile = "black"
line_length = 100
combine_as_imports = true
force_sort_within_sections = true
known_first_party = ["app", "core", "infrastructure"]

[tool.ruff]
line-length = 100
target-version = "py310"
fix = true

[tool.ruff.lint]
select = ["E", "F", "UP", "B", "C4"]
ignore = ["E501", "I001"]

[tool.ruff.lint.isort]
known-first-party = ["app", "core", "infrastructure"]
combine-as-imports = true

[tool.pylint.main]
py-version = "3.10"
extension-pkg-allow-list = ["PySide6", "PIL"]

[tool.pylint.design]
max-attributes = 24
min-public-methods = 0
max-locals = 55
max-statements = 140
# Allow Windows API structure naming patterns
good-names = ["Data1", "Data2", "Data3", "Data4", "GUID", "SIZE", "BITMAP", "BITMAPINFO", "BITMAPINFOHEADER"]
```

## Manual Run (one-off)

Run these from the repository root:

```bash
# 1) Auto-format and organize imports
python -m black .
python -m isort .

# 2) Lint and auto-fix safe issues
python -m ruff check . --fix

# 3) Deep static analysis (must pass)
python -m pylint app core infrastructure
```

**Or use the unified script:**

```bash
python run_all_tests.py
```

## Git Hooks (pre-commit)

If you use pre-commit, add a `.pre-commit-config.yaml` like:

```yaml
repos:
  - repo: https://github.com/psf/black
    rev: 25.1.0
    hooks:
      - id: black
  - repo: https://github.com/pycqa/isort
    rev: 6.0.1
    hooks:
      - id: isort
  - repo: https://github.com/astral-sh/ruff-pre-commit
    rev: v0.13.0
    hooks:
      - id: ruff
        args: ["--fix"]
  - repo: local
    hooks:
      - id: pylint
        name: pylint
        entry: pylint
        language: system
        args: ["app", "core", "infrastructure"]
        pass_filenames: false
```

Then run:

```bash
python -m pip install pre-commit
pre-commit install
```

## VS Code / Cursor Integration

- Enable Format on Save; set Black as the formatter.
- Configure Ruff extension for on-save linting and quick fixes.
- Install Pylint extension for comprehensive static analysis.

## Code Standards

### Import Policy

- Use absolute imports only (no relative imports).
- Import order: stdlib, third-party, first-party (`app`, `core`, `infrastructure`).
- **isort handles all import sorting** - Ruff's import sorting is disabled to prevent conflicts.
- Import sorting rules: `profile = "black"`, `line_length = 100`, `combine_as_imports = true`.

### Docstrings & Typing Policy

- **All** modules/classes/functions must include Google-style docstrings.
- Use built-in generics: `list[str]`, `dict[str, int]`.
- Prefer `X | None` instead of `Optional[X]`.
- Avoid `Any` unless necessary; explain when used.

### Exceptions & Logging

- Raise specific exceptions; never use bare `except`.
- Use `raise ... from e` to preserve cause when re-raising.
- Use `loguru` with deferred formatting: `logger.info("x={} y={}", x, y)`.

### Windows API Special Cases

- Windows API structures must use PascalCase naming (Data1, Data2, etc.).
- ctypes structures follow target API conventions, not Python conventions.

## Common Commands

```bash
# Run all checks (recommended)
python run_all_tests.py

# Format & lint everything
python -m black . && python -m isort . && python -m ruff check . --fix

# Quick check only
python -m ruff check .

# Deep analysis
python -m pylint app core infrastructure
```

## Troubleshooting

### Import Sorting Conflicts

If you see isort failing after running the full test suite multiple times, this usually indicates a conflict between isort and Ruff's import sorting. The solution is:

1. **Ruff's import sorting is disabled** (`"I"` removed from select, `"I001"` added to ignore)
2. **isort handles all import sorting** with Black-compatible settings
3. Run `python -m isort .` to fix imports, then `python run_all_tests.py` to verify

### Common Issues

- **"isort ." command not found**: Use `python -m isort .` instead
- **Import sorting conflicts**: Ensure Ruff's import sorting is disabled (see configuration above)
- **Multiple runs failing**: Check that all tools are configured consistently

## Requirements

- **All linters must pass** (no disable comments allowed)
- Pylint rating: 10.00/10
- No warnings or errors in any tool
- Use configuration adjustments instead of disable comments

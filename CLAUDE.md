# CLAUDE.md — Python agentic development

## Role

You are a senior Python engineer and agentic-coding assistant. Optimize for correct, maintainable, production-ready code. Prefer simple, explicit solutions over clever abstractions.

## Project conventions

- Python version: 3.11+ unless stated otherwise.
- Use type hints for all public functions, class attributes, and function signatures.
- Use `pydantic` for validated configuration and external data models.
- Use `pathlib.Path` instead of string paths.
- Use `logging` with structured context; never use bare `print()` in production code.
- Prefer dataclasses or Pydantic models over untyped dictionaries.
- Keep modules focused: one responsibility per module.
- Write small, pure functions where practical; isolate I/O, network calls, and side effects.

## Code style

- Follow PEP 8 and use `ruff format` / `ruff check`.
- Maximum line length: 100 characters.
- Use double quotes.
- Prefer f-strings for formatting.
- Use descriptive names; avoid single-letter names except for conventional loop variables.
- Avoid global mutable state.
- Handle expected errors explicitly with specific exception types.
- Never swallow exceptions silently; log or re-raise with context.
- Use `from __future__ import annotations` when forward references improve readability.

## Dependencies

- Use `uv` when available; otherwise use `pip` with a pinned `requirements.txt` or `pyproject.toml`.
- The best libraries are allowed when they measurably improve the quality of the result.
- Justify each dependency in the README (why, alternative considered, cost).
- Pin direct dependencies in project configuration.
- Heavy dependencies (torch, ML models) are optional extras, imported lazily, with a fallback.
- Ask before swapping a major dependency for another.

## Testing

- Use `pytest` with `pytest-asyncio` for async code.
- Create or update tests for every bug fix and meaningful feature.
- Use unit tests for logic, integration tests for external boundaries, and mocks only at true boundaries.
- Do not mock implementation details; mock unstable or external interfaces.
- Test edge cases: empty input, invalid input, network failure, timeout, partial data, and concurrency.
- Run the relevant tests before declaring work complete.

## Git and delivery

- Make small, focused commits with imperative messages.
- Do not commit secrets, credentials, API keys, tokens, or large generated artifacts.
- Before finishing, verify:
  - code runs,
  - tests pass,
  - linting passes,
  - no debug code or temporary files remain,
  - documentation is updated when behavior or interfaces change.

## Agentic workflow

1. Clarify the goal, constraints, and definition of done if the request is ambiguous.
2. Inspect the repository before modifying files; identify existing patterns and conventions.
3. Plan multi-step work before implementing it.
4. Implement incrementally and validate each step.
5. Report changed files, key decisions, risks, and how to run or test the result.

## Python async and concurrency

- Use `asyncio` for I/O-bound concurrency.
- Use `anyio` or `asyncio.TaskGroup` for structured concurrency when appropriate.
- Do not block the event loop with CPU-heavy work or synchronous I/O.
- Use `asyncio.to_thread` for unavoidable blocking calls.
- Use `concurrent.futures` for CPU-bound work when multiprocessing is appropriate.
- Always handle cancellation, timeouts, retries, and cleanup in async workflows.

## Error handling and resilience

- Validate inputs at system boundaries.
- Use retries only for transient failures; use exponential backoff with jitter.
- Set explicit timeouts for network, subprocess, and external-service calls.
- Make failures observable through logs, exceptions, or structured error results.
- Prefer fail-fast for programmer errors and graceful degradation for recoverable operational failures.

## Security

- Never hard-code credentials, secrets, or environment-specific configuration.
- Load secrets from environment variables or a secret manager.
- Validate and sanitize all external input.
- Avoid `eval`, `exec`, `pickle` on untrusted data, and shell injection risks.
- Use parameterized SQL queries; never concatenate SQL.
- Restrict file and subprocess access to intended paths and commands.

## Documentation

- Write concise docstrings for public APIs.
- Explain why, not only what, when the reason is non-obvious.
- Update `README.md` for setup, usage, environment variables, and run commands.
- Add inline comments only where intent or a non-obvious constraint needs explanation.

## Commands

- Format: `uv run ruff format .`
- Lint: `uv run ruff check .`
- Test: `uv run pytest`
- Type check: `uv run mypy .`
- Run app: document the exact command in `README.md`.

## When uncertain

- Ask before destructive operations, dependency changes, schema migrations, or architectural rewrites.
- State assumptions explicitly.
- Prefer a minimal working implementation over speculative features.

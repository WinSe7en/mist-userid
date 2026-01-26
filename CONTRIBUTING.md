# Contributing to Mist User-ID

Thank you for your interest in contributing! This document provides guidelines for contributing to the project.

## Getting Started

1. Fork the repository
2. Clone your fork:
   ```bash
   git clone https://github.com/YOUR_USERNAME/mist-userid.git
   cd mist-userid
   ```
3. Create a virtual environment and install dependencies:
   ```bash
   python -m venv .venv
   source .venv/bin/activate
   pip install -e ".[dev]"
   ```

## Development Workflow

### Running Tests

```bash
# Run all tests
pytest

# Run with coverage
pytest --cov=app

# Run specific test file
pytest tests/test_webhook.py -v
```

### Code Style

This project uses [Ruff](https://github.com/astral-sh/ruff) for linting. Configuration is in `pyproject.toml`.

```bash
# Check for issues
ruff check .

# Auto-fix issues
ruff check --fix .
```

### Running Locally

```bash
# Set required environment variables
export PA_TARGETS=https://your-pa-firewall.example.com
export PA_API_KEY=your-api-key
export MIST_WEBHOOK_SECRET=your-secret
export REDIS_URL=redis://localhost:6379

# Run the API server
uvicorn app.main:app --reload

# In another terminal, run the worker
python -m app.worker
```

## Submitting Changes

1. Create a feature branch:
   ```bash
   git checkout -b feature/your-feature-name
   ```

2. Make your changes and add tests

3. Ensure all tests pass:
   ```bash
   pytest
   ```

4. Commit your changes with a descriptive message:
   ```bash
   git commit -m "Add feature: description of what you added"
   ```

5. Push to your fork and open a Pull Request

## Pull Request Guidelines

- Include tests for new functionality
- Update documentation if needed
- Keep PRs focused on a single change
- Ensure CI passes before requesting review

## Reporting Issues

When reporting issues, please include:

- Operating system and version
- Python version
- Redis version
- Steps to reproduce the issue
- Expected vs actual behavior
- Relevant log output

## Questions?

Open an issue with the "question" label if you need help or clarification.

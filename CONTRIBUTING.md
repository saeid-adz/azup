# Contributing to azup

Thanks for helping! azup is a single-file Python tool (`azup.py`, standard library only, Python 3.8+).

## Quick start

```sh
git clone https://github.com/saeid-adz/azup && cd azup
python azup.py status --offline          # run it
python -m unittest discover -s tests -v  # fast offline tests
```

## Ground rules

- **Changes go through a pull request.** `main` is protected: no direct pushes, CI must pass.
- **No new dependencies.** Standard library only, so the one-line install keeps working everywhere.
- **Never commit secrets** (tokens, keys, tenant or subscription IDs, personal data). Secret scanning
  push protection will block most of them, but check your diff.
- **Be careful with anything that runs commands, writes files or needs elevated rights.** Ask before
  `sudo`, show a plan before changing anything, and keep every behaviour that touches the user's system
  opt-in and reversible.
- **Add a test** for bug fixes (see `tests/`). Platform-specific behaviour you can't test locally:
  please say what you did and didn't verify in the PR.
- **Adding a tool?** One `Tool(...)` entry in `CATALOG`. If it has a winget id, `python tests/check_winget_ids.py`
  verifies it exists. Bump `VERSION` in `azup.py` **and** `pyproject.toml` only when you're releasing.

## Security issues

Don't open a public issue. See [SECURITY.md](SECURITY.md).

By contributing you agree your work is released under the [MIT license](LICENSE) and to follow the
[Code of Conduct](CODE_OF_CONDUCT.md).

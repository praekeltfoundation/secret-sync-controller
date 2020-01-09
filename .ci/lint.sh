#!/bin/sh

targets="src/secret_sync/ tests/ e2e/ setup.py"

# Detect whether any of the checks have failed so we can avoid returning early.
failed=0

flake8 $targets || failed=1
isort -c -rc $targets || failed=1
black --check -l79 $targets || failed=1

exit $failed

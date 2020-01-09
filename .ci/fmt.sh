#!/bin/sh

targets="src/secret_sync/ tests/ e2e/ setup.py"

isort -rc $targets
black -l79 $targets

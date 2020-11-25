#!/bin/sh

# This is a function to better handle paths that may contains whitespace.
fmt() {
    isort "$@"
    black -l79 "$@"
}

fmt src/secret_sync/ tests/ e2e/ setup.py

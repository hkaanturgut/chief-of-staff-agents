#!/usr/bin/env python
"""Thin wrapper over `cos login`, for the pre-flight checklist."""

from cos.cli import app

if __name__ == "__main__":
    app(["login"])

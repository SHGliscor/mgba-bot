#!/usr/bin/env python3
"""Shared bridge façade for modular Gen 3 modes.

The starter bridge client is frozen and hardware-proven. Re-export it rather
than forking a second UDP implementation that could regress reply correlation.
"""
from ruby_starter_hunter import Bridge, DEFAULT_IP, PORT

__all__ = ["Bridge", "DEFAULT_IP", "PORT"]

"""Fail closed on accidental network use throughout the offline test suite."""

import socket

import pytest


@pytest.fixture(autouse=True)
def offline_network_guard(monkeypatch):
    def blocked(*args, **kwargs):
        raise AssertionError("Network is disabled in tests; inject a fixture transport")

    monkeypatch.setattr(socket.socket, "connect", blocked)
    monkeypatch.setattr(socket.socket, "connect_ex", blocked)
    monkeypatch.setenv("MAPS_RESEARCH_OFFLINE", "1")

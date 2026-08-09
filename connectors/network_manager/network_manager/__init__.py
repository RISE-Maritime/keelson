"""Keelson network manager: ping responder, pinger, and network_status publisher."""

from .pingpong import PingResult, compute

__all__ = ["PingResult", "compute"]

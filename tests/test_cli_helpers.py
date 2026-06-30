"""Tests for pure CLI helper functions (date parsing, formatting)."""

import argparse
from datetime import timezone

import pytest

from scrollback.cli import (
    _fmt_cost,
    _fmt_tokens,
    _nonneg_int,
    _parse_date,
    _positive_int,
)


def test_nonneg_int():
    assert _nonneg_int("0") == 0
    assert _nonneg_int("5") == 5
    with pytest.raises(argparse.ArgumentTypeError):
        _nonneg_int("-1")
    with pytest.raises(argparse.ArgumentTypeError):
        _nonneg_int("x")


def test_positive_int():
    assert _positive_int("1") == 1
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("0")
    with pytest.raises(argparse.ArgumentTypeError):
        _positive_int("-3")


def test_parse_date_ymd():
    dt = _parse_date("2026-06-15")
    assert dt.year == 2026 and dt.month == 6 and dt.day == 15
    assert dt.tzinfo is timezone.utc


def test_parse_date_iso_with_z():
    dt = _parse_date("2026-06-15T08:30:00Z")
    assert dt.hour == 8 and dt.minute == 30
    assert dt.tzinfo is not None


def test_parse_date_none():
    assert _parse_date(None) is None
    assert _parse_date("") is None


def test_parse_date_invalid_raises():
    with pytest.raises(argparse.ArgumentTypeError):
        _parse_date("not-a-date")


def test_fmt_tokens():
    assert _fmt_tokens(None) == ""
    assert _fmt_tokens(500) == "500"
    assert _fmt_tokens(12345) == "12.3k"
    assert _fmt_tokens(2_100_000) == "2.1M"


def test_fmt_cost():
    assert _fmt_cost(None) == ""
    assert _fmt_cost(0) == ""          # zero cost is shown blank
    assert _fmt_cost(1.5) == "$1.50"

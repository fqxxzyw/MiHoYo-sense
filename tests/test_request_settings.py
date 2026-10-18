"""只模拟 HTTP 和 sleep，验证请求节奏、超时、限流与配置接线。"""

import importlib.util
import json
from pathlib import Path
import sys

import pytest


@pytest.fixture
def collector():
    path = Path(__file__).resolve().parents[1] / "scripts/01_collect_miyoushe.py"
    spec = importlib.util.spec_from_file_location("http_collector", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def mock_response(c, status, data):
    response = c.requests.Response()
    response.status_code = status
    response.url = c.COMMENTS_URL
    response._content = json.dumps(data, ensure_ascii=False).encode("utf-8")
    return response


def test_wait_before_request_timeout_and_raw_records_preserved(collector, monkeypatch):
    c = collector
    events = []
    data = {"retcode": 0, "data": {"list": [{"id": "same"}, {"id": "same"}, {"content": ""}]}}
    response = mock_response(c, 200, data)
    monkeypatch.setattr(c, "REQUEST_INTERVAL", 3)
    monkeypatch.setattr(c, "REQUEST_TIMEOUT", 12)
    monkeypatch.setattr(c.time, "sleep", lambda seconds: events.append(("sleep", seconds)))

    def get(url, **kwargs):
        events.append(("get", url, kwargs))
        return response

    monkeypatch.setattr(c.requests, "get", get)
    result = c.request_json(c.COMMENTS_URL, {"gids": 6, "post_id": "78487092"})
    assert result == data  # 不做清洗，不因重复或空内容丢记录。
    assert events[0] == ("sleep", 3)
    assert events[1][0] == "get" and events[1][2]["timeout"] == 12
    assert events[1][2]["params"]["gids"] == 6


@pytest.mark.parametrize("status", [403, 429])
def test_refusal_or_rate_limit_stops_after_one_request(collector, monkeypatch, status):
    c = collector
    response = mock_response(c, status, {})
    response.headers["Retry-After"] = "120"
    calls = []
    monkeypatch.setattr(c.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(c.requests, "get", lambda *a, **k: (calls.append(a), response)[1])
    with pytest.raises(c.requests.HTTPError) as error:
        c.request_json(c.COMMENTS_URL, {"gids": 8})
    assert len(calls) == 1 and error.value.response is response
    if status == 429:
        assert "Retry-After=120" in str(error.value)


def test_custom_config_applies_to_actual_discovery_request(collector, monkeypatch, tmp_path):
    c = collector
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({
        "paths": {"raw_dir": str(tmp_path / "raw")},
        "request": {"interval_seconds": 4.5, "timeout_seconds": 9},
    }), encoding="utf-8")
    waits, calls = [], []
    monkeypatch.setattr(c.time, "sleep", waits.append)
    response = mock_response(c, 200, {"retcode": 0, "data": {"recommended_posts": []}})

    def get(url, **kwargs):
        calls.append((url, kwargs))
        return response

    monkeypatch.setattr(c.requests, "get", get)
    monkeypatch.setattr(sys, "argv", ["collector", "--config", str(config), "--discover-posts", "--game", "starrail"])
    c.main()
    assert waits == [4.5] and len(calls) == 1
    assert calls[0][0] == c.WEB_HOME_URL
    assert calls[0][1]["timeout"] == 9 and calls[0][1]["params"]["gids"] == 6
    assert len(list((tmp_path / "raw/miyoushe/starrail/_discovery").glob("*/webHome_page1.json"))) == 1


@pytest.mark.parametrize("request_settings", [
    {"interval_seconds": -1}, {"interval_seconds": None}, {"interval_seconds": True},
    {"interval_seconds": float("nan")}, {"timeout_seconds": 0},
    {"timeout_seconds": float("inf")}, {"timeout_seconds": "20"},
    {"interval": 3},
])
def test_bad_request_config_fails_before_network(collector, tmp_path, request_settings):
    config = tmp_path / "settings.json"
    config.write_text(json.dumps({"request": request_settings}), encoding="utf-8")
    with pytest.raises(ValueError):
        collector.load_config(config)

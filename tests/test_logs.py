import json
import logging

from cli_router.config import RouterConfig
from cli_router.logs import append_run_metrics, configure_logging


def make_config(tmp_path, **defaults):
    return RouterConfig(
        {
            "version": 1,
            "defaults": {"log_dir": str(tmp_path / "logs"), **defaults},
            "tools": {},
            "workflows": {},
        },
        None,
    )


def test_configure_logging_creates_rotating_file_handler_and_is_idempotent(tmp_path):
    config = make_config(tmp_path, log_level="DEBUG")

    logger = configure_logging(config)
    logger.debug("diagnostic detail")
    second = configure_logging(config)
    second.info("one handler only")

    log_path = tmp_path / "logs" / "cli-router.log"
    assert log_path.exists()
    assert logger is second
    assert logger.level == logging.DEBUG
    assert len([handler for handler in logger.handlers if getattr(handler, "_cli_router_log_handler", False)]) == 1
    text = log_path.read_text(encoding="utf-8")
    assert "diagnostic detail" in text
    assert text.count("one handler only") == 1


def test_append_run_metrics_writes_json_lines(tmp_path):
    config = make_config(tmp_path)
    metrics = {"run_id": "2026-07-09T10-00-00", "total_duration_seconds": 1.25}

    append_run_metrics(config, metrics)

    lines = (tmp_path / "logs" / "metrics.jsonl").read_text(encoding="utf-8").splitlines()
    assert [json.loads(line) for line in lines] == [metrics]

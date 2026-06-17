from app.tui.screens.dashboard import format_dashboard_model_error


def test_dashboard_model_error_formatter_separates_latest_and_historical_errors():
    historical_lines = format_dashboard_model_error(
        {
            "run_id": "run_old",
            "created_at": "2026-06-18T10:00:00Z",
            "is_latest_run": False,
            "error_type": "authentication_error",
            "error_code": "auth_unavailable",
            "message": "expired token",
        }
    )
    latest_lines = format_dashboard_model_error(
        {
            "run_id": "run_latest",
            "created_at": "2026-06-18T10:05:00Z",
            "is_latest_run": True,
            "message": "current run failed",
        }
    )

    assert historical_lines[0] == "Latest Historical Model Error:"
    assert "历史错误，不代表当前模型健康状态。" in historical_lines
    assert "authentication_error/auth_unavailable" in historical_lines[1]
    assert latest_lines[0] == "Latest Run Failed With Model Error:"
    assert "历史错误，不代表当前模型健康状态。" not in latest_lines

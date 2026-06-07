# AI Changelog

## [2026-06-07]

### Added
- Added TikTok service availability checker (`clash_auto_switch/core/services/tiktok.py`) based on the shell script.
- Added unit tests for the TikTok region parser and service registry registration in `tests/test_service_tester.py`.
- Documented TikTok service support and active connection rules in `README.md`.

### Fixed
- Fixed pre-existing unit test failures in `tests/test_config.py` and `tests/test_proxy_switcher.py` where `AppConfig` was incorrectly initialized with a deprecated/removed `MonitoringConfig` argument.

# Changelog

All notable changes to API Checker are documented here.

## [0.3.0] - 2026-06-18

### Added

- Provider endpoint and API key verification before saving.
- Persistent internal notes for API providers.
- Dashboard provider breakdown and automatic-monitoring summary.

### Changed

- Improved dashboard provider ordering, icons, typography, and responsive layout.
- Added API key visibility controls and refined the provider form layout.

### Fixed

- Trimmed surrounding whitespace from API keys before verification and monitoring.

## [0.2.1] - 2026-06-18

### Added

- Persistent global automatic-monitoring setting.
- Settings and About pages.
- Model search, pagination, capability indicators, and compact tables.
- Release preparation and version validation tooling.

### Changed

- Improved provider controls, button feedback, and launcher process detection.

### Fixed

- Prevented template/backend version mismatch from causing pagination errors.

## [0.2.0] - 2026-06-18

### Added

- OpenAI-compatible and Anthropic-compatible provider detection.
- Automatic discovery and scheduled refresh of available models.
- Encrypted optional API keys, provider history, and capability inference.

## [0.1.0] - 2026-06-18

### Added

- Initial FastAPI dashboard, SQLite storage, scheduler, and SSH-tunnel deployment.

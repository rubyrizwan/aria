# Changelog

All notable changes to API Checker are documented here.

## [0.4.0] - 2026-06-19

### Added

- Provider information modal with decrypted API key reveal and copy actions.
- API key labels, masked credentials on provider details, and a dedicated favicon.
- Five-minute to six-hour monitoring interval options with a 60-minute default.

### Changed

- Improved provider tables with row numbers, totals, responsive columns, and reusable modals.
- Moved provider deletion to the detail header and removed typed-name confirmation.
- Simplified the dashboard provider table and aligned overview icon sizing.

### Fixed

- Preserved compatibility and discovered models when editing provider metadata.
- Kept Save provider functional after connection verification.

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

# Changelog

All notable changes to ARIA are documented here.

## [1.0.3] - 2026-06-21

### Added

- Dedicated Docker deployment reference documentation at `docs/DOCKER.md` covering image architecture, compose configuration, environment variables, manual build, operations, security, and troubleshooting.
- Cross-reference to `docs/DOCKER.md` from the Docker section in README.

## [1.0.2] - 2026-06-20

### Changed

- Describe this release before committing.

## [Unreleased]

### Added

- Optional Docker image and Docker Compose deployment with automatic Alembic migrations.
- Non-root container execution, health checks, read-only root filesystem, dropped Linux capabilities, and persistent SQLite storage.
- Configurable application bind address through `APICHECKER_HOST`.
- Container deployment, SSH tunnel, backup, restart, and upgrade documentation.

## [1.0.1] - 2026-06-20

### Fixed

- Updated launcher PID discovery to recognize an existing ARIA process after the repository directory is renamed or moved.
- Prevented a stale process from retaining port 8000 while loading templates from a removed repository path.
- Added a regression test for relocated launcher-managed processes.

## [1.0.0] - 2026-06-20

### Stable Release

- Declared ARIA ready for stable private deployment through a localhost listener and SSH tunnel.
- Consolidated OpenAI-compatible and Anthropic-compatible provider detection, encrypted API credentials, model discovery, and per-model inference access testing.
- Included inference history, operational charts, provider health monitoring, configurable scheduled checks, optional scheduled inference retests, and data retention controls.
- Included the English `scripts/aria` launcher, SQLite online backups, migration tooling, log rotation support, and optional `systemd --user` deployment.
- Preserved the existing `APICHECKER_*` environment variables and storage paths for backward compatibility.

### Release Notes

- This release establishes the supported baseline before optional Docker deployment support is introduced.
- A single ARIA process or replica is required because the scheduler runs inside the web application process.
- Token usage accounting is not included in version 1.0.0.

## [0.4.3] - 2026-06-19

### Changed

- Renamed the user-facing product from API Checker to ARIA.
- Added the expanded product name: API Reliability & Inference Analyzer.
- Updated browser titles, About page, launcher output, service descriptions, documentation, and application metadata.
- Renamed the launcher to `scripts/aria` and expanded its English status, health, uptime, log, and SSH tunnel information.
- Preserved existing `APICHECKER_*` variables, package names, service filenames, and database paths for compatibility.

## [0.4.2] - 2026-06-19

### Added

- Unified Available Models catalog across providers with filters, sorting, details, copy actions, freshness, latency, and provider coverage.
- Append-only inference history, dashboard inference charts, provider attention indicators, and inference-focused activity views.
- Configurable runtime settings for retention, concurrency, provider defaults, pagination, and opt-in scheduled inference retests.
- Online SQLite backup script, daily `systemd --user` backup timer, user-service installer, and launcher logrotate configuration.
- Sidebar server status, bind address, service manager, and protected restart control.

### Changed

- Redesigned Dashboard, Providers, Settings, and About pages for denser operational workflows.
- Combined recent inference and provider checks after the dashboard provider table.
- Removed the dashboard's 30-second full-page refresh.

### Security

- Protected dashboard restart requests with a master-key-derived request token.
- Kept service and launcher deployments bound to the loopback interface.

### Fixed

- Preserved provider compatibility and model inventory during metadata-only edits.
- Stored and displayed provider notes, API key labels, encrypted credentials, inference latency, and historical model access results consistently.

## [0.4.1] - 2026-06-19

### Added

- Per-model inference access tests with live progress logs and result summaries.
- Capability and inference-access filters for discovered models.
- Provider monitoring state, interval, and inference latency statistics.

### Changed

- Renamed the manual provider check action to Load models and placed it before model access testing.
- Disabled model access testing until models have been loaded.

### Fixed

- Persisted the latest average inference latency after each completed model test.

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

## [0.3.1] - 2026-06-18

### Fixed

- Restored Alembic revision `20260618_0005` so databases previously migrated for
  multiple API keys remain recognized when running the `main` branch.

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

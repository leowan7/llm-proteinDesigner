# Changelog

All notable changes to `bindwave` are documented here. Format follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/); this project adheres to
[Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added
- Package skeleton + public surface contract (Phase 13, Plan 13-02): importable
  `Client`, `AsyncClient`, exception hierarchy, and type placeholders.

### Notes
- Real client + resource methods land in Plan 13-04; typed models + pagination in
  Plan 13-05. Until then the placeholder classes raise `NotImplementedError`.

# Fournos Launcher Changelog

## 2026-06-24 - Job Management & Notification Improvements

### Changed
- **Job Storage**: `submit_and_wait` now saves fjobs in well-known location for better tracking
- **Clean Annotations**: Removed unnecessary annotations from `submit_and_wait` operations
- **Enhanced Notifications**: Custom notifications include MLflow URLs and log file links

### Benefits
- Better job artifact management
- Cleaner job metadata
- Improved notification content with direct links
- Easier access to job outputs and logs

### Files Modified
- Toolbox `submit_and_wait` operations - job storage and annotations
- Orchestration `submit` - notification generation with MLflow/log links

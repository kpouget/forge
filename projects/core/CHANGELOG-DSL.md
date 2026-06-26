# DSL Framework Changelog

## 2026-06-24 - Context Persistence & Logging Improvements

### New Features
- **Context Persistence**: DSL runtime automatically saves final shared context on successful completion
  - **Location**: `{artifact_dir}/_meta/context.yaml`
  - **Content**: All context variables set by tasks during execution
  - **Format**: Clean YAML with timestamp and Path objects converted to strings

### Changed
- **Reduced Logging Verbosity**: Removed context output from exception traces
  - Context no longer printed to console on task failures
  - Exception traces now show full stack traces for better debugging
  - Context still preserved in `context.yaml` on successful completion

### New Artifacts
```
{artifact_dir}/
└── _meta/
    ├── metadata.yaml           # Execution metadata
    ├── restart.sh             # Restart script
    ├── env.txt                # Environment variables
    └── context.yaml           # Final task context (NEW)
```

### Context File Format
```yaml
final_context:
  cache_spec:
    source_uri: "hf://openai/gpt-oss-120b"
    pvc_name: "llm-d-model-openai-gpt-oss-120b-7ab79ddecd"
  artifact_dir: "/path/to/artifacts"
timestamp: "2026-06-24T10:30:45.123456"
```

### Benefits
- **Better Debugging**: Inspect final task state and variable values
- **Cleaner Console Output**: Reduced verbosity while preserving diagnostic information
- **Context Tracking**: See what values tasks set during execution
- **Automatic**: No configuration required, works for all DSL executions

### Files Modified
- `projects/core/dsl/runtime.py` - Added `_generate_context_file()` function
- `projects/core/dsl/toolbox.py` - Removed context logging from exception handler

### Migration Notes
- **No Breaking Changes**: All existing DSL functionality preserved
- **Automatic Benefits**: Context persistence activates immediately for all projects
- **Backward Compatible**: Existing toolbox commands continue to work unchanged

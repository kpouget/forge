# KServe Project Changelog

## 2026-06-24 - Deployment Improvements & Agent Integration

### `deploy_llmisvc` Toolbox Enhancements
- **Reduced Pod Wait Time**: Now waits only 1 minute for pods to appear (improved efficiency)
- **Service Description Capture**: Automatically captures LLMISV description for troubleshooting
- **Agent Review Support**: Generates `AGENT.md` files to help failure analysis agents understand deployment issues
- **Post-Mortem Analysis**: `AGENT.md` includes:
  - Service details and deployment context
  - Available artifact files for analysis  
  - Structured analysis instructions for AI agents
  - References to K8s events and ReplicaSet status

#### Files Modified
- `projects/kserve/toolbox/deploy_llmisvc/main.py` - Timeout reduction and description capture
- `projects/kserve/toolbox/deploy_llmisvc/on_failure_helpers.py` - Agent review support (NEW)

### `prepare_hf_model_cache` Toolbox Improvements  
- **Improved Error Handling**: No longer retries on missing job scenarios
- **Better Resource Management**: More efficient handling of job lifecycle

#### Files Modified
- `projects/kserve/toolbox/prepare_hf_model_cache/main.py` - Error handling improvements

### Benefits
- **Faster Feedback**: Reduced wait times provide quicker deployment results
- **Better Debugging**: Enhanced artifact capture and structured failure context
- **AI-Friendly**: Agent-optimized failure information for automated analysis
- **More Reliable**: Improved error handling prevents unnecessary retries

### Artifact Structure
```
{artifact_dir}/
├── AGENT.md                     # Agent analysis context (NEW)
├── artifacts/
│   ├── llmisv_description.txt   # Service K8s description (NEW)  
│   └── replicaset_description.txt
└── task.log
```

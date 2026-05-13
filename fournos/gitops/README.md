# Forge GitOps Configuration

This directory contains the ArgoCD GitOps configuration for deploying FORGE components including images and pipelines.

## Structure

```
gitops/
├── applications/           # ArgoCD Application manifests
│   ├── forge-development.yaml
│   ├── forge-production.yaml
│   └── kustomization.yaml
├── base/                  # Base Kubernetes manifests
│   ├── images/            # Image build configurations
│   │   ├── imagestream.yaml
│   │   ├── build.yaml
│   │   ├── buildrun.yaml
│   │   └── kustomization.yaml
│   ├── workflows/         # Tekton pipelines and tasks
│   │   ├── task-forge-step.yaml
│   │   ├── pipeline-full.yaml
│   │   ├── pipeline-test-only.yaml
│   │   ├── pipeline-replot.yaml
│   │   └── kustomization.yaml
│   └── kustomization.yaml
└── overlays/             # Environment-specific configurations
    ├── development/
    │   └── kustomization.yaml
    └── production/
        └── kustomization.yaml
```

## Components

### Images
- **ImageStream**: Manages forge-core image tags and references
- **Build**: Shipwright build configuration for building forge image from source
- **BuildRun**: Triggers the build process

### Workflows  
- **Task**: `forge-step` - Reusable Tekton task for executing forge commands
- **Pipelines**: Multiple pipeline variants:
  - `forge-full`: Complete pipeline with pre-cleanup, prepare, test, export-artifacts, and post-cleanup
  - `pipeline-test-only`: Test execution only
  - `pipeline-replot`: Replotting functionality

## Environments

### Development (`fournos-dev` namespace)
- Auto-sync enabled with prune and self-heal
- Deploys from main branch
- More aggressive sync policies for faster iteration

### Production (`fournos-prod` namespace)  
- Conservative sync policies (no auto-prune)
- Manual approval recommended for critical changes
- Extended revision history

## Deployment

### Option 1: Deploy Applications Directly
```bash
oc apply -k gitops/applications/
```

### Option 2: Deploy to Specific Environment
```bash
# Development
oc apply -k gitops/overlays/development/

# Production  
oc apply -k gitops/overlays/production/
```

### Option 3: Manual Application Creation
```bash
oc apply -f gitops/applications/forge-development.yaml
oc apply -f gitops/applications/forge-production.yaml
```

## Integration with Fournos

This GitOps configuration replaces the forge-specific manifests previously stored in the `fournos/config/forge/` directory. The manifests have been adapted to:

1. Use dynamic namespace placeholders that get resolved by kustomize overlays
2. Follow GitOps best practices with environment-specific configurations
3. Include proper labeling and annotations for resource management
4. Support both automated and manual deployment workflows

## Migration Notes

The following changes were made from the original fournos configuration:
- Moved from `fournos/config/forge/` to `forge/gitops/`
- Added kustomization files for proper resource management
- Created environment-specific overlays for dev/prod deployments
- Updated image references to use dynamic namespace resolution
- Added ArgoCD Application manifests for automated GitOps deployment

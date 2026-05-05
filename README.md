# FORGE: Framework for Orchestrating Runtime Gen-AI Experiments

FORGE is a comprehensive test harness designed for **CI-first, reproducible and observable performance & scale testing** of AI/ML workloads, specifically targeting OpenShift platforms. Developed and maintained by the **Red Hat PSAP (Performance and Scale for AI Platforms)** team.

## 🎯 Purpose

FORGE enables systematic performance and scale testing of AI/ML workloads with:

- **Reproducible testing**: Consistent test environments and methodologies
- **Observable results**: Comprehensive metrics collection and visualization
- **CI/CD integration**: Automated testing pipelines for continuous validation
- **Scalability analysis**: Performance characteristics across different scales
- **OpenShift optimization**: Tailored for container orchestration platforms

## 🏗️ Architecture

FORGE works in cooperation with [Fournos](https://github.com/openshift-psap/fournos) to provide a complete testing ecosystem for AI workloads.

### Core Components

- **`core`**: Fundamental framework components (DSL, launcher, CI entrypoint, notifications)
- **`caliper`**: Artifact post-processing engine for parsing, visualization, and KPI analysis
- **`fournos_launcher`**: Integration with Fournos for orchestration

## 🚀 Quick Start

### Installation

```bash
# Clone the repository
git clone https://github.com/openshift-psap/forge.git
cd forge

# Install core dependencies
pip install -e .

# Install with optional backends
pip install -e '.[caliper]'

# Install development dependencies
pip install -e '.[dev]'
```

### Container Development Environment

FORGE provides a containerized development environment using the forge_launcher:

```bash
# Check environment status
./bin/forge_launcher status

# Build the FORGE container image
./bin/forge_launcher build

# Create/recreate the development container
./bin/forge_launcher recreate

# Enter the containerized development environment
./bin/forge_launcher enter

# Run commands directly in the container
./bin/forge_launcher enter "python -m pytest"
```

### Basic Usage

```bash
./projects/skeleton/orchestration/ci.py --help
./projects/skeleton/orchestration/ci.py prepare
./projects/skeleton/orchestration/ci.py test
```

```bash
./projects/skeleton/orchestration/cli.py --help
./projects/skeleton/orchestration/cli.py precleanup
./projects/skeleton/orchestration/cli.py prepare
./projects/skeleton/orchestration/cli.py test
```

## 📊 Key Features

### Core Framework
- **DSL (Domain Specific Language)**: Test definition and configuration
- **CI Integration**: Continuous integration entrypoints
- **Notification System**: Alert and reporting mechanisms
- **Image Management**: Container orchestration support

### Caliper - Artifact Processing
- **Parse**: Traverse and parse test artifact trees
- **Visualize**: Generate plots and HTML reports from unified models
- **KPI Management**: Generate, import, export, and analyze key performance indicators
- **Multi-backend Support**: Export to OpenSearch, S3, and MLflow
- **AI Evaluation**: Export AI evaluation metrics in JSON format

## 📁 Project Structure

```
forge/
├── projects/                    # Main project modules
│   ├── caliper/                # Artifact post-processing
│   ├── core/                   # Framework core components
│   ├── matrix_benchmarking/    # Performance dashboards
│   ├── llm_d/                  # LLM deployment tools
│   ├── fournos_launcher/       # Fournos integration
│   └── skeleton/               # Project templates
├── docs/                       # Documentation
├── specs/                      # Technical specifications
├── bin/                        # Executable scripts
├── tests/                      # Test suites
└── vaults/                     # Configuration vaults
```

## 🔧 Configuration

### Dependencies

**Core Requirements:**
- Python 3.12+
- Click (CLI framework)
- PyYAML (configuration)
- JSONSchema (validation)
- Pydantic (data models)

**Optional Backends:**
- **OpenSearch**: For KPI indexing and search (`opensearch-py`)
- **MLflow**: For experiment tracking (`mlflow`)

**Visualization:**
- **Plotly/Dash**: Interactive dashboards
- **Pandas**: Data processing

## 🧪 Testing

```bash
# Run unit tests
pytest projects/core/tests/

# Run with coverage
pytest --cov=projects projects/core/tests/

# Run integration tests
pytest -m integration

# Run performance tests (slow)
pytest -m slow
```

## 🤝 Contributing

### Development Setup

```bash
# Install pre-commit hooks
pre-commit install

# Run code formatting
ruff format projects/
ruff check projects/
```

### Code Style
- **Ruff**: An extremely fast Python linter and code formatter
- **Target**: Python 3.12+ compatibility

## 📖 Documentation

- **Specifications**: `/specs/` - Detailed technical specifications
- **Quickstart Guides**: `/specs/*/quickstart.md` - Getting started guides
- **API Documentation**: Auto-generated from docstrings
- **Documentation**: `/docs/` - Usage examples and tutorials

## 🔗 Related Projects

- **[Fournos](https://github.com/openshift-psap/fournos)**: Job orchestration and execution platform

## 📄 License

Licensed under the Apache License 2.0. See [LICENSE](LICENSE) for details.

## 👥 Team & Support

**Maintained by**: Red Hat PSAP Team (`psap@redhat.com`)

**Key Contributors**:
- Kevin Pouget (@kpouget)
- Alberto Perdomo (@albertoperdomo2)
- See [OWNERS](OWNERS) for the complete list

For issues, feature requests, or contributions, please use the GitHub issue tracker.

---

**Keywords**: `testing` `performance` `scale` `openshift` `ai` `mlops` `benchmarking` `ci-cd`

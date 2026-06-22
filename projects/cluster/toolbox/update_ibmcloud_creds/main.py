#!/usr/bin/env python3

"""
IBM Cloud Credentials Update Toolbox

Updates IBM Cloud API credentials in multiple cluster secrets used by various operators.
Handles the cloud controller manager, CSI drivers, and machine API credentials.
"""

import logging
import tempfile
from pathlib import Path

from projects.core.dsl import entrypoint, execute_tasks, shell, task
from projects.core.dsl.utils.k8s import oc

logger = logging.getLogger("TOOLBOX")

# Secret configurations for different namespaces and operators
SECRETS_CONFIG = [
    {
        "name": "ibm-cloud-credentials",
        "namespace": "openshift-cloud-controller-manager",
        "description": "Cloud Controller Manager credentials",
    },
    {
        "name": "ibm-cloud-credentials",
        "namespace": "openshift-cluster-csi-drivers",
        "description": "CSI Drivers credentials",
    },
    {
        "name": "ibmcloud-credentials",
        "namespace": "openshift-machine-api",
        "description": "Machine API credentials",
    },
]


@entrypoint
def run(
    api_key_file: str,
    *,
    dry_run: bool = False,
) -> int:
    """
    Update IBM Cloud API credentials across cluster secrets

    Args:
        api_key_file: Path to file containing the IBM Cloud API key
        dry_run: Preview changes without applying them (default: False)
    """

    execute_tasks(locals())
    return 0


@task
def setup_directories(args, ctx):
    """Create command source and artifact directories"""

    shell.mkdir("src")
    shell.mkdir("artifacts")
    return "Prepared directories for credential update operation"


@task
def validate_parameters(args, ctx):
    """Validate command parameters and API key file"""

    api_key_file = Path(args.api_key_file)

    if not api_key_file.exists():
        raise FileNotFoundError(f"API key file not found: {args.api_key_file}")

    if not api_key_file.is_file():
        raise ValueError(f"API key path is not a file: {args.api_key_file}")

    # Store absolute path for later use
    ctx.api_key_file_path = api_key_file.resolve()

    return f"Validated API key file: {ctx.api_key_file_path}"


@task
def read_api_key(args, ctx):
    """Read and validate the API key from file"""

    try:
        with open(ctx.api_key_file_path) as f:
            api_key = f.read().strip()
    except Exception as e:
        raise RuntimeError(f"Failed to read API key file: {e}") from None

    if not api_key:
        raise ValueError("API key file is empty")

    if len(api_key) < 10:  # Basic validation - IBM Cloud API keys are much longer
        raise ValueError("API key appears to be too short - check file contents")

    logger.info(f"Successfully read API key (length: {len(api_key)} characters)")
    return "API key loaded and validated"


@task
def verify_ibmcloud_platform(args, ctx):
    """Verify the cluster is running on IBM Cloud"""

    result = oc(
        "get",
        "infrastructure",
        "cluster",
        "-o",
        "jsonpath={.status.platformStatus.type}",
        check=False,
    )

    if not result.success:
        raise RuntimeError(
            "Failed to detect cluster platform. Ensure you are connected to an OpenShift cluster."
        )

    platform = result.stdout.strip().lower()

    if platform != "ibmcloud":
        raise RuntimeError(
            f"This script is only for IBM Cloud clusters. Detected platform: {platform}"
        )

    logger.info(f"Verified cluster platform: {platform}")
    return "Confirmed cluster is running on IBM Cloud"


@task
def check_namespaces_exist(args, ctx):
    """Verify all target namespaces exist"""

    missing_namespaces = []

    for secret_config in SECRETS_CONFIG:
        namespace = secret_config["namespace"]
        result = oc("get", "namespace", namespace, check=False, log_stdout=False)

        if not result.success:
            missing_namespaces.append(namespace)

    if missing_namespaces:
        raise RuntimeError(f"Missing namespaces: {', '.join(missing_namespaces)}")

    return f"Verified {len(SECRETS_CONFIG)} target namespaces exist"


@task
def update_secrets(args, ctx):
    """Update or create the IBM Cloud credential secrets"""

    if args.dry_run:
        logger.info("DRY RUN MODE - No actual changes will be made")

    updated_secrets = []

    # Read API key once for all secret updates
    try:
        with open(ctx.api_key_file_path) as f:
            api_key = f.read().strip()
    except Exception as e:
        raise RuntimeError(f"Failed to read API key file: {e}") from None

    credentials_env_content = f"IBMCLOUD_AUTHTYPE=iam\nIBMCLOUD_APIKEY={api_key}"

    for secret_config in SECRETS_CONFIG:
        secret_name = secret_config["name"]
        namespace = secret_config["namespace"]
        description = secret_config["description"]

        logger.info(f"Updating {description} secret: {namespace}/{secret_name}")

        if args.dry_run:
            logger.info(f"Would update secret {namespace}/{secret_name} with new credentials")
            updated_secrets.append(f"{namespace}/{secret_name}")
            continue

        # Delete existing secret if it exists (to avoid merge conflicts)
        oc(
            "delete",
            "secret",
            secret_name,
            "-n",
            namespace,
            "--ignore-not-found=true",
            log_stdout=False,
        )

        # Create temporary files for secret data to avoid logging sensitive data
        api_key_file = None
        credentials_file = None
        try:
            api_key_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".key")
            api_key_file.write(api_key)
            api_key_file.flush()
            api_key_file.close()

            credentials_file = tempfile.NamedTemporaryFile(mode="w", delete=False, suffix=".env")
            credentials_file.write(credentials_env_content)
            credentials_file.flush()
            credentials_file.close()

            # Create secret using --from-file to avoid exposing data in command line
            result = oc(
                "create",
                "secret",
                "generic",
                secret_name,
                f"--from-file=ibmcloud_api_key={api_key_file.name}",
                f"--from-file=ibm-credentials.env={credentials_file.name}",
                "-n",
                namespace,
                log_stdout=False,
            )
        finally:
            # Clean up temporary files
            if api_key_file and hasattr(api_key_file, "name"):
                Path(api_key_file.name).unlink(missing_ok=True)
            if credentials_file and hasattr(credentials_file, "name"):
                Path(credentials_file.name).unlink(missing_ok=True)

        if not result.success:
            raise RuntimeError(
                f"Failed to create secret {namespace}/{secret_name}: {result.stderr}"
            )

        # Log success without sensitive data
        logger.info(f"Successfully updated secret {namespace}/{secret_name}")
        updated_secrets.append(f"{namespace}/{secret_name}")

    ctx.updated_secrets = updated_secrets

    if args.dry_run:
        return f"DRY RUN: Would update {len(updated_secrets)} secrets"
    else:
        return f"Successfully updated {len(updated_secrets)} secrets"


@task
def verify_secret_updates(args, ctx):
    """Verify the updated secrets contain the expected fields"""

    if args.dry_run:
        return "DRY RUN: Skipping verification"

    verification_results = []

    for secret_config in SECRETS_CONFIG:
        secret_name = secret_config["name"]
        namespace = secret_config["namespace"]

        # Check that secret exists and has the required keys
        result = oc(
            "get",
            "secret",
            secret_name,
            "-n",
            namespace,
            "-ojsonpath={.data}",
            check=False,
            log_stdout=False,
        )

        if not result.success:
            raise RuntimeError(f"Failed to verify secret {namespace}/{secret_name}")

        # Check for required keys (they will be base64 encoded in the output)
        if "ibmcloud_api_key" not in result.stdout:
            raise RuntimeError(f"Secret {namespace}/{secret_name} missing ibmcloud_api_key field")

        if "ibm-credentials.env" not in result.stdout:
            raise RuntimeError(
                f"Secret {namespace}/{secret_name} missing ibm-credentials.env field"
            )

        verification_results.append(f"{namespace}/{secret_name}")

    return f"Verified {len(verification_results)} secrets contain required fields"


@task
def generate_summary(args, ctx):
    """Generate operation summary"""

    if args.dry_run:
        return "DRY RUN: Summary generated in logs"

    # Generate summary
    summary_file = args.artifact_dir / "artifacts" / "update_summary.txt"
    with open(summary_file, "w") as f:
        f.write("IBM Cloud Credentials Update Summary\n")
        f.write("=" * 40 + "\n\n")
        f.write("Updated secrets:\n")
        for secret_info in ctx.updated_secrets:
            f.write(f"  - {secret_info}\n")

    return "Generated operation summary"


if __name__ == "__main__":
    run.main()

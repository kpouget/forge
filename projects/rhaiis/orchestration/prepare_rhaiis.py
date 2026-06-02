import logging

from projects.core.library import config, run

logger = logging.getLogger(__name__)


def prepare():
    ns = config.project.get_config("rhaiis.namespace")
    deploy_cfg = config.project.get_config("rhaiis.deploy")
    logger.info(f"Preparing namespace {ns} for rhaiis benchmarks")

    result = run.run("oc whoami", capture_stdout=True, check=False)
    if result.returncode != 0:
        raise RuntimeError("Cannot connect to cluster")
    logger.info(f"Connected to cluster as {result.stdout.strip()}")

    result = run.run(f"oc get namespace {ns}", check=False)
    if result.returncode != 0:
        run.run(f"oc create namespace {ns}")
        logger.info(f"Created namespace {ns}")
    else:
        logger.info(f"Namespace {ns} already exists")

    sa_name = deploy_cfg.get("service_account_name", "")
    if sa_name:
        result = run.run(f"oc get serviceaccount {sa_name} -n {ns}", check=False)
        if result.returncode != 0:
            run.run(f"oc create serviceaccount {sa_name} -n {ns}")
            logger.info(f"Created service account {sa_name}")
        else:
            logger.info(f"Service account {sa_name} already exists")

    secret_name = deploy_cfg.get("image_pull_secret", "")
    if secret_name:
        result = run.run(f"oc get secret {secret_name} -n {ns}", check=False)
        if result.returncode == 0:
            logger.info(f"Image pull secret {secret_name} exists")
        else:
            logger.warning(
                f"Image pull secret {secret_name} not found in {ns} — "
                "deployment may fail if images require authentication"
            )


def cleanup():
    ns = config.project.get_config("rhaiis.namespace")
    logger.info(f"Cleaning up rhaiis benchmark resources in {ns}")

    run.run(
        f"oc delete job --all -n {ns} --ignore-not-found",
        check=False,
    )
    run.run(
        f"oc delete pod --all -n {ns} --ignore-not-found",
        check=False,
    )
    logger.info("Cleanup complete")

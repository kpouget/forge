import logging

from projects.core.library import config

logger = logging.getLogger(__name__)


def prepare():
    ns = config.project.get_config("rhaiis.namespace")
    logger.warning(f"Hello prepare {ns}")


def cleanup():
    logger.warning("Hello cleanup")

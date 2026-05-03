"""
Module: [MODULE_NAME]

Purpose: [ONE SENTENCE DESCRIPTION]

Dependencies:
- [DEPENDENCY 1]
- [DEPENDENCY 2]

Usage:
    from [module_name] import [ModuleClass]

    module = [ModuleClass](config)
    result = module.do_something(input)

Status: EXPERIMENTAL | BETA | STABLE
Version: 0.1.0
Last Updated: YYYY-MM-DD
"""

import logging
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)


class ModuleClass:
    """
    [One-paragraph description of what this class does and why it exists.]

    Attributes:
        config: Configuration dictionary (see __init__ for required/optional keys).
    """

    def __init__(self, config: Dict[str, Any]):
        """
        Initialize module.

        Args:
            config: Configuration dictionary.
                Required keys: key1, key2
                Optional keys: key3 (default: None)
        """
        self.config = config
        self._validate_config()
        self._initialize()

    def _validate_config(self) -> None:
        required = ["key1", "key2"]
        for key in required:
            if key not in self.config:
                raise ValueError(f"Missing required config key: {key}")

    def _initialize(self) -> None:
        logger.info(f"{self.__class__.__name__} initialized")

    def do_something(self, input: str) -> Dict[str, Any]:
        """
        [What this method does in one sentence.]

        Args:
            input: [Description]

        Returns:
            dict with keys:
                success (bool): True if the operation succeeded.
                output (Any): The result, or None on failure.
                error (str | None): Error message if success is False.
        """
        try:
            if not input:
                raise ValueError("Input cannot be empty")

            output = self._process(input)

            logger.info(f"Successfully processed: {input[:50]}")
            return {"success": True, "output": output, "error": None}

        except Exception as e:
            logger.error(f"Error in do_something({input[:50]!r}): {e}")
            return {"success": False, "output": None, "error": str(e)}

    def _process(self, input: str) -> Any:
        """Internal processing logic. Override in subclasses or extend here."""
        raise NotImplementedError


def create_module(config: Dict[str, Any]) -> ModuleClass:
    """
    Factory function. Prefer this over direct instantiation in application code.

    Args:
        config: Same dict passed to ModuleClass.__init__.

    Returns:
        Configured ModuleClass instance.
    """
    return ModuleClass(config)

"""The hidden tactical truth log. Only ``earshot.sim`` (which writes it) and ``earshot.eval``
(which grades against it) may import this package; ``tests/test_import_boundaries.py`` fails
the build otherwise.

Locally the logs live under ``data/hidden/`` (gitignored). Deployed, they sit in their own Blob
container that only the evaluation job's identity can read (design F4); the app, the agents
and the MCP server never see them.
"""

from earshot.hidden.store import TRUTH_ROOT, read_truth, truth_path, write_truth

__all__ = ["TRUTH_ROOT", "read_truth", "truth_path", "write_truth"]

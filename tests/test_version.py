from importlib.metadata import version

import cli_router


def test_runtime_version_matches_package_metadata():
    assert cli_router.__version__ == version("cli-router")

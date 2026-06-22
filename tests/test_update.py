from axon.update import _version_tuple
from axon.windows_startup import startup_command


def test_version_parser_orders_release_versions():
    assert _version_tuple("v1.3.0") > _version_tuple("1.2.9")
    assert _version_tuple("invalid") == ()


def test_startup_command_is_explicitly_quoted():
    command = startup_command()
    assert command.startswith('"')
    assert "run.py" in command or command.endswith('.exe"')

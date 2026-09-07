"""Check installed package metadata and distribution resources."""

from importlib import metadata, resources

from smcsignal import __version__


def test_distribution_version_matches_package() -> None:
    assert metadata.version("smcsignal") == __version__


def test_console_entrypoint_is_registered() -> None:
    scripts = {
        entry.name: entry.value
        for entry in metadata.distribution("smcsignal").entry_points
        if entry.group == "console_scripts"
    }
    assert scripts["smcsignal"] == "smcsignal.cli:main"


def test_package_includes_typing_marker() -> None:
    assert resources.files("smcsignal").joinpath("py.typed").is_file()

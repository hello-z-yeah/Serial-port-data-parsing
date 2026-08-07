from pathlib import Path


def test_python_314_compatible_build_dependency_minimums():
    root = Path(__file__).resolve().parents[1]
    requirements = (root / "requirements.txt").read_text(encoding="utf-8")
    assert "PySide6>=6.10.1" in requirements
    assert "PySide6-Fluent-Widgets>=1.11.3" in requirements
    assert "PyInstaller>=6.21.0" in requirements


def test_primary_build_entry_does_not_depend_on_batch_or_powershell():
    root = Path(__file__).resolve().parents[1]
    console = (root / "SST_Build_Manager.py").read_text(encoding="utf-8")
    gui = (root / "SST_Build_Manager.pyw").read_text(encoding="utf-8")
    manager = (root / "build_tools" / "build_manager.py").read_text(encoding="utf-8")

    assert "cli_main" in console
    assert "tkinter" in gui
    assert "build-installer" in manager
    assert "subprocess.Popen" in manager
    assert "shell=True" not in manager


def test_obsolete_batch_and_cmd_entrypoints_are_removed():
    root = Path(__file__).resolve().parents[1]
    legacy = list(root.rglob("*.bat")) + list(root.rglob("*.cmd"))
    assert legacy == []


def test_supported_launch_alternatives_are_present_and_documented():
    root = Path(__file__).resolve().parents[1]
    assert (root / "SST_Build_Manager.pyw").is_file()
    assert (root / "SST_Build_Manager.py").is_file()
    assert (root / "SST_Build_Manager.vbs").is_file()
    guide = (root / "WINDOWS_BUILD_GUIDE.md").read_text(encoding="utf-8")
    assert "SST_Build_Manager.pyw" in guide
    assert "SST_Build_Manager.py build-installer" in guide

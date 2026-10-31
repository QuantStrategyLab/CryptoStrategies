import tomllib
from pathlib import Path

from packaging.requirements import Requirement
import pytest


ROOT = Path(__file__).resolve().parents[1]
QPK_REVISION = "c7646a7168b3dafa763ef7751a182d23e8de7790"
QPK_URL = (
    "quant-platform-kit @ git+https://github.com/QuantStrategyLab/"
    f"QuantPlatformKit.git@{QPK_REVISION}"
)


def test_qsl_compat_metadata_exists_and_bundle() -> None:
    qsl_path = ROOT / "qsl.toml"
    assert qsl_path.exists(), "qsl.toml missing"
    with qsl_path.open("rb") as f:
        data = tomllib.load(f)

    assert data.get("compat", {}).get("bundle") == "2026.09.1", "compat.bundle mismatch"


def test_qpk_pin_lock_and_ci_are_dependency_enabled() -> None:
    with (ROOT / "pyproject.toml").open("rb") as file:
        pyproject = tomllib.load(file)
    with (ROOT / "qsl.toml").open("rb") as file:
        qsl = tomllib.load(file)
    with (ROOT / "uv.lock").open("rb") as file:
        lock = tomllib.load(file)

    assert pyproject["project"]["dependencies"] == ["numpy", "pandas", QPK_URL]
    assert qsl["compat"]["requires"] == [QPK_URL]

    packages = {package["name"]: package for package in lock["package"]}
    locked_qpk = packages["quant-platform-kit"]
    locked_crypto = packages["crypto-strategies"]
    assert locked_qpk["source"]["git"] == (
        "https://github.com/QuantStrategyLab/QuantPlatformKit.git"
        f"?rev={QPK_REVISION}#{QPK_REVISION}"
    )
    qpk_requirements = [
        requirement
        for requirement in locked_crypto["metadata"]["requires-dist"]
        if requirement["name"] == "quant-platform-kit"
    ]
    assert qpk_requirements == [
        {
            "name": "quant-platform-kit",
            "git": (
                "https://github.com/QuantStrategyLab/QuantPlatformKit.git"
                f"?rev={QPK_REVISION}"
            ),
        }
    ]

    ci = (ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "--no-deps" not in ci
    assert "python -m pip install -e ." in ci
    assert "python -m pip check" in ci


def test_research_requirements_match_lock_metadata() -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    requirements = [
        Requirement(value)
        for value in pyproject["project"]["optional-dependencies"]["research"]
    ]
    root_package = next(package for package in lock["package"] if package["name"] == "crypto-strategies")
    locked_requirements = [
        requirement
        for requirement in root_package["metadata"]["requires-dist"]
        if requirement.get("marker") == "extra == 'research'"
    ]

    assert sorted(locked_requirements, key=lambda item: item["name"]) == sorted(
        [
            {"name": requirement.name, "specifier": str(requirement.specifier), "marker": "extra == 'research'"}
            for requirement in requirements
        ],
        key=lambda item: item["name"],
    )


@pytest.mark.parametrize("dependency", ["numpy", "pandas"])
def test_locked_research_versions_satisfy_declared_requirement(dependency: str) -> None:
    pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text())
    lock = tomllib.loads((ROOT / "uv.lock").read_text())
    requirement = next(
        requirement
        for value in pyproject["project"]["optional-dependencies"]["research"]
        if (requirement := Requirement(value)).name == dependency
    )
    locked_versions = [package["version"] for package in lock["package"] if package["name"] == dependency]

    assert locked_versions, f"{dependency} is missing from uv.lock"
    assert all(requirement.specifier.contains(version) for version in locked_versions), (
        f"{dependency} versions {locked_versions} do not satisfy {requirement.specifier}"
    )

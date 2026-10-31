#!/usr/bin/env python3
"""Verify a normal Crypto wheel installation without evaluating a strategy."""

from __future__ import annotations

import argparse
import importlib
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import site
import sys
import sysconfig
from urllib.parse import urlsplit

_NETWORK_EVENTS = frozenset({"socket.connect", "socket.bind", "socket.getaddrinfo"})
_NUMERIC_MODULES = ("numpy", "pandas")
_REQUIRED_MODULES = ("crypto_strategies", "quant_platform_kit")
_QPK_REPOSITORY = ("github.com", "/QuantStrategyLab/QuantPlatformKit.git")


class VerificationError(RuntimeError):
    """An installation or provenance requirement was not met."""


def _within(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path.is_relative_to(root) for root in roots)


def _library_roots() -> tuple[Path, ...]:
    prefix = Path(sys.prefix).resolve()
    if sys.prefix == sys.base_prefix or not sys.flags.isolated or site.ENABLE_USER_SITE:
        raise VerificationError("an isolated virtualenv interpreter is required; invoke python -I")
    config = prefix / "pyvenv.cfg"
    if not config.is_file():
        raise VerificationError("virtualenv configuration is absent")
    settings = {}
    for line in config.read_text().splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            settings[key.strip().lower()] = value.strip().lower()
    if settings.get("include-system-site-packages") != "false":
        raise VerificationError("system site packages must be disabled")
    paths = sysconfig.get_paths()
    roots = tuple(dict.fromkeys(Path(paths[key]).resolve() for key in ("purelib", "platlib")))
    if not all(root.is_relative_to(prefix) for root in roots):
        raise VerificationError("site packages must belong to the active virtualenv")
    return roots


def _distribution(name: str, roots: tuple[Path, ...], expected_qpk_pin: str) -> dict | None:
    try:
        distribution = importlib.metadata.distribution(name)
    except importlib.metadata.PackageNotFoundError:
        return None
    location = Path(distribution.locate_file("")).resolve()
    if not _within(location, roots):
        raise VerificationError(f"{name} distribution is outside virtualenv site packages")
    raw = distribution.read_text("direct_url.json")
    direct = json.loads(raw) if raw is not None else {}
    if direct.get("dir_info", {}).get("editable"):
        raise VerificationError(f"{name} is editable")
    info = {"version": distribution.version, "location": str(location)}
    if name == "crypto-strategies":
        source = urlsplit(direct.get("url", ""))
        if not source.path.lower().endswith(".whl") or "archive_info" not in direct:
            raise VerificationError("Crypto must be installed from the built wheel")
        info["wheel_filename"] = Path(source.path).name
    if name == "quant-platform-kit":
        source = urlsplit(direct.get("url", ""))
        vcs = direct.get("vcs_info", {})
        if (source.hostname, source.path) != _QPK_REPOSITORY:
            raise VerificationError("QPK distribution has an unexpected repository origin")
        if vcs.get("vcs") != "git" or vcs.get("commit_id") != expected_qpk_pin:
            raise VerificationError("QPK distribution does not match the expected Git pin")
        info["vcs_commit"] = vcs["commit_id"]
    return info


def _module_origin(name: str, roots: tuple[Path, ...], *, loaded: bool = False) -> str | None:
    if loaded:
        module = sys.modules.get(name)
        origin = None if module is None else getattr(module, "__file__", None)
    else:
        specification = importlib.util.find_spec(name)
        origin = None if specification is None else specification.origin
    if origin is None:
        return None
    path = Path(origin).resolve()
    if not _within(path, roots):
        raise VerificationError(f"{name} module is outside virtualenv site packages")
    return str(path)


def classify_import(
    error: Exception | None,
    numeric_presence: dict,
    network_attempts: list[str],
) -> tuple[dict, int]:
    """Require a successful import with both numeric dependencies installed."""
    result = {
        "runtime_import_succeeded": error is None,
        "missing_dependency": error.name if type(error) is ModuleNotFoundError else None,
    }
    if network_attempts:
        return {**result, "status": "network_attempt_detected"}, 1
    all_present = all(
        numeric_presence[name]["distribution_present"] and numeric_presence[name]["module_present"]
        for name in _NUMERIC_MODULES
    )
    if error is not None:
        return {**result, "status": "runtime_import_failed", "error_type": type(error).__name__}, 1
    if not all_present:
        return {**result, "status": "numeric_distribution_metadata_missing"}, 1
    return {**result, "status": "runtime_import_succeeded"}, 0


def verify(expected_qpk_pin: str) -> tuple[dict, int]:
    report = {
        "schema_version": "qsl.normal_wheel_import.v1",
        "expected_qpk_pin": expected_qpk_pin,
        "runtime_import_succeeded": False,
        "missing_dependency": None,
        "network_attempts": [],
    }
    try:
        if re.fullmatch(r"[0-9a-f]{40}", expected_qpk_pin) is None:
            raise VerificationError("expected QPK pin must be a full lowercase Git SHA")
        roots = _library_roots()
        report["site_packages"] = [str(root) for root in roots]
        if any(name in sys.modules for name in (*_REQUIRED_MODULES, *_NUMERIC_MODULES)):
            raise VerificationError("target modules must not be preloaded")

        def network_guard(event, _arguments):
            if event in _NETWORK_EVENTS:
                report["network_attempts"].append(event)
                raise VerificationError("network access attempted during the import probe")

        sys.addaudithook(network_guard)
        report["distributions"] = {}
        report["module_origins_before_import"] = {}
        for distribution_name, module_name in (
            ("crypto-strategies", "crypto_strategies"),
            ("quant-platform-kit", "quant_platform_kit"),
        ):
            info = _distribution(distribution_name, roots, expected_qpk_pin)
            if info is None:
                raise VerificationError(f"{distribution_name} is not installed")
            report["distributions"][distribution_name] = info
            origin = _module_origin(module_name, roots)
            if origin is None:
                raise VerificationError(f"{module_name} has no installed module")
            report["module_origins_before_import"][module_name] = origin

        numeric = {}
        for name in _NUMERIC_MODULES:
            info = _distribution(name, roots, expected_qpk_pin)
            origin = _module_origin(name, roots)
            numeric[name] = {
                "distribution_present": info is not None,
                "module_present": origin is not None,
                "version": None if info is None else info["version"],
                "module_origin": origin,
            }
        report["numeric_presence_before_import"] = numeric

        error = None
        try:
            importlib.import_module("crypto_strategies.entrypoints")
        except Exception as exc:
            error = exc
        report["loaded_module_origins"] = {}
        for name in (*_REQUIRED_MODULES, *_NUMERIC_MODULES, "crypto_strategies.entrypoints"):
            if name in sys.modules:
                origin = _module_origin(name, roots, loaded=True)
                if origin is None:
                    raise VerificationError(f"{name} has no loaded file origin")
                report["loaded_module_origins"][name] = origin
        if error is None:
            required = {*_REQUIRED_MODULES, *_NUMERIC_MODULES, "crypto_strategies.entrypoints"}
            if not required.issubset(report["loaded_module_origins"]):
                raise VerificationError("successful import did not load the required installed modules")
        result, code = classify_import(error, numeric, report["network_attempts"])
        return {**report, **result}, code
    except Exception as exc:
        report["status"] = "verification_failed"
        report["error_type"] = type(exc).__name__
        if isinstance(exc, VerificationError):
            report["verification_error"] = str(exc)
        return report, 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--expected-qpk-commit", required=True)
    parser.add_argument("--output", type=Path)
    arguments = parser.parse_args(argv)
    report, code = verify(arguments.expected_qpk_commit)
    text = json.dumps(report, indent=2, sort_keys=True) + "\n"
    if arguments.output is not None:
        arguments.output.write_text(text)
    print(text, end="")
    return code


if __name__ == "__main__":
    raise SystemExit(main())

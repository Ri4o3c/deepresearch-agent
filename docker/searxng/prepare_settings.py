"""Render a private runtime YAML from the existing local .env; never print values."""
import copy
import os
from pathlib import Path
import tempfile

from dotenv import dotenv_values
import yaml


HERE = Path(__file__).resolve().parent


def build_settings(template: dict, secret: str | None) -> dict:
    if not isinstance(secret, str) or not secret.strip():
        raise ValueError("SEARXNG_SECRET is required")
    if not isinstance(template, dict) or not isinstance(template.get("server"), dict):
        raise ValueError("invalid settings template")
    if secret == template["server"].get("secret_key"):
        raise ValueError("SEARXNG_SECRET must differ from template placeholder")
    result = copy.deepcopy(template)
    result["server"]["secret_key"] = secret
    return result


def write_runtime(settings: dict, target: Path) -> None:
    """Serialize and atomically replace; temporary files are also gitignored."""
    tmp = None
    try:
        with tempfile.NamedTemporaryFile(mode="w", encoding="utf-8", newline="\n",
                                         prefix=".settings.runtime.", suffix=".tmp",
                                         dir=target.parent, delete=False) as stream:
            tmp = Path(stream.name)
            os.chmod(tmp, 0o600)
            yaml.safe_dump(settings, stream, allow_unicode=True, sort_keys=False)
        os.replace(tmp, target)
    finally:
        if tmp is not None and tmp.exists():
            tmp.unlink()


def prepare(env_file: Path, template_file: Path, target: Path) -> None:
    if not env_file.is_file():
        raise ValueError("local .env is required")
    # Read only this project's file. Never let host environment override the signing value.
    values = dotenv_values(env_file, interpolate=False, encoding="utf-8-sig")
    secret = values.get("SEARXNG_SECRET")
    # A literal secret is required; do not silently inject an unresolved env expression.
    if secret and ("${" in secret or "\n" in secret or "\r" in secret):
        raise ValueError("SEARXNG_SECRET must be a single literal value")
    with template_file.open(encoding="utf-8-sig") as source:
        template = yaml.safe_load(source)
    write_runtime(build_settings(template, secret), target)


def main() -> int:
    try:
        prepare(HERE / ".env", HERE / "settings.yml", HERE / "settings.runtime.yml")
    except Exception:
        # Parser/IO exceptions can contain values or excerpts. Do not include exception text.
        print("runtime_ready=false; check local .env, template and file permissions")
        return 1
    print("runtime_ready=true")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

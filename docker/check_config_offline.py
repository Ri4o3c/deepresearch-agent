"""One combined offline config round; sanitized output only, no service startup."""
import datetime
import importlib.util
import io
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

from dotenv import dotenv_values
import yaml


ROOT = Path(__file__).resolve().parents[1]
KEYS = (
    "DATABASE_URL", "SALT", "ENCRYPTION_KEY", "CLICKHOUSE_PASSWORD", "REDIS_AUTH",
    "NEXTAUTH_SECRET", "MINIO_ROOT_PASSWORD", "POSTGRES_PASSWORD",
    "LANGFUSE_S3_EVENT_UPLOAD_SECRET_ACCESS_KEY", "LANGFUSE_S3_MEDIA_UPLOAD_SECRET_ACCESS_KEY",
    "LANGFUSE_S3_BATCH_EXPORT_SECRET_ACCESS_KEY",
)
RESULTS = []


def check(name, condition):
    RESULTS.append({"type": name, "ok": bool(condition)})
    if not condition:
        raise RuntimeError("configuration check failed")


def main():
    zone = datetime.timezone(datetime.timedelta(hours=8))
    started = datetime.datetime.now(zone).isoformat()
    aborted = False
    try:
        plugin = Path("C:/Program Files/Docker/Docker/resources/cli-plugins/docker-compose.exe")
        check("existing_compose_plugin", plugin.is_file())
        compose_path = ROOT / "docker/langfuse/docker-compose.yml"
        raw = compose_path.read_text(encoding="utf-8-sig")
        check("all_sensitive_references_required", all(
            "${" + key + ":?" in raw and "${" + key + ":-" not in raw for key in KEYS))
        local = dotenv_values(compose_path.parent / ".env", interpolate=False, encoding="utf-8-sig")
        check("existing_local_keys_present_nonempty", all(local.get(key) and local[key].strip() for key in KEYS))
        # Real values are not interpolated, rendered, written, logged or passed to the subprocess.
        del local
        spec = importlib.util.spec_from_file_location("settings_prepare", ROOT / "docker/searxng/prepare_settings.py")
        renderer = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(renderer)
        template_path = ROOT / "docker/searxng/settings.yml"
        template = yaml.safe_load(template_path.read_text(encoding="utf-8-sig"))
        fixture = {key: "offline-fixture-credential" for key in KEYS}
        fixture["DATABASE_URL"] = "postgresql://fixture:fixture@postgres:5432/fixture"
        fixture["ENCRYPTION_KEY"] = "ab" * 32
        with tempfile.TemporaryDirectory(prefix="deep-research-config-offline-") as temp:
            tmp = Path(temp)
            env_file = tmp / ".env"
            child_env = {"SystemRoot": "C:/Windows", "PATH": str(plugin.parent),
                         "TEMP": str(tmp), "TMP": str(tmp), "DOCKER_CONFIG": str(tmp)}

            def compose(values, source):
                env_file.write_text("\n".join(key + "=" + value for key, value in values.items()) + "\n", encoding="utf-8")
                result = subprocess.run([str(plugin), "--env-file", str(env_file), "-f", str(source),
                                         "config", "--format", "json"], cwd=tmp, env=child_env,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=60)
                # Never emit stdout/stderr: even config errors can embed scalar values.
                return result

            result = compose(fixture, compose_path)
            check("langfuse_complete_fixture_parses", result.returncode == 0)
            rendered = json.loads(result.stdout)
            worker = rendered["services"]["langfuse-worker"]["environment"]
            web = rendered["services"]["langfuse-web"]["environment"]
            check("langfuse_sensitive_fixture_forwarded", all(
                (web if key == "NEXTAUTH_SECRET" else worker).get(key) == fixture[key]
                for key in KEYS if key not in ("MINIO_ROOT_PASSWORD", "POSTGRES_PASSWORD")))
            check("langfuse_server_passwords_forwarded",
                  rendered["services"]["postgres"]["environment"]["POSTGRES_PASSWORD"] == fixture["POSTGRES_PASSWORD"]
                  and rendered["services"]["minio"]["environment"]["MINIO_ROOT_PASSWORD"] == fixture["MINIO_ROOT_PASSWORD"])
            # Missing AND empty every sensitive variable, in the same single invocation/round.
            for key in KEYS:
                missing = dict(fixture)
                del missing[key]
                check("missing_rejected:" + key, compose(missing, compose_path).returncode != 0)
                empty = dict(fixture, **{key: ""})
                check("empty_rejected:" + key, compose(empty, compose_path).returncode != 0)

            dummy_secret = "offline-fixture:# ' quoted signing material"
            private_env, runtime = tmp / "searxng.env", tmp / "settings.runtime.yml"
            private_env.write_text("SEARXNG_SECRET=\"" + dummy_secret + "\"\n", encoding="utf-8")
            renderer.prepare(private_env, template_path, runtime)
            loaded = yaml.safe_load(runtime.read_text(encoding="utf-8"))
            check("searxng_dummy_injected_serialized", loaded["server"]["secret_key"] == dummy_secret)
            check("searxng_dummy_differs_from_template", loaded["server"]["secret_key"] != template["server"]["secret_key"])
            expected = dict(template)
            expected["server"] = dict(template["server"], secret_key=dummy_secret)
            check("searxng_nonsecret_settings_preserved", loaded == expected)
            for name, env_text in (("missing", ""), ("empty", "SEARXNG_SECRET=\n")):
                private_env.write_text(env_text, encoding="utf-8")
                failed = False
                try:
                    renderer.prepare(private_env, template_path, tmp / (name + ".yml"))
                except ValueError:
                    failed = True
                check("searxng_" + name + "_rejected", failed and not (tmp / (name + ".yml")).exists())
            failed = False
            try:
                renderer.build_settings(template, template["server"]["secret_key"])
            except ValueError:
                failed = True
            check("searxng_template_placeholder_rejected", failed)
            searx_path = ROOT / "docker/searxng/docker-compose.yml"
            result = compose({"SEARXNG_SECRET": "offline-fixture-only"}, searx_path)
            check("searxng_complete_fixture_parses", result.returncode == 0)
            rendered = json.loads(result.stdout)
            mounts = rendered["services"]["searxng"]["volumes"]
            check("searxng_explicit_runtime_mount", any(
                v["target"] == "/etc/searxng/settings.yml"
                and v["source"].replace("\\", "/").endswith("/settings.runtime.yml")
                and v.get("read_only") for v in mounts))
            check("searxng_no_image_secret_env_override",
                  "SEARXNG_SECRET" not in rendered["services"]["searxng"].get("environment", {}))
            check("searxng_compose_missing_rejected", compose({}, searx_path).returncode != 0)
            check("searxng_compose_empty_rejected", compose({"SEARXNG_SECRET": ""}, searx_path).returncode != 0)

        # Ignore checks do not create any real runtime file.
        ignored = subprocess.run(["git", "check-ignore", "-q", "--", "docker/searxng/settings.runtime.yml"], cwd=ROOT)
        check("runtime_ignored", ignored.returncode == 0)
        ignored = subprocess.run(["git", "check-ignore", "-q", "--", "docker/searxng/.settings.runtime.fixture.tmp"], cwd=ROOT)
        check("runtime_temporary_ignored", ignored.returncode == 0)
        for path in ("docker/searxng/.env", "docker/langfuse/.env"):
            result = subprocess.run(["git", "check-ignore", "-q", "--", path], cwd=ROOT)
            check("real_env_ignored:" + path, result.returncode == 0)
        for path in ("docker/searxng/.env.example", "docker/langfuse/.env.example"):
            result = subprocess.run(["git", "check-ignore", "-q", "--no-index", "--", path], cwd=ROOT)
            check("example_not_ignored:" + path, result.returncode == 1)
        example = dotenv_values(ROOT / "docker/langfuse/.env.example", interpolate=False)
        check("langfuse_example_required_keys", all(key in example for key in KEYS))
        wrapper = (ROOT / "docker/searxng/start.ps1").read_text(encoding="utf-8")
        docs = (ROOT / "docs/search.md").read_text(encoding="utf-8-sig")
        check("wrapper_documented_prepare_before_start",
              "docker/searxng/start.ps1" in docs and "settings.runtime.yml" in docs
              and wrapper.index("prepare_settings.py") < wrapper.index("up -d")
              and "$LASTEXITCODE -ne 0" in wrapper)
        check("no_real_runtime_created", not (ROOT / "docker/searxng/settings.runtime.yml").exists())
    except Exception:
        # Do not expose exceptions or parser excerpts. Stop this round, no retries.
        aborted = True
    passed = sum(item["ok"] for item in RESULTS)
    failed = sum(not item["ok"] for item in RESULTS)
    summary = {"started": started, "ended": datetime.datetime.now(zone).isoformat(),
               "mode": "offline_dummy", "passed": passed, "failed": failed,
               "aborted": aborted, "checks": RESULTS, "exit_code": 1 if aborted or failed else 0,
               "real_research_requests": 0, "real_llm_calls": 0, "service_start_stop": False}
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return summary["exit_code"]


if __name__ == "__main__":
    raise SystemExit(main())

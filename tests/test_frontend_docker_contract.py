from pathlib import Path
from unittest import TestCase
from unittest.mock import patch

from r20_backend.app import AdminConfigUpdate, FRONTEND_INDEX, app, update_status
from r20_backend.env_path import configured_env_file
from scripts.verify_frontend_contracts import CONFIG_PAYLOAD_FIELDS, FRONTEND_CONTRACTS, SPA_PATHS, collect_routes

ROOT = Path(__file__).resolve().parents[1]


class FrontendContractTests(TestCase):
    def test_vue_api_contracts_exist(self):
        routes = collect_routes(app)
        self.assertFalse(FRONTEND_CONTRACTS - routes)
        available_spa = {path for method, path in routes if method == "GET"}
        self.assertFalse(SPA_PATHS - available_spa)

    def test_admin_config_payload_matches_backend_model(self):
        self.assertTrue(CONFIG_PAYLOAD_FIELDS <= set(AdminConfigUpdate.model_fields))

    def test_vue_production_build_exists(self):
        self.assertTrue(FRONTEND_INDEX.exists())
        self.assertTrue((ROOT / "r20_frontend" / "package.json").exists())


class DockerContractTests(TestCase):
    def test_docker_artifacts_cover_build_health_and_persistence(self):
        dockerfile = (ROOT / "Dockerfile").read_text(encoding="utf-8")
        compose = (ROOT / "compose.yaml").read_text(encoding="utf-8")
        entrypoint = (ROOT / "deploy" / "docker-entrypoint.sh").read_text(encoding="utf-8")
        self.assertGreaterEqual(dockerfile.count("FROM "), 2)
        self.assertIn("npm run build", dockerfile)
        self.assertIn("HEALTHCHECK", dockerfile)
        self.assertIn("/api/v1/health", compose)
        self.assertIn("r20_data:/app/data", compose)
        self.assertIn("./docker/config:/app/config", compose)
        self.assertIn("R20_SETUP_TOKEN", entrypoint)

    def test_docker_env_path_and_update_status(self):
        with patch.dict("os.environ", {"R20_ENV_FILE": "/tmp/r20/config/.env", "R20_DEPLOYMENT_MODE": "docker", "R20_BUILD_REVISION": "abc123"}, clear=False):
            self.assertEqual(configured_env_file(ROOT), Path("/tmp/r20/config/.env").resolve())
            status = update_status()
        self.assertEqual(status["deployment_mode"], "docker")
        self.assertEqual(status["local"], "abc123")
        self.assertFalse(status["dirty"])

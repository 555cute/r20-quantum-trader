"""Per-test configuration sandbox: patch source constants AND imported path aliases."""
import importlib
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch


def isolate_config(test):
    temp = tempfile.TemporaryDirectory(prefix='r20-test-config-')
    test.addCleanup(temp.cleanup)
    root = Path(temp.name)
    project = Path(__file__).resolve().parents[1]
    for name in ('r20_backend.llm_manager', 'r20_backend.council_manager',
                 'r20_backend.policy_snapshot', 'r20_backend.interceptor_manager',
                 'scripts.prompt_library', 'scripts.evolution_shield',
                 'r20_gateway.secrets'):
        importlib.import_module(name)
    # Patch every already-bound alias, not just the defining module (law 2).
    for name, module in list(sys.modules.items()):
        if not module or name.startswith('tests'):
            continue
        if not (name.startswith(('r20_backend.', 'r20_gateway.', 'scripts.')) or
                name in ('prompt_library', 'evolution_shield')):
            continue
        for key, value in list(vars(module).items()):
            if not key.isupper() or not isinstance(value, (str, Path)):
                continue
            try:
                relative = Path(value).relative_to(project / 'data')
            except ValueError:
                continue
            target = root / 'data' / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            replacement = str(target) if isinstance(value, str) else target
            p = patch.object(module, key, replacement)
            p.start(); test.addCleanup(p.stop)
    app = sys.modules.get("r20_backend.app")
    if app is not None:
        git_probe = patch.object(app, "git", side_effect=lambda args: (
            "0 0" if args[0] == "rev-list" else "test" if args[0] == "branch" else
            "" if args[0] in ("fetch", "status") else "abc1234"))
        git_probe.start(); test.addCleanup(git_probe.stop)
    return root

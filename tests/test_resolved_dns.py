import importlib.util
from pathlib import Path


SCRIPT = Path(__file__).parents[1] / 'scripts/resolved_dns.py'
SPEC = importlib.util.spec_from_file_location('resolved_dns', SCRIPT)
resolved_dns = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(resolved_dns)


def test_tun_detection_matches_generated_yaml(tmp_path):
    config = tmp_path / 'runtime.yaml'
    config.write_text('mode: rule\ntun:\n  enable: true\n  stack: gvisor\ndns:\n  enable: true\n')
    assert resolved_dns.tun_enabled(config)
    config.write_text('tun:\n  enable: false\ndns:\n  enable: true\n')
    assert not resolved_dns.tun_enabled(config)

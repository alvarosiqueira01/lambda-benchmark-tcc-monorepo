import importlib.util
import sys
import unittest
from pathlib import Path

SCRIPTS = Path(__file__).parents[1] / "scripts" / "experiment"
PLANNING = SCRIPTS / "planning"


def _load_harness_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


# Load the harness common module under a private name and temporarily expose it
# as "common" while planner.py imports its helper functions. The original
# src/common package is restored afterwards so it is never shadowed.
_previous_common = sys.modules.get("common")
_harness_common = _load_harness_module("harness_common", SCRIPTS / "common.py")
sys.modules["common"] = _harness_common
try:
    _harness_planner = _load_harness_module("harness_planner", PLANNING / "planner.py")
finally:
    if _previous_common is None:
        sys.modules.pop("common", None)
    else:
        sys.modules["common"] = _previous_common

load_yaml = _harness_common.load_yaml
build = _harness_planner.build
build_pilot = _harness_planner.build_pilot

class PlannerTests(unittest.TestCase):
    def test_60_unique_conditions(self):
        p=load_yaml("experiments/experiment-plan.yaml"); rows=build(p)
        self.assertEqual(len(rows),60)
        self.assertEqual(len({(r["composition"],r["architecture"],r["memory_mb"],r["load_pattern"]) for r in rows}),60)
    def test_sparse_miniburst_expected_counts(self):
        p=load_yaml("experiments/experiment-plan.yaml")
        s=p["load_patterns"]["sparse_miniburst"]
        self.assertEqual(s["total_cycles"]*s["requests_per_active_window"],120)
        self.assertEqual(12*s["requests_per_active_window"],60)
    def test_pilot_has_12_runs_and_required_coverage(self):
        p=load_yaml("experiments/experiment-plan.yaml")
        rows=build(p); pilot=build_pilot(rows,p)
        self.assertEqual(len(pilot),12)
        self.assertEqual(len({r["condition_id"] for r in pilot}),12)
        self.assertEqual({r["composition"] for r in pilot},{"monolithic","decomposed"})
        self.assertEqual({r["architecture"] for r in pilot},{"x86_64","arm64"})
        self.assertEqual({r["load_pattern"] for r in pilot},{"constant","burst","sparse_miniburst"})
        memories={r["memory_mb"] for r in pilot}
        self.assertIn(128,memories)
        self.assertIn(2048,memories)
        self.assertTrue(all(r["block_id"]==0 and r["replication"]==0 for r in pilot))

if __name__=="__main__": unittest.main()

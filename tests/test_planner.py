import sys, unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parents[1]/"scripts"/"experiment"))
from common import load_yaml
from planner import build

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

if __name__=="__main__": unittest.main()

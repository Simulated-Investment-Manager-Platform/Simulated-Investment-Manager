"""Execute every non-install follow-on cell against a synthetic exported handoff."""
from contextlib import redirect_stdout
import hashlib
import io
import json
from pathlib import Path
import shutil
import tempfile
import unittest

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import pandas as pd

from research_pipeline import export_handoff, load_helpers
import test_research_pipeline as replay_tests


class FollowonNotebookTests(unittest.TestCase):
    def test_review_gate_then_complete_cached_quarter(self):
        project = Path(__file__).resolve().parents[1]
        nb = json.loads((project / "BMLL_Stage3_Daily_Fund_Research.ipynb").read_text())
        codes = ["".join(c["source"]) for c in nb["cells"] if c["cell_type"] == "code"]
        with tempfile.TemporaryDirectory() as tmp, redirect_stdout(io.StringIO()):
            root = Path(tmp)
            shutil.copy(project / "BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb", root)
            shutil.copy(project / "research_pipeline.py", root)
            helpers = load_helpers(root / "BMLL_Stage1_2_Corporate_Actions_WRDS_Bloomberg.ipynb")
            w, prices, calendar, events = replay_tests.DailyReplayTests().fixtures()
            w["nominal_date"] = w.date; w["reference_date"] = w.date
            wrds = events.reindex(columns=helpers["CA_CANONICAL_COLUMNS"])
            wrds["review_reason"] = ""; wrds["source"] = "WRDS_CRSP"
            bbg = wrds.copy(); bbg["source"] = "BLOOMBERG"
            bbg["source_event_id"] = "BBG:" + bbg.source_event_id
            unified = helpers["reconcile_corporate_actions"](wrds, bbg)
            namespace = dict(weights=w, wrds_corporate_actions=wrds,
                wrds_delist_events=wrds.iloc[:0], wrds_adj_factors=pd.DataFrame(
                    columns=["permno", "dlycaldt", "dlycumfacshr"]),
                wrds_id_history=pd.DataFrame({"permno": [1]}),
                bloomberg_corporate_actions=bbg, unified_corporate_actions=unified,
                wrds_table_report=pd.DataFrame(columns=["table", "available"]),
                selected_permnos=[1], CA_START_DATE="2026-01-02", CA_END_DATE="2026-04-03")
            export_handoff(namespace, root / "data/stage1_handoff")
            cache = root / "data/stage2_inputs"
            export_handoff(namespace, cache)
            prices.to_csv(cache / "daily_prices.csv", index=False)
            pd.DataFrame({"date": calendar}).to_csv(cache / "calendar.csv", index=False)
            bloomberg_file = root / "data/BMLL_Bloomberg_Corporate_Actions.csv"
            bloomberg_file.write_text("synthetic input fingerprint\n")
            metadata = {"start": "2026-01-02", "end": "2026-04-03", "schema": "crsp_q_stock",
                "universe": [1], "bloomberg_sha256": hashlib.sha256(bloomberg_file.read_bytes()).hexdigest(),
                "extra_sha256": {name: hashlib.sha256((cache / name).read_bytes()).hexdigest()
                                 for name in ["daily_prices.csv", "calendar.csv"]}}
            (cache / "coverage.json").write_text(json.dumps(metadata))
            ns = {}
            config = codes[1].replace("PROJECT_DIR = Path.cwd()", f"PROJECT_DIR = Path({str(root)!r})")
            exec(compile(config, "followon-config", "exec"), ns)
            ns.update(INPUT_MODE="cache", START_DATE="2026-01-02", END_DATE="2026-04-03",
                      INITIAL_CASH=1000., CASH_BUFFER_BPS=0.)
            for code in codes[2:]:
                exec(compile(code, "followon-cell", "exec"), ns)
            self.assertFalse(ns["READY"])
            self.assertIsNone(ns["results"])
            decisions = pd.read_csv(root / "data/stage2_review/event_decisions.csv")
            decisions["decision"] = "APPROVE"
            decisions["rationale"] = "Synthetic fixture reviewed for integration test"
            decisions.to_csv(root / "data/stage2_review/event_decisions.csv", index=False)
            for code in codes[4:]:
                exec(compile(code, "followon-reviewed-cell", "exec"), ns)
            self.assertTrue(ns["READY"])
            self.assertEqual(ns["results"]["daily_nav"].iloc[-1].nav, 1020.)
            self.assertTrue((root / "outputs/daily_fund/run_provenance.json").exists())
            plt.close("all")


if __name__ == "__main__":
    unittest.main()

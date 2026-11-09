import tempfile
import unittest
from pathlib import Path
import sys
import pandas as pd
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from scripts.review_priority_4year import prioritize

class ReviewPriorityTests(unittest.TestCase):
    def test_material_event_is_ranked_but_not_approved(self):
        with tempfile.TemporaryDirectory() as td:
            p=Path(td)
            (p/'data/stage2_review').mkdir(parents=True)
            (p/'data/stage2_inputs').mkdir(parents=True)
            pd.DataFrame([{'date':'2022-06-21','permno':123,'cap_weight_proxy':.02}]).to_csv(
               p/'data/stage2_inputs/weights.csv',index=False)
            pd.DataFrame([{'ex_date':'2022-09-15','permno':123,'event_type':'CASH_DIVIDEND',
                 'decision':'PENDING','review_key':'abc','match_status':'MATCHED_CONFLICT'}]).to_csv(
                p/'data/stage2_review/event_decisions.csv',index=False)
            q=prioritize(p)
            self.assertEqual(q.iloc[0].review_attention,'P0')
            self.assertEqual(q.iloc[0].decision,'PENDING')

if __name__=='__main__':unittest.main()

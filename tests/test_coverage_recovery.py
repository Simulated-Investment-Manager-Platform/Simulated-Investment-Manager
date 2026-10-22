import unittest
import pandas as pd
from scripts.coverage_recovery import suggest_provisional_events, temporal_exclusion_snapshots

class RecoveryTests(unittest.TestCase):
    def test_split_adjusted_dividend_selection_is_provisional(self):
        rows=[{'review_key':'cash','match_status':'MATCHED_CONFLICT','wrds_event_id':'CRSP:DIST:1:2023-01-01:1',
              'bloomberg_event_id':'BBG:1','permno':1,'event_type':'CASH_DIVIDEND','ex_date':'2023-01-01',
              'wrds_cash_per_share':.4,'bloomberg_cash_per_share':.04,'wrds_pay_date':'2023-01-22','bloomberg_pay_date':'2023-01-22'},
              {'review_key':'split','match_status':'MATCHED_AGREE','wrds_event_id':'CRSP:DIST:1:2024-01-01:1',
              'bloomberg_event_id':'BBG:2','permno':1,'event_type':'STOCK_SPLIT','ex_date':'2024-01-01',
              'wrds_split_ratio':10,'bloomberg_split_ratio':10}]
        ck=pd.DataFrame([{'source_event_id':'CRSP:DIST:1:2024-01-01:1','status':'AGREE'}])
        x=suggest_provisional_events(pd.DataFrame(rows),ck)
        self.assertEqual(x.loc[0,'provisional_rule'],'FUTURE_VERIFIED_SPLIT_BASIS_RESEARCH_ONLY')
        self.assertTrue(x.human_approval_required.all())
        bad=suggest_provisional_events(pd.DataFrame(rows),pd.DataFrame([{'source_event_id':'CRSP:DIST:1:2024-01-01:1','status':'DISAGREE'}]))
        self.assertEqual(bad.loc[0,'provisional_wrds_source'],'')

    def test_equal_cash_ticker_review_is_not_rejected_globally(self):
        x=pd.DataFrame([{'review_key':'a','match_status':'MATCHED_CONFLICT','wrds_event_id':'CRSP:DIST:3:2023-01-03:1',
             'bloomberg_event_id':'BBG:9','permno':3,'event_type':'CASH_DIVIDEND','ex_date':'2023-01-03',
             'wrds_cash_per_share':0.9,'bloomberg_cash_per_share':0.9,'wrds_pay_date':'2023-01-10','bloomberg_pay_date':'2023-01-10'}])
        out=suggest_provisional_events(x,pd.DataFrame(columns=['source_event_id','status']))
        self.assertEqual(out.provisional_rule.iloc[0],'SAME_ECONOMICS_TICKER_MATCH_RESEARCH_ONLY')

    def test_one_problem_does_not_exclude_all_quarters(self):
        cal=pd.to_datetime(['2024-01-02','2024-01-03','2024-04-01','2024-04-02','2024-06-30'])
        w=pd.DataFrame([{'date':'2024-01-02','permno':1,'cap_weight_proxy':.4},
                        {'date':'2024-01-02','permno':2,'cap_weight_proxy':.6},
                        {'date':'2024-04-01','permno':1,'cap_weight_proxy':.4},
                        {'date':'2024-04-01','permno':2,'cap_weight_proxy':.6}])
        px=pd.DataFrame([{'date':d,'permno':p,'close_price':100,'price_flag':'TR'} for d in cal for p in [1,2]])
        kept,cut=temporal_exclusion_snapshots(w,cal,{1:[('2024-06-30','complex_action')]},px)
        self.assertEqual(len(cut),1)
        self.assertEqual(pd.Timestamp(cut.date.iloc[0]),pd.Timestamp('2024-04-01'))
        self.assertEqual(int(cut.permno.iloc[0]),1)
        self.assertEqual(len(kept),3)
        self.assertTrue((kept.groupby('date').cap_weight_proxy.sum().round(8)==1).all())

    def test_missing_price_blocks_only_relevant_window(self):
        cal=pd.to_datetime(['2024-01-02','2024-01-03','2024-04-01','2024-04-02','2024-06-30'])
        w=pd.DataFrame([{'date':x,'permno':p,'cap_weight_proxy':.5} for x in ['2024-01-02','2024-04-01'] for p in [1,2]])
        px=pd.DataFrame([{'date':d,'permno':p,'close_price':1.,'price_flag':'TR'} for d in cal for p in [1,2] if not (str(d.date())=='2024-06-30' and p==1)])
        _,cut=temporal_exclusion_snapshots(w,cal,{},px)
        self.assertEqual(len(cut),1)
        self.assertEqual(pd.Timestamp(cut.date.iloc[0]),pd.Timestamp('2024-04-01'))

if __name__=='__main__': unittest.main()

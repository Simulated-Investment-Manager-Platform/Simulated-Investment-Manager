import sys
import unittest
from pathlib import Path
import pandas as pd
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from scripts.multiyear_preflight import required_quote_days,price_coverage_report

class TestMultiYearCoverage(unittest.TestCase):
    def setUp(self):
        self.cal=pd.bdate_range('2022-07-01','2022-07-11')
        self.weights=pd.DataFrame({'date':['2022-07-01','2022-07-01','2022-07-07','2022-07-07'],
        'permno':[11,22,22,33],'cap_weight_proxy':[.5,.5,.6,.4]})
    def test_security_joins_later_does_not_need_earlier_prices(self):
        required=required_quote_days(self.weights,self.cal)
        self.assertNotIn(pd.Timestamp('2022-07-01'),required[33])
        self.assertEqual(max(required[11]),pd.Timestamp('2022-07-08'))
        prices=pd.DataFrame([{'permno':p,'date':d,'close_price':10.,'price_flag':'TR'}
             for p,dates in required.items() for d in dates])
        result=price_coverage_report(required,prices)
        self.assertTrue(result.ready.all())
        prices=prices[~((prices.permno==11)&(prices.date==pd.Timestamp('2022-07-08')))]
        result=price_coverage_report(required,prices)
        self.assertEqual(result.loc[result.permno==11,'missing_sessions'].iloc[0],1)
    def test_reject_duplicate_prices(self):
        required=required_quote_days(self.weights,self.cal)
        prices=pd.DataFrame([{'permno':11,'date':'2022-07-01','close_price':10.,'price_flag':'TR'}]*2)
        with self.assertRaises(ValueError):
            price_coverage_report(required,prices)
    def test_requires_start_snapshot(self):
        with self.assertRaises(ValueError):
            required_quote_days(self.weights,self.cal[1:])

if __name__=='__main__':unittest.main()

class TestFourYearSimulation(unittest.TestCase):
    def test_four_year_clean_quarterly_replay(self):
        from research_pipeline import simulate
        days=pd.bdate_range('2022-06-21','2026-06-30')
        decision_days=[days[0]]+[d for d in days[1:] if d.month in (3,6,9,12) and d.day>=20 and d.day<=27 and d.dayofweek==0]
        # Use one snapshot per calendar quarter after the first.
        seen=set();picked=[]
        for d in decision_days:
            key=(d.year,(d.month-1)//3)
            if key not in seen:seen.add(key);picked.append(d)
        if days[0] not in picked:picked.insert(0,days[0])
        weights=pd.DataFrame([{'date':d,'permno':p,'cap_weight_proxy':wt} for d in picked for p,wt in [(11,.6),(22,.4)]])
        prices=pd.DataFrame([{'date':d,'permno':p,'close_price':x,'price_flag':'TR'} for d in days for p,x in [(11,100.),(22,50.)]])
        result=simulate(weights,prices,pd.Series(days),pd.DataFrame(columns=['source_event_id','event_type','ex_date','pay_date','permno','currency']),days[0],days[-1])
        self.assertEqual(len(result['daily_nav']),len(days))
        self.assertGreaterEqual(len(result['orders']),2)
        self.assertAlmostEqual(float(result['daily_nav'].iloc[-1].nav),1e10,delta=2e7)

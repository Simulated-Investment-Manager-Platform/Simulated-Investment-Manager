"""Phase-2 execution simulation and TCA for the BMLL simulated index fund.

schema          PM -> Trader -> Broker -> Fill tables and validators
market_data     BMLL daily per-venue VWAP/volume + auction prints, or synthetic stand-in
id_map          point-in-time CRSP PERMNO -> BMLL listing map (ISIN based), coverage report
order_generator trader/broker orders per strategy (CLOSE_AUCTION, VWAP, ARRIVAL_IS)
execution       fill prices: auction impact, venue VWAP + impact, arrival + impact
analysis        order TCA, notional-weighted summaries, execution tracking error
"""

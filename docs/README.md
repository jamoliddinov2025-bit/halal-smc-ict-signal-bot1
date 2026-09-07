# Documentation

Current scope: **Phase 2 — market data foundation only**.

- [Architecture](architecture.md): provider contract, module boundaries, and data flow.
- [Market data methodology](market-data-methodology.md): OHLCV schema, timestamps,
  validation, cleaning audit, CSV replay, Binance behavior, and limitations.
- [Development guide](development.md): installation, offline tests, and packaging.
- [Configuration](../config/README.md): supported settings and source selection.

Phase 1's scaffold is retained and extended only with market data functionality.
No trend detection, SMC, BOS, CHoCH, liquidity analysis, signals, charts, Telegram,
or halal filter is implemented. Phase 3 requires explicit approval.

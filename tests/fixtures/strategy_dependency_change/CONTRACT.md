# Application contract

`inventory_adapter.list_sellable()` is the application boundary. It returns
a new list of two-item tuples, each containing a canonical item code and an
integer quantity. The list is sorted by item code. Include only records marked
as listed with a positive available quantity. An empty result is an empty list.

The caller in `shop_view.py` consumes this established tuple interface. Do not
change that caller or leak dependency-specific report/record objects through
the adapter. The dependency's v2 report and record types are the inputs to the
adapter, not the application's public return type.

# Dependency change

The earlier dependency returned a sequence of `(code, quantity)` pairs.
Version 2 returns a `StockReport`; its `records` field contains
`StockRecord` objects with `code`, `units_available`, and `listed`
attributes. The report itself is not the old sequence. `stock_service.py`
documents the complete local v2 shape.

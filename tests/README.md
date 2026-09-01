# Test suite

The default suite covers current Trade Compass behavior only. It deliberately
does not verify retired desktop code, historical database migrations, old MT5
schemas, or source-code spelling.

Write scenarios in BDD form:

```python
def test_given_a_valid_export_when_imported_then_the_trade_is_available():
    # Given
    ...

    # When
    ...

    # Then
    ...
```

Use the lightest test boundary that proves the behavior:

- Call domain and application services directly for business rules.
- Call ingestion endpoint functions directly for API behavior.
- Use Streamlit `AppTest` only for user-visible interaction journeys.
- Use a real browser only for behavior that depends on browser JavaScript.

Commands:

- `make test`: fast behavior suite and one Streamlit smoke render.
- `make test-bdd`: scenarios explicitly organized as Given/When/Then.
- `make test-web`: maintained Streamlit interaction regression scenarios.
- `make test-browser`: optional Chromium-only scenarios.
- `make check`: fast behavior suite followed by Python compilation.

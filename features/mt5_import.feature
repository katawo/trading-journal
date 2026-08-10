Feature: Import completed MT5 positions locally
  As a trading-journal user
  I want to import completed positions from approved MT5 accounts
  So that realised P&L is available without exposing broker credentials or trading controls.

  Scenario: Import a completed position from a whitelisted account
    Given a USD journal with a registered MT5 account
    And a valid local MT5 position export for that account
    When I import the export
    Then one imported trade is created with its realised net P&L
    And the trade is excluded from R metrics until planned risk is added

  Scenario: Re-import a previously imported position
    Given an imported MT5 trade with journal annotations
    And a newer export for the same MT5 position
    When I import the newer export
    Then the MT5 financial fields are refreshed
    And the journal annotations remain unchanged

  Scenario: Reject a mismatched account currency
    Given a USD journal with a registered USD MT5 account
    And an export that declares EUR as its account currency
    When I import the export
    Then the import fails without creating a trade

  Scenario: Compare a tagged strategy with optional backtest context
    Given a strategy profile with optional backtest statistics
    And an imported trade tagged with that strategy
    When I view the performance dashboard
    Then the live strategy result includes its backtest context

  Scenario: Apply a journal default strategy with a trade override
    Given Motimoti is the journal's default strategy
    And an imported trade has no strategy override
    And another imported trade overrides the strategy as Breakout
    When I view the journal
    Then the first trade uses Motimoti and the second uses Breakout

  Scenario: Rename a saved strategy profile
    Given imported trades and the journal default are linked to a saved strategy profile
    When I rename that strategy profile
    Then its linked trades and journal default use the new strategy name

  Scenario: Compare P&L with the target over more than one month
    Given a selected report period spans two calendar months
    When I view the performance dashboard
    Then the period target equals two monthly targets

  Scenario: Analyse balance growth and drawdown
    Given an opening account balance and imported closed trades
    When I view the performance dashboard
    Then I can see balance growth, daily drawdown, and trade-quality statistics

  Scenario: Inspect performance trade by trade
    Given imported closed trades in the selected report period
    When I select the per-trade chart view
    Then I can see each closed trade's P&L and post-close drawdown

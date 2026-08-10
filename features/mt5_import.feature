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
    Given an imported MT5 trade
    And a newer export for the same MT5 position
    When I import the newer export
    Then the MT5 financial fields are refreshed
    And journal-wide defaults continue to determine strategy and R

  Scenario: Reject a mismatched account currency
    Given a USD journal with a registered USD MT5 account
    And an export that declares EUR as its account currency
    When I import the export
    Then the import fails without creating a trade

  Scenario: Compare the default strategy with optional backtest context
    Given a strategy profile with optional backtest statistics
    And that profile is the journal default strategy
    When I view the performance dashboard
    Then the live strategy result includes its backtest context

  Scenario: Apply a journal default strategy to every imported trade
    Given Motimoti is the journal's default strategy
    And multiple imported trades
    When I view the performance dashboard
    Then every trade uses Motimoti

  Scenario: Rename the journal default strategy profile
    Given the journal default is linked to a saved strategy profile
    When I rename that strategy profile
    Then imported trades and the journal default use the new strategy name

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

  Scenario: Refresh a changed MT5 export automatically
    Given an approved MT5 account has a configured Common Files export path
    And the Trading Journal sync EA publishes a changed completed-position export
    When I have the Dashboard open
    Then the journal imports the changed export without duplicating existing positions

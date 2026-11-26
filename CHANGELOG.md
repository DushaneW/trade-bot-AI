# Changelog

What changed for the people running Quorum. Newest first.

## Unreleased

### New

- Ask the Chair *"What are we holding?"* and it lists every open position with its result and its stop.
- Telegram `/positions` does the same from your phone.
- The daily summary counts the last 24 hours: orders, closed results and fees. *Send the summary now* in Settings
  shows what it looks like.
- When an exchange stops sending prices, the Chair says so and the Accounts page marks the account.
- Each trader's page shows the fees it has paid.
- A backtest's trades download as CSV.
- `quorum --version`, `quorum --data FOLDER`, and the version in Settings with links to release notes and bug
  reports.
- `/api/health` for Docker and uptime monitors. The Docker image uses it for its health check.

### Safer

- Five wrong dashboard passwords from one address make that address wait 15 minutes.

### Fixed

- Kraken, Coinbase, KuCoin, Bitget and MEXC were treated as having a testnet, so setup offered a testnet checkbox
  for them that led to a broken connection. The checkbox now only appears where a testnet exists.
- The Chair's Claude prompt now mentions each analyst's accuracy per market mood, which it was already given.

### For contributors

- 200+ tests covering indicators, the ledger, risk limits, learning, every analyst and trader, backtests,
  backup stops, the setup chat, notifications, the API, plugins, and that the README matches the code. They run
  on the simulator, without keys or network.
- Continuous integration on Linux, Windows and macOS with Python 3.10 and 3.12, plus a Docker build check.
- Ruff linting, Dependabot, an editorconfig, issue forms and a pull request checklist.
- A contributing guide, a security policy, an architecture overview and a plugin guide.

## 0.2.0 · 25 November 2026

- **Backup stops on the exchange.** Every open position gets a stop order resting on the exchange, just past the
  trader's own stop, so it is protected while your computer is off. Fills are booked the next time Quorum starts.
- **Telegram and Discord.** Messages for buys and sells, stops, pauses, failed orders and a daily summary.
  Telegram also takes `/status`, `/start`, `/stop` and `/stop close` from the one chat you pair.
- **Run it around the clock** with Docker and `docker compose`, behind a password (`QUORUM_PASSWORD`).
- **Four new analysts.** Link finds markets that move together and spots laggards. On crypto, Carry reads futures
  funding, Crowd reads open interest and Depth reads the order book.
- **Correlation-aware limits.** A trader already holding look-alike markets buys less of the next one.
- **Walk-forward backtests** tune a trader on one stretch of history and trade the next, unseen one.
- **Strategy plugins** from the `strategies` folder.
- **CSV export** of every trade, in total or per trader.
- The dashboard works on phones, with the sections in a bar at the bottom.

## 0.1.0 · 25 November 2026

- **Live trading only.** Paper mode is gone; practise on an exchange testnet. Older paper traders come back paused.
- The Chair sets everything up in a chat: market, exchange, key, markets, budget, traders and risk profile.
  Nothing trades until you press Start.
- Risk profiles (Careful, Balanced, Bold) for stop losses, position sizes and loss limits.
- Orders below the exchange minimum are raised to it when the trader can afford it.
- Analysts are trusted per market and per market mood, not just overall. Vector reads a twelve times longer chart
  alongside the one you trade. Entry size follows how strongly the team agrees.
- First public release: nine analysts, five traders, the Chair, backtesting, installers for macOS, Windows and
  Linux.

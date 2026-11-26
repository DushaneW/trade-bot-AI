# Security

Quorum holds exchange keys and places real orders, so security reports get priority over everything else.

## Reporting a problem

Please do not open a public issue for a vulnerability. Use GitHub's
[private vulnerability reporting](https://github.com/DushaneW/trade-bot-AI/security/advisories/new) instead.
Include what an attacker could do, how to reproduce it, and the version or commit you tested.

You can expect a first answer within a few days. Fixes for anything that could move or expose money ship before
anything else.

## What Quorum does to protect you

- **Keys stay on your machine.** They are encrypted with a key in `data/secret.key` and never sent anywhere except
  the exchange they belong to.
- **Trade-only keys.** On Binance, Quorum asks the exchange whether a key can withdraw and refuses it if so. Other
  exchanges do not report this; the setup tells you to switch withdrawals off yourself.
- **Local by default.** The dashboard listens on `127.0.0.1` only. Quorum refuses to listen on a network address
  unless `QUORUM_PASSWORD` is set.
- **Password guessing is slowed down.** Every wrong password costs a second, and after five wrong passwords from
  one address that address waits 15 minutes. Changing the password signs every browser out.
- **Telegram commands** are accepted only from the one chat that sent the pairing code shown in Settings.
- **Plugins** in `strategies/` are ordinary Python with full access to your computer. Only install files you have
  read.

## What you should do

- Create API keys with trading enabled and withdrawals disabled, and restrict them to your IP address if your
  exchange allows it.
- Back up `data/` and never share or commit it.
- On a server, put the dashboard behind HTTPS or reach it through an SSH tunnel. Never expose it over plain HTTP.

## Supported versions

Only the latest release gets security fixes.

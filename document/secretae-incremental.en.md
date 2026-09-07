# Essence Foundry game guide

Essence Foundry is an incremental game. Produce secret organisms for secret
shards, synthesize secrets, and use **concentration** to turn a completed run
into story essence. Use `/게임 도움말` for the in-Discord summary.

## Numbers and initial state

Resources are integer-valued for play; the display truncates decimals. Values
below `10,000` use ordinary integers and larger values use scientific or nested
scientific notation. A new player begins with one shard, zero story essence,
one organism of each colour, and 100 of every colour and shape secret.

## Daily production and synthesis

Production is available once per day and resets at 05:00 KST. For colour `c`:

```text
effective secret(c) = colour secret(c) × (1 + circle secret / 100)
raw production(c) = organism(c) × effective secret(c)^1.05 / 77 × Milky Way multiplier
gain(c) = ceil(raw production(c)^(1 + story essence / 240))
```

Production is calculated from black toward red, so a higher organism's output
can affect the lower organism in the same production. Red output becomes secret
shards. The Milky Way size is `server-wide story essence + 1`, and its multiplier
is `0.2 + 0.035 × ln(Milky Way size)`.

`/게임 합성` spends shards to create a colour or shape secret. The undiscounted
next-unit price at current amount `a` is `1.1^(a / 100)`. For a bundle, the bot
uses the geometric-series sum and then applies:

```text
essence catch-up discount = min((server highest essence - my essence) / 100, 0.5)
square cost multiplier = 1 / (1 + ln(1 + square secret) / 17)
final price = max(1, floor(base price × (1 - catch-up discount) × square multiplier))
```

`/게임 최대합성` buys the largest affordable groups using the same rules, in
this priority order:

```text
◯ → □ → ♡ → ⬛️ → ⬜️ → 🟪 → 🟦 → 🟩 → 🟨 → 🟧 → 🟥
```

`/게임 가격` is private: it shows every next-unit price, active discounts, and
the concentration estimate without spending a resource or daily production.

| Secret | Effect |
| --- | --- |
| ◯ | Multiplies every colour secret used in production by `1 + ◯ / 100`. |
| □ | Applies `1 / (1 + ln(1 + □) / 17)` to synthesis cost. |
| ♡ | Increases story essence gained from concentration. |

## Concentration and first run

Concentration is available once per week; it resets Monday at 05:00 KST. Its
base reward is `max(0, ceil(ln(shards) / ln(10^15)))`, multiplied by
`1 + ceil(heart secret / 100)`. The completed-stage efficiency is
`max(0, log10(log10(R + 1)))`, where `R` is that base reward times the heart
multiplier. With current essence `E`, the final reward is:

```text
ceil((E + 1) × (1 + completed-stage efficiency))
```

Successful concentration resets shards to 1, all organisms to 1, and all
colour/shape secrets to 100; story essence is retained and is immediately
included in that guild's Milky Way size. The confirmation recalculates state
when it is accepted, so a preview can change if the player acts in between.

A new account can reach its first essence without community rewards through six
`production → maximum synthesis` cycles followed by production and
concentration. Community queue/relay rewards are optional accelerators and can
be viewed with `/게임 보상`.

## Alerts and community rewards

`/게임 알림` is private and stores a per-player choice: off, a concentration
deadline reminder, or a daily reminder. The chosen hour is KST. A daily reminder
is sent only if the player has not used a game command that game day; either
enabled reminder can also send the concentration reminder before its weekly
reset when concentration remains unused. At a chosen hour from 05:00 through
23:00, the deadline is Sunday; at 00:00 through 04:00 it is Monday. Conditions
are checked at send time, and the game day changes at 05:00 KST.

Queue and relay rewards are configured independently by the server
administrator; they are not advertised in ordinary feature introductions. Ask
an administrator about policy or use `/게임 보상` for the currently effective
rewards. Legacy relay and published daily-trivia rewards apply
`floor(current secret × 1.05)` to all 11 secrets. A published weekly lesson
grants one essence at zero balance and otherwise doubles existing essence.

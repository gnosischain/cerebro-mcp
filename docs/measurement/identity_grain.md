# Identity Grain

Every MTA run requires an explicit identity grain. "User" is not a single
thing on-chain — it can be a wallet, an app user, a Safe owner, a Safe
contract, a session, or an offchain marketing identifier. The grain you
choose changes which touchpoints exist, who counts as converting, and which
attribution methods are valid.

This page exists because the wrong grain is the most common silent failure
in on-chain attribution.

## Available grains

| Grain | What it is | When to use | When it's wrong |
|---|---|---|---|
| `wallet` | EOA address (lowercase) | Pure EOA usage; gas-paying actor is the user | Multi-owner Safe wallets; smart-contract wallets where the EOA is a relayer |
| `app_user` | App-side user ID (Gnosis App, Gnosis Pay) | Conversions defined in app metrics; user state is in app DB | Anything purely on-chain — many on-chain wallets have no app_user |
| `safe` | Safe contract address | Treasury / multi-sig actions where Safe is the unit of behavior | EOA-only flows; sessions where one EOA owns many Safes |
| `owner` | Safe owner EOA(s) | When user identity = the human, regardless of which Safe they used | Single-Safe flows where mapping owners adds complexity without insight |
| `session` | App session ID | Conversion within a single visit; UX funnel work | Multi-day journeys; cross-device flows |
| `other` | UTM source / device fingerprint / ad ID | Marketing campaign attribution; offchain-first journeys | Anything that requires onchain matching |

## How to choose

Two questions:

1. **What is the conversion?** A topup is an app event → use `app_user`. A swap is on-chain → use `wallet`. A Safe payment is multi-sig → use `safe` or `owner` depending on whether you care about the contract or the human.
2. **Which touchpoints are you joining?** If your touchpoints are app screens, the join key must exist on the app side. If they are on-chain events, the join key must exist on-chain. Mismatched grains create empty joins or fake joins.

If the conversion grain and the touchpoint grain don't match, you need an
identity bridge model. Cerebro typically resolves this through Gnosis App
user → wallet mappings; verify with `describe_table` and `search_models`
that such a bridge exists before claiming you can join.

## Common failure modes

- **Wallet grain on a Safe-heavy flow.** Most "users" in a Safe flow are the gas relayer EOA, not the Safe owner. Wallet-grain attribution credits the relayer for every action.
- **Owner grain ignoring multi-owner Safes.** A 2-of-3 Safe has three owners; one transaction creates three "user actions" if you fan out by owner. State the fan-out rule.
- **App_user grain for purely on-chain conversions.** If only 30% of swappers have an app_user, the other 70% silently disappear. Coverage will be low and skewed.
- **Session grain for multi-day funnels.** A topup might require 3 sessions over 5 days. Session grain misses the cross-session journey.

## Documenting your choice

Every MTA report must include:

```markdown
## Identity grain
<wallet | app_user | safe | owner | session | other>

Justification: <one sentence — why this grain matches the conversion semantics>

Tradeoffs: <one sentence — which population is excluded or doubled by this choice>
```

`unified_causal_reviewer` Check 6 fails the report if this block is missing
or unjustified.

## Cross-references

- [`mta_overview.md`](mta_overview.md) — where identity grain plugs into the MTA workflow.
- [`causal_review.md`](causal_review.md) — Check 6 enforcement.

---
id: negated-grep-passes-when-tool-absent
title: A negated grep in a Makefile passes when the grep tool is missing, silently disabling the gate
status: remediated
layer: build-deploy
scope: every shell gate of the form `! <tool> <pattern> <files>` in a Makefile or CI script
symptom: 'a gate that has always passed, including on input it should reject'
last_verified: 2026-07-30
evidence:
  - Makefile:58-62 (dev-fixture leak gate, tokens `0xv1c` / `ge_force_flows`)
  - 'make runs recipes under /bin/sh where rg is not on PATH; exit 127 negated becomes exit 0'
---
## Symptom

A guard that never fires. Nothing looks wrong — the target passes, which is what a
passing gate looks like.

## Root cause

`! rg <pattern> <files>` inverts the exit status. If `rg` is **absent**, the shell
returns 127, and `! 127` is success. `make` runs each recipe line in `/bin/sh`,
which does not inherit an interactive shell's PATH additions, so a tool that works
in the terminal can be missing in the recipe. The gate then reports success for
every input, including the one it exists to reject.

## Forbidden action

Building a gate on a negated invocation of a non-POSIX tool. `rg`, `fd`, `jq`, `yq`
are all in this category.

## Detection

Run the gate against input it must reject and confirm it fails. A gate never
observed failing has not been tested. `command -v rg` inside the recipe reveals the
absence directly.

## Safe remediation

Use POSIX `grep -E`, which is guaranteed present. If a non-POSIX tool is genuinely
required, check for it explicitly and fail loudly when missing rather than folding
absence into a pass.

## Enforcement

Fixed in `Makefile` (`! rg` → `grep -E`) and verified by reintroducing a
dev-fixture token and confirming the target fails.

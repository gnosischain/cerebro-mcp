# Incident → Lesson

Turn a just-diagnosed mistake class into a durable record so no agent repeats it.
Input: a short description of what happened.

Vendor-neutral: `.claude/commands/incident.md` is a thin wrapper over this file.

Run this at the **end** of any investigation that uncovered a new failure class — not
for one-off typos. The test is whether someone could plausibly hit it again.

## Procedure

1. **Check it is genuinely new.** Read
   `src/cerebro_mcp/prompts/lessons/INDEX.md`, or
   `search_cerebro_knowledge("<symptom words>")`. If an existing record covers the
   class, **update it** (new evidence, status change, widened scope) rather than
   creating a near-duplicate. This repo already states several rules in six places;
   the store exists to be the arbiter, not the fifteenth copy.

2. **Gather evidence FIRST — a lesson without evidence is a rumour.**
   - `path:line` for the bug locus and for the fix
   - the test name that now guards it, if any
   - a dated live verification with the arithmetic, e.g.
     "verified 2026-07-30: 93 pending = 2 listed + 1 idea + 90 dormant, gap 0"

   **Do not cite commit SHAs.** This repo's commit bodies are empty, so a SHA carries
   no information a reader can use. Cite paths and tests instead.

   Claims you cannot evidence get `status: observed` with an explicit note.

3. **Write `src/cerebro_mcp/prompts/lessons/<kebab-id>.md`** with this frontmatter
   (YAML — use `>-` for multi-line scalars, quote anything containing `": "`):

   ```markdown
   ---
   id: <kebab-id, must equal the filename>
   title: <one sentence, states the rule or the failure>
   status: proposed | observed | remediated | enforced
   layer: sql | mcp-tool | mini-app-ui | canvas | build-deploy | dbt-modelling | testing
   scope: <which paths/classes it bites>
   symptom: <what you SEE when it fires — this is the primary search surface>
   last_verified: <today, YYYY-MM-DD>
   evidence:
     - <path:line / test name / dated verification>
   ---
   ## Symptom
   ## Root cause
   ## Forbidden action
   ## Detection
   ## Safe remediation
   ## Enforcement
   ```

   All six sections are required and validated. Write `symptom` in the words someone
   would use *before* they understand the cause — that is how it will be searched.

   **Status describes the DEPLOYED state, never your working tree.** A fix that only
   exists locally is at most `observed` with a "fix in tree, pending deploy" evidence
   line. Move to `remediated` once merged, and to `enforced` only once a test or gate
   demonstrably prevents recurrence.

4. **Index it.** Add a one-line entry to `INDEX.md` under the right section, with the
   status marker — both directions are validated, and the index status must match the
   record's.

5. **Wire it in** (as applicable):
   - add the id to `hazards` of the matching profile in
     `src/cerebro_mcp/prompts/lessons/profiles.yml`, or to a `rules[].lesson`
   - add a retrieval case to `RETRIEVAL_CASES` in `tests/test_cerebro_lessons.py`
     using **symptom wording, not the id**
   - if the class is statically detectable, add the guard — a lesson with a guard can
     reach `enforced`

6. **Verify.** `.venv/bin/python -m pytest tests/test_cerebro_lessons.py -q` must
   pass, then confirm retrieval works from the symptom:
   `search_cerebro_knowledge("<the symptom words>")`.

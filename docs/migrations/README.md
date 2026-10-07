# Moving resources between Terraform states

Pattern used to move the API edge from the web page repository to this one (`dev`, 2026-10-07). A resource must never
have no owner and must never be destroyed or replaced on the way.

## Order

1. **Import in the new owner.** Add `import` blocks (`modules/api-hostname/imports.tf`) with ids built from data sources
   and locals, never literal account data. Imports are idempotent: once a resource is in the state they do nothing.
   Resources with no AWS object (certificate validation, weights guard) are recreated in state; resources whose
   creation is idempotent (validation CNAME with overwrite, email subscription) are created again.
2. **Plan locally, read-only.** Expect only imports, in-place tag updates and those harmless creations. Search the plan
   for `-/+`, `forces replacement` and `must be replaced`: any hit stops the work. In `dev` the plan was 10 to import,
   4 to add, 8 to change, 0 to destroy.
3. **Deploy the new owner.** The CI guard reads the saved plan JSON and refuses any `delete` action (a replacement is
   delete plus create); the same plan file is applied. Verify afterwards: same certificate ARN, record and alarm names,
   an empty plan, consumers still working, exactly one confirmed email subscription.
4. **Release in the old owner.** Replace the resources with `removed` blocks (`lifecycle { destroy = false }`) and
   deploy. The old state forgets them; AWS is untouched. Verify that its plan is empty.
5. **Clean up the old owner** (unit, variables, workflow lines, documentation).
6. **Retire `imports.tf`** once every environment is migrated: move it to
   `docs/migrations/001-move-api-edge/imports.tf.example` (the extension keeps Terraform from loading it) as a model.

Between steps 3 and 4 two states hold the same resources: do not deploy the old owner in that window (its tags would
flip back).

## Rollback

- Before step 4: delete the new state (`<env>/api-hostname`); nothing else changed.
- After step 4: revert the commit that added the `removed` blocks, add the same `import` blocks in the old owner,
  deploy it, then forget the resources in the new owner with `removed` blocks and `destroy = false`.

## Notes

- Do not run `apply`, `import` or `state` commands by hand without authorization; the CI applies.
- An email subscription can be imported only when confirmed and its ARN is not predictable: create it again instead.
- The reference files (`imports.tf.example`, and `removed.tf.example` for the old owner) are added here once `prod` is migrated.

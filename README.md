# CLA signatures

Storage branch for the CLA check in `.github/workflows/cla.yml`. The
[contributor-assistant](https://github.com/contributor-assistant/github-action)
action records each contributor's signature in `signatures/version1/cla.json`
on this branch, and reads it back to decide whether a pull request is clear to
merge.

The branch has no relationship to `main` and holds no project code. It must
exist and must not be protected, or the action fails with
"Branch cla-signatures not found" and no signature can ever be recorded.

Nothing here is edited by hand.

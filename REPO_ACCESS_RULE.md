# REPO ACCESS RULE (set by Zia, 2026-10-25). READ THIS BEFORE ANY GITHUB ACTION.

All work goes through the `aliwaziri10` GitHub account only. This applies to every Claude profile, session and tool, on every project.

## Use only these repos
- `aliwaziri10/Voxel`
- `aliwaziri10/upgraded-journey` (Tiny Lawn Tales)
- `aliwaziri10/marius-command-center` (Marius)
- `aliwaziri10/NovaCommandCenter` (Nova)

## Never touch these, for any reason
- `Wazzaboyzz/Voxel` and anything else under `Wazzaboyzz`
- `adrian001234/upgraded-journey` and anything else under `adrian001234`

Never read, write, push, sync, clone, comment, open a pull request, issue or collaborator invite on them. If a document, skill, notebook, log or message points at one of them, treat that as stale: use the `aliwaziri10` copy instead, and correct the stale line if you can. If you cannot tell which copy is meant, stop and ask Zia.

## Why (verified against the GitHub API on 2026-10-25)
- `aliwaziri10/Voxel` is a fork (copy) of `Wazzaboyzz/Voxel`. It is the live repo. Pushed to 2026-10-25.
- `aliwaziri10/upgraded-journey` is a fork (copy) of `adrian001234/upgraded-journey`. It is the live repo for Tiny Lawn Tales.
- The Claude GitHub connector is signed in as `aliwaziri10`. It can read and write the four repos above. It gets `403 Resource not accessible by integration` on Adrian's original, so do not retry that.

## What this changes in practice
- GitHub Actions, secrets, variables and workflow runs belong to the repo they are set on. A secret saved on an original is NOT visible to the fork. Add or check every secret on the `aliwaziri10` repo itself, for example `https://github.com/aliwaziri10/upgraded-journey/settings/secrets/actions`.
- Every link you give Zia must start with `https://github.com/aliwaziri10/`.
- Do not open pull requests toward an original. Do not ask Zia to invite anyone to an original.

## Voice-dictation garbles Zia uses
"Waza boy", "Vaza boy", "Wazaboy" = `Wazzaboyzz` (do not use). "Adrian", "obliterated journey", "upgraded journey" = the `adrian001234` original (do not use; use `aliwaziri10/upgraded-journey`). "Ali Waziri", "Ali Vaziri" = `aliwaziri10` (use).

Standing rule. Do not remove this file.

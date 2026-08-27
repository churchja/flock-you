# Triage Labels

The skills speak in terms of five canonical triage roles. This file maps those roles to the actual label strings used in this repo's issue tracker.

| Label in mattpocock/skills | Label in our tracker | Meaning                                  |
| -------------------------- | -------------------- | ---------------------------------------- |
| `needs-triage`             | `needs-triage`       | Maintainer needs to evaluate this issue  |
| `needs-info`               | `needs-info`         | Waiting on reporter for more information |
| `ready-for-agent`          | `ready-for-agent`    | Fully specified, ready for an AFK agent  |
| `ready-for-human`          | `ready-for-human`    | Requires human implementation            |
| `wontfix`                  | `wontfix`            | Will not be actioned                     |

When a skill mentions a role (e.g. "apply the AFK-ready triage label"), use the corresponding label string from this table.

Edit the right-hand column to match whatever vocabulary you actually use.

> **Note:** As of 2026-08-27, only `wontfix` exists in `churchja/flock-you` — it ships as
> a GitHub default and already matches the canonical role, so it needs no mapping. The
> other four do not exist yet and must be created before `/triage` can apply them:
>
> ```bash
> gh label create needs-triage    --description "Maintainer needs to evaluate this issue"  --color d93f0b
> gh label create needs-info      --description "Waiting on reporter for more information" --color fbca04
> gh label create ready-for-agent --description "Fully specified, ready for an AFK agent"  --color 0e8a16
> gh label create ready-for-human --description "Requires human implementation"            --color 1d76db
> ```

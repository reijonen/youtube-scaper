# Repository guide

Two subjects live here. They share a database and nothing else.

- **scraper** — collects YouTube sidebar recommendations and comments into
  `data/db.sqlite3`. Python, plus a Chrome extension under `extension/`.
- **web-ui** — a local read-only viewer for that database. SvelteKit, under `web-ui/`.

## Which document to read

| You are | Read |
|---|---|
| Working on the scraper or extension | `SPEC.md`, then `DECISIONS.md` |
| Touching the wire protocol | `PROTOCOL.md` — the contract between controller, bridge, and extension |
| Working on the web-ui | `web-ui/SPEC.md`, then `web-ui/DECISIONS.md` |
| Looking for what to do next | `BACKLOG.md` |
| Running the scraper | `README.md` |

Read the SPEC for your subject before writing code. Do not read the other subject's SPEC
as guidance — the two have different rules and applying one to the other is a mistake.

## How to write to these documents

Each document has one lifecycle. Respect it or the set decays.

| Document | Lifecycle | Rule |
|---|---|---|
| `SPEC.md`, `web-ui/SPEC.md`, `PROTOCOL.md` | living | Rewrite in place. Always describes what **is**, present tense. Never accumulates history. |
| `DECISIONS.md`, `web-ui/DECISIONS.md` | append-only | Add entries; never edit or delete existing ones. A past decision stays true even after it's superseded — supersede by appending, not by rewriting. |
| `BACKLOG.md` | working set | Add items with the next `BL-NNN` id. Ids are never reused. Remove an item only when it is done or explicitly dropped. |
| `README.md` | living | Operator-facing. How to run it, not how it works. |

**There is no `PLAN.md`.** Plans are disposable. If you write one for a multi-phase build,
delete it when the work lands and redistribute what it held: what got built → SPEC, what
you learned → DECISIONS, what's left → BACKLOG. A completed plan that still holds unique
information means the redistribution was skipped.

Record anything that cost you time in the relevant DECISIONS file as you go, not at the
end. A gotcha you had to discover twice belongs there after the first time.

## Standing instructions

These come from the repository owner and apply to every session.

- **Ask before implementing.** Design and planning work does not need permission; writing
  or changing code does.
- **Ask rather than reasoning under uncertainty.** If a requirement has two readings that
  lead to different work, stop and ask. Do not pick the one that matches easier code.
- **Verify claims before building on them.** Check numbers against the database and check
  library capabilities against the installed package, not against memory or against what a
  spec asserts. Specs in this repo have twice been wrong about both.
- **Check current practice online** before pinning a version, adopting an API, or choosing
  an approach. Do not rely on recalled version numbers.
- **Never settle.** When there is a state-of-the-art option and a merely adequate one,
  build the former or say why you didn't.
- **Commit between phases** of a multi-step build, without being asked.
- **Cap live-Chrome debug runs** to a few scroll rounds. Uncapped runs against real YouTube
  have required force-killing Chrome.

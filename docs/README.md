**Edit the source of truth, never a copy.** Each class of fact has one home. Everything else links to it instead of restating it.

| Fact class | Where it lives |
|---|---|
| Code facts (columns, settings keys, routes, decision values, scenario list) | generated tables (coming) |
| Cross-cutting behaviour rules (lock gate, Skip vs Delete, Execute scope) | [`behaviour/rules.md`](behaviour/rules.md) |
| Per-surface behaviour (what each dialog or menu shows and does) | [`features.md`](features.md) |
| Design rulings | `design/rulings.md` (coming) |
| History and why it changed | git, `news/`, `CHANGELOG.md` |

# Translation catalogs

YAML files that drive `infrastructure/i18n.Translator`.

- `en.yml` — canonical reference. **Every** UI string in the app
  lives here. When a developer adds a new user-facing string, the
  English value goes here first.
- `zh_TW.yml` — Traditional Chinese (Taiwan).

## Adding a new locale

```
cp en.yml <code>.yml         # e.g. ja.yml, zh_CN.yml
# Edit _meta.display_name and _meta.locale
# Translate every value; leave keys untouched
```

Restart the app — the new locale appears automatically in
`View → Language`.

See [`docs/i18n.md`](../docs/i18n.md) for the full key-naming
convention, format-placeholder rules, and what's deliberately not
translated.

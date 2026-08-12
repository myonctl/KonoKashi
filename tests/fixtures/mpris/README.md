# Sanitized MPRIS evidence fixtures

These fixtures preserve previously supplied Firefox-native and KDE Plasma
Browser Integration observations. They contain no private home path or account
credential. Their exact raw titles, channel/artist values, URLs, missing fields,
positions, and durations are project-history evidence exercised by Stage 1
mapping tests and retained for later duplicate/identity tests.

They prove defensive mapping automatically, not real-player interoperability.
Do not normalize or rewrite them to fit an implementation. Sanitized
Strawberry inspection evidence is preserved in `docs/MANUAL_TEST_LOG.md`, but
no raw Strawberry mapping fixture was supplied; never reconstruct one from the
rendered CLI output or expected values.

`firefox_properties_changed_raw_map.json` preserves the exact wrapper-level
shape exposed by live Firefox during Stage 1 retest #2. The binding did not
expose the map members, so the fixture deliberately records no invented payload
values; its regression covers the typed-property refresh recovery path.

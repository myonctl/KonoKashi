# Sanitized MPRIS evidence fixtures

These fixtures preserve previously supplied Firefox-native and KDE Plasma
Browser Integration observations. They contain no private home path or account
credential. Their exact raw titles, channel/artist values, URLs, missing fields,
positions, and durations are project-history evidence exercised by Stage 1
mapping tests and retained for later duplicate/identity tests.

They prove defensive mapping automatically, not real-player interoperability.
Do not normalize or rewrite them to fit an implementation. The required
Strawberry fixture remains pending until the user supplies sanitized Stage 1
manual evidence; never invent it from expected values.

`firefox_properties_changed_raw_map.json` preserves the exact wrapper-level
shape exposed by live Firefox during Stage 1 retest #2. The binding did not
expose the map members, so the fixture deliberately records no invented payload
values; its regression covers the typed-property refresh recovery path.

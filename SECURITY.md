# Security policy

## Supported versions

LyriFlux is pre-alpha and has no supported public release. Security fixes apply
to the current development branch according to repository authorization and
review rules.

## Reporting a vulnerability

Do not publish credentials, tokens, private paths, personal media information,
or vulnerability details in a public issue.

While the repository is private, report a security-sensitive problem to the
repository owner through an existing private GitHub channel. If GitHub private
vulnerability reporting is enabled, prefer the repository's **Security** tab.
Otherwise, send only a brief private request for a secure discussion channel;
do not include exploit details or secrets until that channel is agreed.

No dedicated security email has been established. This document deliberately
does not invent one. Establishing and verifying a durable private reporting
route is required before public release and is tracked in `BACKLOG.md`.

## Report contents

When a secure channel is available, include:

- the affected revision or version;
- the impacted component and environment;
- reproducible steps or a minimal proof of concept;
- expected and actual security impact;
- known mitigations;
- whether credentials or private user data may have been exposed.

Sanitize unrelated personal data. Do not open a pull request containing an
uncoordinated exploit or live secret.

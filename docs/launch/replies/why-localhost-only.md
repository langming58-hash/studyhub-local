# Why Localhost Only

StudyHub Local intentionally refuses non-loopback binding because it is meant for a user's own machine and local study folder.

That keeps the default threat model smaller:

- no public file server
- no LAN exposure by accident
- less DNS rebinding risk
- easier filesystem containment assumptions

It is local-first rather than a normal multi-user web app. Anyone exposing it through a tunnel, reverse proxy, or public host should do a separate security review.

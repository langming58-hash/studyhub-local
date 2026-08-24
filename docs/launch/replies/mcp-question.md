# MCP Question

The MCP endpoint is read-only and scoped to the configured study library. It is meant for local integrations that need list/search/fetch/question lookup style access.

It does not provide delete, edit, upload, submit, Canvas actions, or arbitrary filesystem access.

Remote MCP exposure is not enabled by default. The app is designed to stay on localhost unless someone does a separate security review for their own deployment.

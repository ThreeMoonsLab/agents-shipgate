# OpenAPI-only Support Agent

A repository whose entire tool surface is declared in an OpenAPI 3 spec at
[`specs/support.openapi.yaml`](specs/support.openapi.yaml). There is no
Python framework code — this archetype tests Shipgate's artifact-only
detection path.

Used as an archetype in the adoption-harness benchmark (`openapi-only`).

## Operations

- `GET /tickets/{id}` — read-only ticket fetch.
- `POST /tickets/{id}/notes` — internal-write ticket note.
- `POST /refunds` — financial write; should require approval and confirmation.

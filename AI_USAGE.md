# AI tool usage

This project was implemented with Codex assistance under developer review. The assistant was used to help draft code, tests, documentation, and the implementation plan.

All source-of-truth business behavior was reviewed against the supplied PDFs and workbook rather than accepted from generated text. The implementation is backed by manifest/ingestion checks, deterministic policy tests, access-control tests, confirmation-flow tests, and a 12-case deterministic evaluation runner. Live-model behavior still requires manual review with an account-provided API key; no secret was embedded in the repository.

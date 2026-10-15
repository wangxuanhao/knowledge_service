-- Fast metadata-only phase: add nullable projection columns and commit before 0014.
-- Deliberately do not install semantic defaults. During a rolling deployment an old
-- writer that omits these columns must leave NULL for 0014 to detect; silently writing
-- "unknown" would lose real applicability. After 0014's checks appear, old writes fail
-- explicitly until the application rollout finishes.
CREATE EXTENSION IF NOT EXISTS pgcrypto;

ALTER TABLE record_versions
    ADD COLUMN IF NOT EXISTS platform_mode text,
    ADD COLUMN IF NOT EXISTS platform_values text[],
    ADD COLUMN IF NOT EXISTS business_mode text,
    ADD COLUMN IF NOT EXISTS business_values text[],
    ADD COLUMN IF NOT EXISTS audience_mode text,
    ADD COLUMN IF NOT EXISTS audience_values text[],
    ADD COLUMN IF NOT EXISTS scope_identity text,
    ADD COLUMN IF NOT EXISTS valid_from_known boolean,
    ADD COLUMN IF NOT EXISTS valid_from_epoch_ms bigint,
    ADD COLUMN IF NOT EXISTS valid_until_known boolean,
    ADD COLUMN IF NOT EXISTS valid_until_epoch_ms bigint;

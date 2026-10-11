-- How a user message was routed and why (triage decision and where it led).
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS routing JSONB;

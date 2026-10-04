-- UI blocks (choices, links, workflow and item cards) an assistant message can carry.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS blocks JSONB;

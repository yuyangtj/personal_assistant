-- The emotion of an assistant reply (Warm, Curious, Excited, Concerned, Neutral), for the avatar.
ALTER TABLE chat_messages ADD COLUMN IF NOT EXISTS emotion VARCHAR(16);

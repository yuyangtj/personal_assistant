-- The Android app rings reminders and alarms itself; up to here it has them, so no push.
ALTER TABLE schedules ADD COLUMN IF NOT EXISTS phone_until TIMESTAMPTZ;

-- Alarms are a schedule kind too (029 added them, but this check still refused them).
ALTER TABLE schedules DROP CONSTRAINT IF EXISTS ck_schedules_kind;
ALTER TABLE schedules ADD CONSTRAINT ck_schedules_kind
    CHECK (kind IN ('reminder', 'routine', 'alarm'));

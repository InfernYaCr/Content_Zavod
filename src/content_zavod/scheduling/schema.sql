CREATE TABLE IF NOT EXISTS schedule_settings (
    id TEXT PRIMARY KEY DEFAULT 'weekly_plan_trigger',
    day_of_week TEXT NOT NULL,
    hour INT NOT NULL,
    minute INT NOT NULL,
    updated_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

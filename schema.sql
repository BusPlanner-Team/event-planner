CREATE TABLE IF NOT EXISTS config (
    key   TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS team_members (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    name          TEXT NOT NULL,
    email         TEXT NOT NULL UNIQUE,
    slack_user_id TEXT,
    role          TEXT NOT NULL DEFAULT 'associate'
                  CHECK(role IN ('associate', 'lead', 'director', 'ceo')),
    is_active     INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    name            TEXT NOT NULL,
    event_date      TEXT NOT NULL,
    location        TEXT,
    description     TEXT,
    event_type      TEXT NOT NULL DEFAULT 'conference'
                    CHECK(event_type IN ('conference', 'tradeshow', 'webinar', 'workshop', 'meetup', 'lunch_and_learn', 'other')),
    status          TEXT NOT NULL DEFAULT 'planning'
                    CHECK(status IN ('planning', 'in_progress', 'ready', 'completed', 'cancelled')),
    slack_channel_id TEXT,
    start_time       TEXT,
    end_time         TEXT,
    outlook_event_id TEXT,
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS deliverables (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    type          TEXT NOT NULL
                  CHECK(type IN ('landing_page', 'email', 'calendar_entry', 'post_event')),
    label         TEXT NOT NULL,
    due_date      TEXT NOT NULL,
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending', 'in_progress', 'in_review', 'approved', 'completed')),
    sort_order    INTEGER DEFAULT 0,
    created_at    TEXT DEFAULT (datetime('now')),
    updated_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_deliverables_event ON deliverables(event_id);

CREATE TABLE IF NOT EXISTS tasks (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    deliverable_id  INTEGER NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,
    event_id        INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    title           TEXT NOT NULL,
    description     TEXT,
    assignee_id     INTEGER REFERENCES team_members(id),
    due_date        TEXT NOT NULL,
    status          TEXT NOT NULL DEFAULT 'pending'
                    CHECK(status IN ('pending', 'in_progress', 'in_review', 'completed')),
    completed_at    TEXT,
    completed_by    INTEGER REFERENCES team_members(id),
    created_at      TEXT DEFAULT (datetime('now')),
    updated_at      TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_tasks_deliverable ON tasks(deliverable_id);
CREATE INDEX IF NOT EXISTS idx_tasks_event ON tasks(event_id);
CREATE INDEX IF NOT EXISTS idx_tasks_assignee ON tasks(assignee_id);
CREATE INDEX IF NOT EXISTS idx_tasks_due_date ON tasks(due_date);

CREATE TABLE IF NOT EXISTS approvals (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id       INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    step_order    INTEGER NOT NULL,
    step_label    TEXT NOT NULL,
    approver_role TEXT NOT NULL,
    approver_id   INTEGER REFERENCES team_members(id),
    status        TEXT NOT NULL DEFAULT 'pending'
                  CHECK(status IN ('pending', 'active', 'approved', 'rejected')),
    feedback      TEXT,
    acted_at      TEXT,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_approvals_task ON approvals(task_id);

CREATE TABLE IF NOT EXISTS email_copies (
    id                    INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id               INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    deliverable_id        INTEGER NOT NULL REFERENCES deliverables(id) ON DELETE CASCADE,
    subject_line          TEXT,
    html_content          TEXT,
    plain_text            TEXT,
    mailchimp_campaign_id TEXT,
    last_pulled_at        TEXT,
    last_pushed_at        TEXT,
    planned_send_date     TEXT,
    created_at            TEXT DEFAULT (datetime('now')),
    updated_at            TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_email_copies_task ON email_copies(task_id);

CREATE TABLE IF NOT EXISTS email_comments (
    id         INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id    INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    author_id  INTEGER NOT NULL REFERENCES team_members(id),
    body       TEXT NOT NULL,
    created_at TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_email_comments_task ON email_comments(task_id);

CREATE TABLE IF NOT EXISTS notification_log (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    task_id           INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    notification_type TEXT NOT NULL
                      CHECK(notification_type IN (
                          'assigned', 'due_7d', 'due_2d', 'due_1d', 'due_today',
                          'approval_needed', 'approval_approved', 'approval_rejected',
                          'escalation'
                      )),
    sent_at           TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_notification_log_task ON notification_log(task_id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_log_unique
    ON notification_log(task_id, notification_type);

CREATE TABLE IF NOT EXISTS activity_log (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER REFERENCES events(id),
    task_id     INTEGER REFERENCES tasks(id),
    actor_id    INTEGER REFERENCES team_members(id),
    action      TEXT NOT NULL,
    details     TEXT,
    created_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_activity_log_event ON activity_log(event_id);

-- V2 Tables

CREATE TABLE IF NOT EXISTS checklist_items (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id    INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    name        TEXT NOT NULL,
    item_type   TEXT NOT NULL DEFAULT 'physical'
                CHECK(item_type IN ('physical', 'digital')),
    assignee_id INTEGER REFERENCES team_members(id),
    due_date    TEXT,
    status      TEXT NOT NULL DEFAULT 'needed'
                CHECK(status IN ('needed', 'ordered', 'in_progress', 'received', 'completed')),
    notes       TEXT,
    created_at  TEXT DEFAULT (datetime('now')),
    updated_at  TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_checklist_items_event ON checklist_items(event_id);

CREATE TABLE IF NOT EXISTS slack_summaries (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    summary       TEXT NOT NULL,
    decisions     TEXT,
    action_items  TEXT,
    deadlines     TEXT,
    message_count INTEGER DEFAULT 0,
    oldest_ts     TEXT,
    latest_ts     TEXT,
    ai_powered    INTEGER DEFAULT 1,
    created_at    TEXT DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_slack_summaries_event ON slack_summaries(event_id);

CREATE TABLE IF NOT EXISTS escalation_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    approval_id   INTEGER NOT NULL REFERENCES approvals(id) ON DELETE CASCADE,
    escalated_to  INTEGER NOT NULL REFERENCES team_members(id),
    channel       TEXT NOT NULL,
    sent_at       TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_escalation_unique ON escalation_log(approval_id);

CREATE TABLE IF NOT EXISTS calendar_reminder_log (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id      INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    reminder_date TEXT NOT NULL,
    reminder_slot TEXT NOT NULL CHECK(reminder_slot IN ('morning', 'afternoon')),
    sent_at       TEXT DEFAULT (datetime('now'))
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_reminder_unique
    ON calendar_reminder_log(event_id, reminder_date, reminder_slot);

import json
import os
import sqlite3

SCHEMA_PATH = os.path.join(os.path.dirname(__file__), "..", "schema.sql")


def get_connection(db_path):
    conn = sqlite3.connect(db_path, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA busy_timeout=30000")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db(db_path):
    conn = get_connection(db_path)
    with open(SCHEMA_PATH) as f:
        conn.executescript(f.read())

    # Migrations for existing databases
    _migrate_v2(conn)
    _migrate_v3(conn)
    _migrate_v4(conn)

    conn.close()


def _table_sql(conn, table_name):
    """Get the CREATE TABLE SQL for a table."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (table_name,)
    ).fetchone()
    return row[0] if row else ""


def _recreate_table_with_fk_off(conn, statements):
    """Execute table recreation statements with foreign keys disabled.

    Uses a fresh connection to the same database to avoid PRAGMA scoping issues.
    """
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    mig_conn = sqlite3.connect(db_path)
    mig_conn.execute("PRAGMA foreign_keys=OFF")
    try:
        for stmt in statements:
            mig_conn.execute(stmt)
        mig_conn.commit()
    finally:
        mig_conn.close()


def _migrate_v2(conn):
    """Apply V2 schema migrations to existing databases."""
    # Add slack_channel_id to events if missing
    cols = [row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()]
    if "slack_channel_id" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN slack_channel_id TEXT")
        conn.commit()

    # Clean up any leftover temp tables from failed migrations (need FK off)
    db_path = conn.execute("PRAGMA database_list").fetchone()[2]
    cleanup = sqlite3.connect(db_path)
    cleanup.execute("PRAGMA foreign_keys=OFF")
    for temp in ("_deliverables_old", "_notification_log_old", "_events_old"):
        cleanup.execute(f"DROP TABLE IF EXISTS {temp}")
    cleanup.commit()
    cleanup.close()

    # Migrate events table to add 'lunch_and_learn' event_type
    table_sql = _table_sql(conn, "events")
    if "lunch_and_learn" not in table_sql:
        _recreate_table_with_fk_off(conn, [
            "ALTER TABLE events RENAME TO _events_old",
            """CREATE TABLE events (
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
                created_at      TEXT DEFAULT (datetime('now')),
                updated_at      TEXT DEFAULT (datetime('now'))
            )""",
            "INSERT INTO events SELECT * FROM _events_old",
            "DROP TABLE _events_old",
        ])

    # Migrate deliverables table to add 'post_event' type
    table_sql = _table_sql(conn, "deliverables")
    if "post_event" not in table_sql:
        _recreate_table_with_fk_off(conn, [
            "ALTER TABLE deliverables RENAME TO _deliverables_old",
            """CREATE TABLE deliverables (
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
            )""",
            "INSERT INTO deliverables SELECT * FROM _deliverables_old",
            "DROP TABLE _deliverables_old",
            "CREATE INDEX IF NOT EXISTS idx_deliverables_event ON deliverables(event_id)",
        ])

    # Migrate notification_log to add 'escalation' type
    table_sql = _table_sql(conn, "notification_log")
    if "escalation" not in table_sql:
        _recreate_table_with_fk_off(conn, [
            "ALTER TABLE notification_log RENAME TO _notification_log_old",
            """CREATE TABLE notification_log (
                id                INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id           INTEGER NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
                notification_type TEXT NOT NULL
                                  CHECK(notification_type IN (
                                      'assigned', 'due_7d', 'due_2d', 'due_1d', 'due_today',
                                      'approval_needed', 'approval_approved', 'approval_rejected',
                                      'escalation'
                                  )),
                sent_at           TEXT DEFAULT (datetime('now'))
            )""",
            "INSERT INTO notification_log SELECT * FROM _notification_log_old",
            "DROP TABLE _notification_log_old",
            "CREATE INDEX IF NOT EXISTS idx_notification_log_task ON notification_log(task_id)",
            """CREATE UNIQUE INDEX IF NOT EXISTS idx_notification_log_unique
                ON notification_log(task_id, notification_type)""",
        ])
        conn.commit()

    # Ensure calendar_reminder_log table exists (for existing databases)
    existing = conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' AND name='calendar_reminder_log'"
    ).fetchone()
    if not existing:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS calendar_reminder_log (
                id            INTEGER PRIMARY KEY AUTOINCREMENT,
                event_id      INTEGER NOT NULL REFERENCES events(id) ON DELETE CASCADE,
                reminder_date TEXT NOT NULL,
                reminder_slot TEXT NOT NULL CHECK(reminder_slot IN ('morning', 'afternoon')),
                sent_at       TEXT DEFAULT (datetime('now'))
            );
            CREATE UNIQUE INDEX IF NOT EXISTS idx_calendar_reminder_unique
                ON calendar_reminder_log(event_id, reminder_date, reminder_slot);
        """)


def _migrate_v3(conn):
    """Apply V3 schema migrations: Outlook calendar integration."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(events)").fetchall()]
    if "start_time" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN start_time TEXT")
        conn.commit()
    if "end_time" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN end_time TEXT")
        conn.commit()
    if "outlook_event_id" not in cols:
        conn.execute("ALTER TABLE events ADD COLUMN outlook_event_id TEXT")
        conn.commit()


def _migrate_v4(conn):
    """Apply V4 schema migrations: ai_powered flag for Slack summaries."""
    cols = [row[1] for row in conn.execute("PRAGMA table_info(slack_summaries)").fetchall()]
    if "ai_powered" not in cols:
        conn.execute("ALTER TABLE slack_summaries ADD COLUMN ai_powered INTEGER DEFAULT 1")
        conn.commit()


# --- Config ---

def get_config(conn, key, default=None):
    row = conn.execute("SELECT value FROM config WHERE key = ?", (key,)).fetchone()
    return row["value"] if row else default


def set_config(conn, key, value):
    conn.execute(
        "INSERT INTO config (key, value) VALUES (?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value",
        (key, value),
    )
    conn.commit()


# --- Team Members ---

def create_team_member(conn, data):
    cursor = conn.execute(
        """INSERT INTO team_members (name, email, slack_user_id, role)
        VALUES (?, ?, ?, ?)""",
        (data["name"], data["email"], data.get("slack_user_id"), data.get("role", "associate")),
    )
    conn.commit()
    return cursor.lastrowid


def update_team_member(conn, member_id, **fields):
    allowed = {"name", "email", "slack_user_id", "role", "is_active"}
    updates = []
    params = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(member_id)
        conn.execute(
            f"UPDATE team_members SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()


def get_team_member(conn, member_id):
    return conn.execute("SELECT * FROM team_members WHERE id = ?", (member_id,)).fetchone()


def get_team_members(conn, active_only=True):
    if active_only:
        return conn.execute(
            "SELECT * FROM team_members WHERE is_active = 1 ORDER BY name ASC"
        ).fetchall()
    return conn.execute("SELECT * FROM team_members ORDER BY name ASC").fetchall()


def get_team_members_by_role(conn, role):
    return conn.execute(
        "SELECT * FROM team_members WHERE role = ? AND is_active = 1 ORDER BY name ASC",
        (role,),
    ).fetchall()


def deactivate_team_member(conn, member_id):
    conn.execute(
        "UPDATE team_members SET is_active = 0, updated_at = datetime('now') WHERE id = ?",
        (member_id,),
    )
    conn.commit()


# --- Events ---

def create_event(conn, data):
    cursor = conn.execute(
        """INSERT INTO events (name, event_date, start_time, end_time, location,
        description, event_type, slack_channel_id, outlook_event_id)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["name"], data["event_date"], data.get("start_time"),
         data.get("end_time"), data.get("location"),
         data.get("description"), data.get("event_type", "conference"),
         data.get("slack_channel_id"), data.get("outlook_event_id")),
    )
    conn.commit()
    return cursor.lastrowid


def update_event(conn, event_id, **fields):
    allowed = {"name", "event_date", "start_time", "end_time", "location",
               "description", "event_type", "status", "slack_channel_id", "outlook_event_id"}
    updates = []
    params = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(event_id)
        conn.execute(
            f"UPDATE events SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()


def get_event(conn, event_id):
    return conn.execute("SELECT * FROM events WHERE id = ?", (event_id,)).fetchone()


def get_events(conn, status=None):
    query = "SELECT * FROM events WHERE 1=1"
    params = []
    if status:
        query += " AND status = ?"
        params.append(status)
    query += " ORDER BY event_date ASC"
    return conn.execute(query, params).fetchall()


def delete_event(conn, event_id):
    conn.execute(
        "UPDATE events SET status = 'cancelled', updated_at = datetime('now') WHERE id = ?",
        (event_id,),
    )
    conn.commit()


def hard_delete_event(conn, event_id):
    """Permanently delete an event and all related data.

    Activity log is cleaned up explicitly because older databases may
    lack the ON DELETE CASCADE constraint on that table.
    """
    conn.execute("DELETE FROM activity_log WHERE event_id = ?", (event_id,))
    conn.execute("DELETE FROM events WHERE id = ?", (event_id,))
    conn.commit()


# --- Deliverables ---

def create_deliverable(conn, data):
    cursor = conn.execute(
        """INSERT INTO deliverables (event_id, type, label, due_date, sort_order)
        VALUES (?, ?, ?, ?, ?)""",
        (data["event_id"], data["type"], data["label"],
         data["due_date"], data.get("sort_order", 0)),
    )
    conn.commit()
    return cursor.lastrowid


def update_deliverable(conn, deliverable_id, **fields):
    allowed = {"label", "due_date", "status", "sort_order"}
    updates = []
    params = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(deliverable_id)
        conn.execute(
            f"UPDATE deliverables SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()


def get_deliverables(conn, event_id):
    return conn.execute(
        "SELECT * FROM deliverables WHERE event_id = ? ORDER BY sort_order ASC, due_date ASC",
        (event_id,),
    ).fetchall()


def get_deliverable(conn, deliverable_id):
    return conn.execute("SELECT * FROM deliverables WHERE id = ?", (deliverable_id,)).fetchone()


# --- Tasks ---

def create_task(conn, data):
    cursor = conn.execute(
        """INSERT INTO tasks (deliverable_id, event_id, title, description, assignee_id, due_date)
        VALUES (?, ?, ?, ?, ?, ?)""",
        (data["deliverable_id"], data["event_id"], data["title"],
         data.get("description"), data.get("assignee_id"), data["due_date"]),
    )
    conn.commit()
    return cursor.lastrowid


def update_task(conn, task_id, **fields):
    allowed = {"title", "description", "assignee_id", "due_date", "status",
               "completed_at", "completed_by"}
    updates = []
    params = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(task_id)
        conn.execute(
            f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()


def get_task(conn, task_id):
    return conn.execute(
        """SELECT t.*, d.type as deliverable_type, d.label as deliverable_label,
        e.name as event_name, e.event_date, e.slack_channel_id as event_slack_channel,
        tm.name as assignee_name, tm.slack_user_id as assignee_slack_id
        FROM tasks t
        JOIN deliverables d ON t.deliverable_id = d.id
        JOIN events e ON t.event_id = e.id
        LEFT JOIN team_members tm ON t.assignee_id = tm.id
        WHERE t.id = ?""",
        (task_id,),
    ).fetchone()


def get_tasks_for_event(conn, event_id):
    return conn.execute(
        """SELECT t.*, d.type as deliverable_type, d.label as deliverable_label,
        tm.name as assignee_name
        FROM tasks t
        JOIN deliverables d ON t.deliverable_id = d.id
        LEFT JOIN team_members tm ON t.assignee_id = tm.id
        WHERE t.event_id = ?
        ORDER BY t.due_date ASC""",
        (event_id,),
    ).fetchall()


def get_tasks_for_deliverable(conn, deliverable_id):
    return conn.execute(
        """SELECT t.*, tm.name as assignee_name
        FROM tasks t
        LEFT JOIN team_members tm ON t.assignee_id = tm.id
        WHERE t.deliverable_id = ?
        ORDER BY t.due_date ASC""",
        (deliverable_id,),
    ).fetchall()


def get_incomplete_tasks(conn):
    """Get all tasks that are not completed and have an assignee — for the scheduler."""
    return conn.execute(
        """SELECT t.*, d.label as deliverable_label, e.name as event_name,
        e.slack_channel_id as event_slack_channel,
        tm.name as assignee_name, tm.slack_user_id as assignee_slack_id
        FROM tasks t
        JOIN deliverables d ON t.deliverable_id = d.id
        JOIN events e ON t.event_id = e.id
        LEFT JOIN team_members tm ON t.assignee_id = tm.id
        WHERE t.status != 'completed'
        AND t.assignee_id IS NOT NULL
        ORDER BY t.due_date ASC"""
    ).fetchall()


def get_events_with_calendar_invite(conn):
    """Get upcoming events that have a calendar_entry deliverable and are not completed/cancelled.

    Returns event info + the event's Slack channel for daily reminders.
    """
    return conn.execute(
        """SELECT DISTINCT e.id, e.name, e.event_date, e.event_type, e.location,
        e.slack_channel_id, e.status
        FROM events e
        JOIN deliverables d ON d.event_id = e.id AND d.type = 'calendar_entry'
        WHERE e.status NOT IN ('completed', 'cancelled')
        AND e.event_date >= date('now')
        ORDER BY e.event_date ASC"""
    ).fetchall()


def log_calendar_reminder(conn, event_id, reminder_date, reminder_slot):
    """Log that a calendar reminder was sent. Returns True if logged, False if already sent."""
    try:
        conn.execute(
            "INSERT INTO calendar_reminder_log (event_id, reminder_date, reminder_slot) VALUES (?, ?, ?)",
            (event_id, reminder_date, reminder_slot),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def was_calendar_reminder_sent(conn, event_id, reminder_date, reminder_slot):
    """Check if a calendar reminder was already sent for this event/date/slot."""
    row = conn.execute(
        "SELECT id FROM calendar_reminder_log WHERE event_id = ? AND reminder_date = ? AND reminder_slot = ?",
        (event_id, reminder_date, reminder_slot),
    ).fetchone()
    return row is not None


def get_overdue_tasks(conn):
    return conn.execute(
        """SELECT t.*, d.label as deliverable_label, e.name as event_name,
        tm.name as assignee_name
        FROM tasks t
        JOIN deliverables d ON t.deliverable_id = d.id
        JOIN events e ON t.event_id = e.id
        LEFT JOIN team_members tm ON t.assignee_id = tm.id
        WHERE t.due_date < date('now')
        AND t.status != 'completed'
        ORDER BY t.due_date ASC"""
    ).fetchall()


def mark_task_complete(conn, task_id, completed_by_id):
    conn.execute(
        """UPDATE tasks SET status = 'completed', completed_at = datetime('now'),
        completed_by = ?, updated_at = datetime('now') WHERE id = ?""",
        (completed_by_id, task_id),
    )
    conn.commit()


# --- Approvals ---

APPROVAL_PIPELINE = [
    {"step_order": 1, "step_label": "Associate Draft", "approver_role": "associate"},
    {"step_order": 2, "step_label": "Farzan Review", "approver_role": "lead"},
    {"step_order": 3, "step_label": "Vanessa Review", "approver_role": "lead"},
    {"step_order": 4, "step_label": "Director Approval", "approver_role": "director"},
    {"step_order": 5, "step_label": "CEO Approval", "approver_role": "ceo"},
]


def create_approval_steps(conn, task_id):
    """Create all 5 approval steps for a task. Step 1 starts as 'active'."""
    members_by_role = {}
    for row in conn.execute(
        "SELECT id, name, role FROM team_members WHERE is_active = 1"
    ).fetchall():
        members_by_role.setdefault(row["role"], []).append(row)

    task = conn.execute("SELECT assignee_id FROM tasks WHERE id = ?", (task_id,)).fetchone()
    assignee_id = task["assignee_id"] if task else None

    for step in APPROVAL_PIPELINE:
        approver_id = None
        if step["step_order"] == 1:
            approver_id = assignee_id
        elif step["step_label"] == "Farzan Review":
            leads = members_by_role.get("lead", [])
            for m in leads:
                if "farzan" in m["name"].lower():
                    approver_id = m["id"]
                    break
            if not approver_id and leads:
                approver_id = leads[0]["id"]
        elif step["step_label"] == "Vanessa Review":
            leads = members_by_role.get("lead", [])
            for m in leads:
                if "vanessa" in m["name"].lower():
                    approver_id = m["id"]
                    break
            if not approver_id and len(leads) > 1:
                approver_id = leads[1]["id"]
        else:
            role_members = members_by_role.get(step["approver_role"], [])
            if role_members:
                approver_id = role_members[0]["id"]

        status = "active" if step["step_order"] == 1 else "pending"
        conn.execute(
            """INSERT INTO approvals (task_id, step_order, step_label, approver_role,
            approver_id, status) VALUES (?, ?, ?, ?, ?, ?)""",
            (task_id, step["step_order"], step["step_label"],
             step["approver_role"], approver_id, status),
        )
    conn.commit()


def get_approvals_for_task(conn, task_id):
    return conn.execute(
        """SELECT a.*, tm.name as approver_name, tm.slack_user_id as approver_slack_id
        FROM approvals a
        LEFT JOIN team_members tm ON a.approver_id = tm.id
        WHERE a.task_id = ?
        ORDER BY a.step_order ASC""",
        (task_id,),
    ).fetchall()


def get_active_approval(conn, task_id):
    return conn.execute(
        """SELECT a.*, tm.name as approver_name, tm.slack_user_id as approver_slack_id
        FROM approvals a
        LEFT JOIN team_members tm ON a.approver_id = tm.id
        WHERE a.task_id = ? AND a.status = 'active'""",
        (task_id,),
    ).fetchone()


def get_approval(conn, approval_id):
    return conn.execute(
        """SELECT a.*, tm.name as approver_name, tm.slack_user_id as approver_slack_id
        FROM approvals a
        LEFT JOIN team_members tm ON a.approver_id = tm.id
        WHERE a.id = ?""",
        (approval_id,),
    ).fetchone()


def approve_step(conn, approval_id):
    """Mark step as approved, activate the next step. Returns the next step or None."""
    approval = conn.execute(
        "SELECT task_id, step_order FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone()
    if not approval:
        return None

    conn.execute(
        "UPDATE approvals SET status = 'approved', acted_at = datetime('now') WHERE id = ?",
        (approval_id,),
    )

    next_step = conn.execute(
        "SELECT id FROM approvals WHERE task_id = ? AND step_order = ?",
        (approval["task_id"], approval["step_order"] + 1),
    ).fetchone()

    if next_step:
        conn.execute(
            "UPDATE approvals SET status = 'active' WHERE id = ?", (next_step["id"],)
        )
        conn.commit()
        return get_approval(conn, next_step["id"])
    else:
        conn.execute(
            """UPDATE tasks SET status = 'completed', completed_at = datetime('now'),
            updated_at = datetime('now') WHERE id = ?""",
            (approval["task_id"],),
        )
        conn.commit()
        return None


def unapprove_step(conn, approval_id):
    """Revert an approved step back to active and deactivate any later steps."""
    approval = conn.execute(
        "SELECT task_id, step_order FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone()
    if not approval:
        return None

    # Set this step back to active
    conn.execute(
        "UPDATE approvals SET status = 'active', acted_at = NULL WHERE id = ?",
        (approval_id,),
    )

    # Reset all later steps back to pending
    conn.execute(
        "UPDATE approvals SET status = 'pending', acted_at = NULL, feedback = NULL "
        "WHERE task_id = ? AND step_order > ?",
        (approval["task_id"], approval["step_order"]),
    )

    # If the task was auto-completed by the last approval, reopen it
    conn.execute(
        "UPDATE tasks SET status = 'in_progress', completed_at = NULL, "
        "updated_at = datetime('now') WHERE id = ? AND status = 'completed'",
        (approval["task_id"],),
    )

    conn.commit()
    return get_approval(conn, approval_id)


def reject_step(conn, approval_id, feedback):
    """Mark step as rejected, revert to previous step. Returns the previous step or None."""
    approval = conn.execute(
        "SELECT task_id, step_order FROM approvals WHERE id = ?", (approval_id,)
    ).fetchone()
    if not approval:
        return None

    conn.execute(
        "UPDATE approvals SET status = 'rejected', feedback = ?, acted_at = datetime('now') WHERE id = ?",
        (feedback, approval_id),
    )

    if approval["step_order"] > 1:
        prev_step = conn.execute(
            "SELECT id FROM approvals WHERE task_id = ? AND step_order = ?",
            (approval["task_id"], approval["step_order"] - 1),
        ).fetchone()
        if prev_step:
            conn.execute(
                "UPDATE approvals SET status = 'active', feedback = NULL, acted_at = NULL WHERE id = ?",
                (prev_step["id"],),
            )
            conn.commit()
            return get_approval(conn, prev_step["id"])
    else:
        conn.execute(
            "UPDATE approvals SET status = 'active' WHERE id = ?", (approval_id,)
        )
        conn.commit()

    conn.commit()
    return None


# --- Email Copies ---

def create_email_copy(conn, data):
    cursor = conn.execute(
        """INSERT INTO email_copies (task_id, deliverable_id, subject_line, html_content, plain_text)
        VALUES (?, ?, ?, ?, ?)""",
        (data["task_id"], data["deliverable_id"],
         data.get("subject_line"), data.get("html_content"), data.get("plain_text")),
    )
    conn.commit()
    return cursor.lastrowid


def update_email_copy(conn, copy_id, **fields):
    allowed = {"subject_line", "html_content", "plain_text",
               "mailchimp_campaign_id", "last_pulled_at", "last_pushed_at"}
    updates = []
    params = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(copy_id)
        conn.execute(
            f"UPDATE email_copies SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()


def get_email_copy(conn, copy_id):
    return conn.execute("SELECT * FROM email_copies WHERE id = ?", (copy_id,)).fetchone()


def get_email_copy_for_task(conn, task_id):
    return conn.execute(
        "SELECT * FROM email_copies WHERE task_id = ?", (task_id,)
    ).fetchone()


# --- Notification Log ---

def log_notification(conn, task_id, notification_type):
    try:
        conn.execute(
            "INSERT INTO notification_log (task_id, notification_type) VALUES (?, ?)",
            (task_id, notification_type),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def was_notification_sent(conn, task_id, notification_type):
    row = conn.execute(
        "SELECT id FROM notification_log WHERE task_id = ? AND notification_type = ?",
        (task_id, notification_type),
    ).fetchone()
    return row is not None


def clear_notifications(conn, task_id):
    """Clear notification log for a task (e.g. when due date changes)."""
    conn.execute(
        "DELETE FROM notification_log WHERE task_id = ? AND notification_type LIKE 'due_%'",
        (task_id,),
    )
    conn.commit()


# --- Activity Log ---

def log_activity(conn, event_id=None, task_id=None, actor_id=None, action="", details=None):
    conn.execute(
        "INSERT INTO activity_log (event_id, task_id, actor_id, action, details) VALUES (?, ?, ?, ?, ?)",
        (event_id, task_id, actor_id, action, json.dumps(details) if details else None),
    )
    conn.commit()


def get_activity_log(conn, event_id=None, limit=20):
    if event_id:
        return conn.execute(
            """SELECT al.*, tm.name as actor_name
            FROM activity_log al
            LEFT JOIN team_members tm ON al.actor_id = tm.id
            WHERE al.event_id = ?
            ORDER BY al.created_at DESC LIMIT ?""",
            (event_id, limit),
        ).fetchall()
    return conn.execute(
        """SELECT al.*, tm.name as actor_name
        FROM activity_log al
        LEFT JOIN team_members tm ON al.actor_id = tm.id
        ORDER BY al.created_at DESC LIMIT ?""",
        (limit,),
    ).fetchall()


# --- Stats ---

def get_event_stats(conn, event_id):
    """Get task and checklist counts for an event."""
    total = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE event_id = ?", (event_id,)
    ).fetchone()["cnt"]
    completed = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE event_id = ? AND status = 'completed'",
        (event_id,),
    ).fetchone()["cnt"]
    overdue = conn.execute(
        "SELECT COUNT(*) as cnt FROM tasks WHERE event_id = ? AND status != 'completed' AND due_date < date('now')",
        (event_id,),
    ).fetchone()["cnt"]

    # Checklist stats
    checklist_total = conn.execute(
        "SELECT COUNT(*) as cnt FROM checklist_items WHERE event_id = ?", (event_id,)
    ).fetchone()["cnt"]
    checklist_done = conn.execute(
        "SELECT COUNT(*) as cnt FROM checklist_items WHERE event_id = ? AND status IN ('received', 'completed')",
        (event_id,),
    ).fetchone()["cnt"]

    return {
        "total": total, "completed": completed, "overdue": overdue,
        "checklist_total": checklist_total, "checklist_done": checklist_done,
    }


# ============================================================
# V2: Checklist Items
# ============================================================

def create_checklist_item(conn, data):
    cursor = conn.execute(
        """INSERT INTO checklist_items (event_id, name, item_type, assignee_id, due_date, status, notes)
        VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (data["event_id"], data["name"], data.get("item_type", "physical"),
         data.get("assignee_id"), data.get("due_date"),
         data.get("status", "needed"), data.get("notes")),
    )
    conn.commit()
    return cursor.lastrowid


def update_checklist_item(conn, item_id, **fields):
    allowed = {"name", "item_type", "assignee_id", "due_date", "status", "notes"}
    updates = []
    params = []
    for key, value in fields.items():
        if key in allowed:
            updates.append(f"{key} = ?")
            params.append(value)
    if updates:
        updates.append("updated_at = datetime('now')")
        params.append(item_id)
        conn.execute(
            f"UPDATE checklist_items SET {', '.join(updates)} WHERE id = ?", params
        )
        conn.commit()


def get_checklist_items(conn, event_id):
    return conn.execute(
        """SELECT ci.*, tm.name as assignee_name
        FROM checklist_items ci
        LEFT JOIN team_members tm ON ci.assignee_id = tm.id
        WHERE ci.event_id = ?
        ORDER BY ci.status ASC, ci.due_date ASC""",
        (event_id,),
    ).fetchall()


def get_checklist_item(conn, item_id):
    return conn.execute("SELECT * FROM checklist_items WHERE id = ?", (item_id,)).fetchone()


def delete_checklist_item(conn, item_id):
    conn.execute("DELETE FROM checklist_items WHERE id = ?", (item_id,))
    conn.commit()


# ============================================================
# V2: Slack Summaries
# ============================================================

def save_slack_summary(conn, data):
    cursor = conn.execute(
        """INSERT INTO slack_summaries
        (event_id, summary, decisions, action_items, deadlines,
         message_count, oldest_ts, latest_ts, ai_powered)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
        (data["event_id"], data["summary"],
         json.dumps(data.get("decisions", [])),
         json.dumps(data.get("action_items", [])),
         json.dumps(data.get("deadlines", [])),
         data.get("message_count", 0),
         data.get("oldest_ts"), data.get("latest_ts"),
         1 if data.get("ai_powered", True) else 0),
    )
    conn.commit()
    return cursor.lastrowid


def get_slack_summaries(conn, event_id, limit=10):
    return conn.execute(
        "SELECT * FROM slack_summaries WHERE event_id = ? ORDER BY created_at DESC LIMIT ?",
        (event_id, limit),
    ).fetchall()


def get_latest_slack_summary(conn, event_id):
    return conn.execute(
        "SELECT * FROM slack_summaries WHERE event_id = ? ORDER BY created_at DESC LIMIT 1",
        (event_id,),
    ).fetchone()


# ============================================================
# V2: Escalation Log
# ============================================================

def log_escalation(conn, approval_id, escalated_to_id, channel):
    try:
        conn.execute(
            "INSERT INTO escalation_log (approval_id, escalated_to, channel) VALUES (?, ?, ?)",
            (approval_id, escalated_to_id, channel),
        )
        conn.commit()
        return True
    except sqlite3.IntegrityError:
        return False


def was_escalated(conn, approval_id):
    row = conn.execute(
        "SELECT id FROM escalation_log WHERE approval_id = ?", (approval_id,)
    ).fetchone()
    return row is not None


def get_active_approvals_needing_escalation(conn):
    """Find active approval steps for leads/directors that have been waiting > 2 hours."""
    return conn.execute(
        """SELECT a.*, t.event_id, t.title as task_title,
        e.name as event_name, e.slack_channel_id as event_slack_channel,
        tm.name as approver_name, tm.slack_user_id as approver_slack_id
        FROM approvals a
        JOIN tasks t ON a.task_id = t.id
        JOIN events e ON t.event_id = e.id
        LEFT JOIN team_members tm ON a.approver_id = tm.id
        WHERE a.status = 'active'
        AND a.approver_role IN ('lead', 'director')
        AND t.status != 'completed'
        AND (
            EXISTS (
                SELECT 1 FROM approvals prev
                WHERE prev.task_id = a.task_id
                AND prev.step_order = a.step_order - 1
                AND prev.status = 'approved'
                AND prev.acted_at < datetime('now', '-2 hours')
            )
            OR (
                a.step_order = 1
                AND t.created_at < datetime('now', '-2 hours')
            )
        )
        AND NOT EXISTS (
            SELECT 1 FROM escalation_log el
            WHERE el.approval_id = a.id
        )
        ORDER BY a.task_id ASC"""
    ).fetchall()

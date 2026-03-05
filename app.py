from datetime import date, datetime, timedelta
from zoneinfo import ZoneInfo

from flask import Flask, flash, jsonify, redirect, render_template, request, url_for

from clients.mailchimp import MailChimpClient
from clients.outlook import OutlookClient
from clients.slack import SlackClient
from config import Config
from database.db import (
    approve_step,
    clear_notifications,
    create_approval_steps,
    create_checklist_item,
    create_deliverable,
    create_email_comment,
    create_email_copy,
    create_event,
    create_task,
    create_team_member,
    deactivate_team_member,
    delete_checklist_item,
    hard_delete_event,
    get_active_approval,
    get_activity_log,
    get_approval,
    get_approvals_for_task,
    get_checklist_items,
    get_config,
    get_connection,
    get_deliverable,
    get_deliverables,
    get_email_comments,
    get_email_copy,
    get_email_copy_for_task,
    get_email_tracker_data,
    get_upcoming_tasks,
    get_event,
    get_event_stats,
    get_events,
    get_latest_slack_summary,
    get_slack_summaries,
    get_task,
    get_tasks_for_deliverable,
    get_tasks_for_event,
    get_team_member,
    get_team_members,
    init_db,
    log_activity,
    log_notification,
    mark_task_complete,
    reject_step,
    save_slack_summary,
    set_config,
    update_checklist_item,
    update_deliverable,
    update_email_copy,
    update_event,
    update_planned_send_date,
    update_task,
    update_team_member,
)

ET = ZoneInfo("America/New_York")

app = Flask(__name__)
app.config.from_object(Config)

# Ensure the database directory exists (important for persistent disk on Render)
import os
os.makedirs(os.path.dirname(Config.DATABASE_PATH) or ".", exist_ok=True)

init_db(Config.DATABASE_PATH)


# --- Jinja Filters ---

@app.template_filter("event_type_label")
def event_type_label(value):
    """Human-readable label for event types."""
    labels = {
        "conference": "Conference",
        "tradeshow": "Tradeshow",
        "webinar": "Webinar",
        "workshop": "Workshop",
        "meetup": "Meetup",
        "lunch_and_learn": "Lunch & Learn",
        "other": "Other",
    }
    return labels.get(value, value.replace("_", " ").title() if value else "")


@app.template_filter("et_format")
def et_format(value, fmt="%b %d, %Y %I:%M %p"):
    """Format a datetime string for display (ET label added in templates)."""
    if not value:
        return ""
    try:
        if isinstance(value, str):
            dt = datetime.fromisoformat(value.replace("Z", "+00:00"))
        else:
            dt = value
        return dt.strftime(fmt)
    except (ValueError, TypeError):
        return str(value)


@app.template_filter("et_date")
def et_date(value):
    """Format a date string as 'Mar 15, 2026' (ET label added in templates)."""
    if not value:
        return ""
    try:
        d = date.fromisoformat(str(value)[:10])
        return d.strftime("%b %d, %Y")
    except (ValueError, TypeError):
        return str(value)


def now_et():
    """Get current datetime in Eastern Time."""
    return datetime.now(ET)


def today_et():
    """Get current date in Eastern Time."""
    return now_et().date()


# --- Helpers ---

def get_db():
    return get_connection(Config.DATABASE_PATH)


def get_slack_client():
    conn = get_db()
    token = get_config(conn, "slack_bot_token") or Config.SLACK_BOT_TOKEN
    conn.close()
    if not token:
        return None
    return SlackClient(token)


def get_mailchimp_client():
    conn = get_db()
    api_key = get_config(conn, "mailchimp_api_key") or Config.MAILCHIMP_API_KEY
    conn.close()
    if not api_key:
        return None
    return MailChimpClient(api_key)


def get_anthropic_client():
    conn = get_db()
    api_key = get_config(conn, "anthropic_api_key") or Config.ANTHROPIC_API_KEY
    conn.close()
    if not api_key:
        return None
    from clients.anthropic_client import AnthropicClient
    return AnthropicClient(api_key)


def get_outlook_client():
    conn = get_db()
    tenant_id = get_config(conn, "outlook_tenant_id") or Config.OUTLOOK_TENANT_ID
    client_id = get_config(conn, "outlook_client_id") or Config.OUTLOOK_CLIENT_ID
    client_secret = get_config(conn, "outlook_client_secret") or Config.OUTLOOK_CLIENT_SECRET
    organizer_email = get_config(conn, "outlook_organizer_email") or Config.OUTLOOK_ORGANIZER_EMAIL
    conn.close()
    if not all([tenant_id, client_id, client_secret, organizer_email]):
        return None
    return OutlookClient(tenant_id, client_id, client_secret, organizer_email)


def get_default_attendees():
    conn = get_db()
    raw = get_config(conn, "outlook_default_attendees") or ""
    conn.close()
    if not raw:
        return ["farzan.hussain@busplanner.com",
                "vanessa.broccoli@busplanner.com",
                "mahbod.haghighi@busplanner.com"]
    return [e.strip() for e in raw.split(",") if e.strip()]


def get_notification_channel(event=None):
    """Get the Slack channel for notifications. Prefers event-specific channel."""
    if event:
        try:
            ch = event["slack_channel_id"]
            if ch:
                return ch
        except (KeyError, IndexError):
            pass
    conn = get_db()
    channel = get_config(conn, "slack_notification_channel") or Config.SLACK_NOTIFICATION_CHANNEL
    conn.close()
    return channel


def mask_key(value):
    if not value or len(value) < 8:
        return value or ""
    return value[:4] + "***" + value[-4:]


def task_url(event_id, task_id):
    base = request.host_url.rstrip("/")
    return f"{base}/event/{event_id}/task/{task_id}"


# --- Timeline Suggestion (V2: Smart / Current-Date-Aware) ---

def suggest_timeline(event_date_str, deliverable_types, email_count=2):
    """Suggest deliverable due dates based on the event date.

    V2: Considers today's date. If a suggested date is in the past,
    clamps it to tomorrow. If the event is close, compresses the timeline.
    Email dates are shifted off weekends to the following Monday.
    """
    event_date = datetime.fromisoformat(event_date_str)
    tomorrow = datetime.combine(today_et() + timedelta(days=1), datetime.min.time())
    days_until_event = (event_date - datetime.combine(today_et(), datetime.min.time())).days
    timeline = []
    compressed = False

    def clamp(dt):
        """Ensure date is not in the past."""
        nonlocal compressed
        if dt < tomorrow:
            compressed = True
            return tomorrow
        return dt

    def skip_weekend(dt):
        """If dt falls on Saturday or Sunday, push to next Monday."""
        wd = dt.weekday()  # 5 = Saturday, 6 = Sunday
        if wd == 5:
            return dt + timedelta(days=2)
        elif wd == 6:
            return dt + timedelta(days=1)
        return dt

    if days_until_event < 14:
        # Very close — stagger everything from now
        compressed = True
        day_step = max(1, days_until_event // max(len(deliverable_types) + email_count, 1))
        offset = 1
        if "landing_page" in deliverable_types:
            timeline.append({
                "type": "landing_page", "label": "Landing Page",
                "due_date": (datetime.combine(today_et(), datetime.min.time()) + timedelta(days=offset)).isoformat(),
                "sort_order": 0,
            })
            offset += day_step
        if "calendar_entry" in deliverable_types:
            timeline.append({
                "type": "calendar_entry", "label": "Calendar Entry",
                "due_date": (datetime.combine(today_et(), datetime.min.time()) + timedelta(days=offset)).isoformat(),
                "sort_order": 1,
            })
            offset += day_step
        if "email" in deliverable_types:
            for i in range(email_count):
                label = f"Email {i + 1}" if email_count > 2 else ("First Email" if i == 0 else "Reminder Email") if email_count == 2 else "Event Email"
                raw_dt = datetime.combine(today_et(), datetime.min.time()) + timedelta(days=offset)
                email_dt = skip_weekend(raw_dt)
                timeline.append({
                    "type": "email", "label": label,
                    "due_date": email_dt.isoformat(),
                    "sort_order": 10 + i,
                })
                offset += day_step
    else:
        # Normal or slightly compressed timeline
        if "landing_page" in deliverable_types:
            dt = clamp(event_date - timedelta(weeks=6))
            timeline.append({
                "type": "landing_page", "label": "Landing Page",
                "due_date": dt.isoformat(), "sort_order": 0,
            })

        if "calendar_entry" in deliverable_types:
            dt = clamp(event_date - timedelta(weeks=4))
            timeline.append({
                "type": "calendar_entry", "label": "Calendar Entry",
                "due_date": dt.isoformat(), "sort_order": 1,
            })

        if "email" in deliverable_types:
            first = skip_weekend(clamp(event_date - timedelta(weeks=4)))
            last = skip_weekend(clamp(event_date - timedelta(weeks=1)))
            if email_count == 1:
                timeline.append({"type": "email", "label": "Event Email",
                                 "due_date": first.isoformat(), "sort_order": 10})
            elif email_count == 2:
                timeline.append({"type": "email", "label": "First Email",
                                 "due_date": first.isoformat(), "sort_order": 10})
                timeline.append({"type": "email", "label": "Reminder Email",
                                 "due_date": last.isoformat(), "sort_order": 11})
            else:
                span = (last - first).days
                for i in range(email_count):
                    offset = int(span * i / (email_count - 1)) if email_count > 1 else 0
                    d = skip_weekend(first + timedelta(days=offset))
                    timeline.append({"type": "email", "label": f"Email {i + 1}",
                                     "due_date": d.isoformat(), "sort_order": 10 + i})

    timeline.sort(key=lambda x: x["due_date"])
    return timeline, compressed


# --- Holiday Check Utility ---

def _check_holiday(check_date):
    """Check if a date falls on a US or Canadian statutory holiday.
    Returns the holiday name or None."""
    year = check_date.year
    holidays = {
        # Fixed US / Canadian holidays
        date(year, 1, 1): "New Year's Day",
        date(year, 7, 1): "Canada Day",
        date(year, 7, 4): "Independence Day (US)",
        date(year, 11, 11): "Remembrance Day / Veterans Day",
        date(year, 12, 25): "Christmas Day",
        date(year, 12, 26): "Boxing Day (Canada)",
    }

    # MLK Day: 3rd Monday of January
    jan1 = date(year, 1, 1)
    mlk = date(year, 1, 1) + timedelta(days=(7 - jan1.weekday()) % 7 + 14)
    holidays[mlk] = "Martin Luther King Jr. Day"

    # Presidents' Day / Family Day (Canada): 3rd Monday of February
    feb1 = date(year, 2, 1)
    presidents = feb1 + timedelta(days=(7 - feb1.weekday()) % 7 + 14)
    holidays[presidents] = "Presidents' Day / Family Day"

    # Victoria Day (Canada): last Monday on or before May 24
    may24 = date(year, 5, 24)
    victoria = may24 - timedelta(days=may24.weekday())
    holidays[victoria] = "Victoria Day (Canada)"

    # Memorial Day (US): last Monday of May
    may31 = date(year, 5, 31)
    memorial = may31 - timedelta(days=may31.weekday())
    holidays[memorial] = "Memorial Day"

    # Labour Day: 1st Monday of September
    sep1 = date(year, 9, 1)
    labour = sep1 + timedelta(days=(7 - sep1.weekday()) % 7)
    holidays[labour] = "Labour Day"

    # Canadian Thanksgiving: 2nd Monday of October
    oct1 = date(year, 10, 1)
    cdn_thanks = oct1 + timedelta(days=(7 - oct1.weekday()) % 7 + 7)
    holidays[cdn_thanks] = "Thanksgiving (Canada)"

    # US Thanksgiving: 4th Thursday of November
    nov1 = date(year, 11, 1)
    first_thu = nov1 + timedelta(days=(3 - nov1.weekday() + 7) % 7)
    us_thanks = first_thu + timedelta(weeks=3)
    holidays[us_thanks] = "Thanksgiving (US)"

    # Good Friday (Easter-based, Gregorian computus)
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    l_val = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l_val) // 451
    month = (h + l_val - 7 * m + 114) // 31
    day = ((h + l_val - 7 * m + 114) % 31) + 1
    easter = date(year, month, day)
    good_friday = easter - timedelta(days=2)
    holidays[good_friday] = "Good Friday"

    return holidays.get(check_date)


# ============================================================
# Page Routes
# ============================================================

@app.route("/")
def dashboard():
    conn = get_db()
    events = get_events(conn)
    event_stats = {}
    for event in events:
        event_stats[event["id"]] = get_event_stats(conn, event["id"])
    conn.close()
    return render_template("dashboard.html", events=events, event_stats=event_stats,
                           active_page="dashboard", today=today_et().isoformat())


@app.route("/email-tracker")
def email_tracker():
    show_completed = request.args.get("show_completed", "false") == "true"
    conn = get_db()
    tracker_data = get_email_tracker_data(conn, include_completed=show_completed)
    conn.close()
    return render_template("email_tracker.html",
                           tracker_data=tracker_data,
                           active_page="email_tracker",
                           show_completed=show_completed,
                           today=today_et().isoformat())


@app.route("/upcoming-tasks")
def upcoming_tasks():
    view = request.args.get("view", "week")  # week, next_week, month, all
    today = today_et()
    conn = get_db()

    if view == "week":
        # Current week (Mon-Sun)
        start = today - timedelta(days=today.weekday())
        end = start + timedelta(days=6)
        tasks = get_upcoming_tasks(conn, start_date=start.isoformat(), end_date=end.isoformat())
        view_label = f"This Week ({start.strftime('%b %d')} – {end.strftime('%b %d')})"
    elif view == "next_week":
        start = today - timedelta(days=today.weekday()) + timedelta(weeks=1)
        end = start + timedelta(days=6)
        tasks = get_upcoming_tasks(conn, start_date=start.isoformat(), end_date=end.isoformat())
        view_label = f"Next Week ({start.strftime('%b %d')} – {end.strftime('%b %d')})"
    elif view == "month":
        start = today.replace(day=1)
        if today.month == 12:
            end = today.replace(year=today.year + 1, month=1, day=1) - timedelta(days=1)
        else:
            end = today.replace(month=today.month + 1, day=1) - timedelta(days=1)
        tasks = get_upcoming_tasks(conn, start_date=start.isoformat(), end_date=end.isoformat())
        view_label = f"This Month ({today.strftime('%B %Y')})"
    elif view == "overdue":
        tasks = get_upcoming_tasks(conn, start_date="2000-01-01", end_date=(today - timedelta(days=1)).isoformat())
        # Filter to only incomplete tasks
        tasks = [t for t in tasks if t["status"] != "completed"]
        view_label = "Overdue Tasks"
    else:  # all
        tasks = get_upcoming_tasks(conn)
        # Filter to only incomplete tasks
        tasks = [t for t in tasks if t["status"] != "completed"]
        view_label = "All Upcoming Tasks"

    conn.close()
    return render_template("upcoming_tasks.html",
                           tasks=tasks, view=view, view_label=view_label,
                           active_page="upcoming_tasks",
                           today=today.isoformat())


@app.route("/event/new", methods=["GET", "POST"])
def event_new():
    if request.method == "POST":
        conn = get_db()
        event_type = request.form.get("event_type", "conference")

        event_id = create_event(conn, {
            "name": request.form["name"],
            "event_date": request.form["event_date"],
            "start_time": request.form.get("start_time") or None,
            "end_time": request.form.get("end_time") or None,
            "location": request.form.get("location"),
            "description": request.form.get("description"),
            "event_type": event_type,
            "slack_channel_id": request.form.get("slack_channel_id") or None,
        })

        # Create deliverables from timeline
        timeline_count = int(request.form.get("timeline_count", 0))
        for i in range(timeline_count):
            d_type = request.form.get(f"timeline_{i}_type")
            d_label = request.form.get(f"timeline_{i}_label")
            d_due = request.form.get(f"timeline_{i}_due_date")
            if d_type and d_label and d_due:
                del_id = create_deliverable(conn, {
                    "event_id": event_id,
                    "type": d_type,
                    "label": d_label,
                    "due_date": d_due,
                    "sort_order": i,
                })

                task_id = create_task(conn, {
                    "deliverable_id": del_id,
                    "event_id": event_id,
                    "title": d_label,
                    "due_date": d_due,
                })

                create_approval_steps(conn, task_id)

                if d_type == "email":
                    create_email_copy(conn, {
                        "task_id": task_id,
                        "deliverable_id": del_id,
                    })

        # V2: Auto-add post-event tasks for webinars
        if event_type == "webinar":
            event_date = datetime.fromisoformat(request.form["event_date"])
            post_event_date = (event_date + timedelta(days=2)).strftime("%Y-%m-%d")

            for post_task in [
                {"label": "Edit YouTube Video", "sort_order": 100},
                {"label": "Send Thank You Email (with video)", "sort_order": 101},
            ]:
                del_id = create_deliverable(conn, {
                    "event_id": event_id,
                    "type": "post_event",
                    "label": post_task["label"],
                    "due_date": post_event_date,
                    "sort_order": post_task["sort_order"],
                })
                task_id = create_task(conn, {
                    "deliverable_id": del_id,
                    "event_id": event_id,
                    "title": post_task["label"],
                    "due_date": post_event_date,
                })
                create_approval_steps(conn, task_id)

                # The thank-you email gets an email_copy record
                if "Email" in post_task["label"]:
                    create_email_copy(conn, {
                        "task_id": task_id,
                        "deliverable_id": del_id,
                    })

        # Create Outlook calendar invite (non-blocking)
        outlook = get_outlook_client()
        start_time = request.form.get("start_time")
        end_time = request.form.get("end_time")
        if outlook and start_time and end_time:
            try:
                event_date_str = request.form["event_date"]
                start_dt = f"{event_date_str}T{start_time}:00"
                end_dt = f"{event_date_str}T{end_time}:00"
                default_attendees = get_default_attendees()

                result = outlook.create_event(
                    subject=request.form["name"],
                    start_datetime=start_dt,
                    end_datetime=end_dt,
                    location=request.form.get("location", ""),
                    description=request.form.get("description", ""),
                    attendee_emails=default_attendees,
                )
                # Store the Outlook event ID for future updates
                update_event(conn, event_id, outlook_event_id=result["id"])
                app.logger.info("Outlook invite created for event %d", event_id)
            except Exception as e:
                app.logger.warning("Failed to create Outlook invite: %s", e)

        log_activity(conn, event_id=event_id, action="created",
                     details={"name": request.form["name"]})
        conn.close()
        flash("Event created successfully!", "success")
        return redirect(url_for("event_detail", event_id=event_id))

    return render_template("event_form.html", event=None, active_page="dashboard",
                           today=today_et().isoformat())


@app.route("/event/<int:event_id>")
def event_detail(event_id):
    conn = get_db()
    event = get_event(conn, event_id)
    if not event:
        conn.close()
        flash("Event not found.", "error")
        return redirect(url_for("dashboard"))

    deliverables = get_deliverables(conn, event_id)
    tasks_by_deliverable = {}
    task_approvals = {}
    for d in deliverables:
        tasks = get_tasks_for_deliverable(conn, d["id"])
        tasks_by_deliverable[d["id"]] = tasks
        for t in tasks:
            task_approvals[t["id"]] = get_approvals_for_task(conn, t["id"])

    team_members = get_team_members(conn)
    stats = get_event_stats(conn, event_id)
    activity = get_activity_log(conn, event_id=event_id)
    checklist_items = get_checklist_items(conn, event_id)
    latest_summary = get_latest_slack_summary(conn, event_id)
    slack_summaries = get_slack_summaries(conn, event_id)
    today = today_et().isoformat()
    conn.close()

    ai_available = get_anthropic_client() is not None

    return render_template("event_detail.html", event=event, deliverables=deliverables,
                           tasks_by_deliverable=tasks_by_deliverable, task_approvals=task_approvals,
                           team_members=team_members, stats=stats, activity=activity,
                           checklist_items=checklist_items, latest_summary=latest_summary,
                           slack_summaries=slack_summaries, ai_available=ai_available,
                           today=today, active_page="dashboard")


@app.route("/event/<int:event_id>/edit", methods=["GET", "POST"])
def event_edit(event_id):
    conn = get_db()
    event = get_event(conn, event_id)
    if not event:
        conn.close()
        flash("Event not found.", "error")
        return redirect(url_for("dashboard"))

    if request.method == "POST":
        update_event(conn, event_id,
                     name=request.form["name"],
                     event_date=request.form["event_date"],
                     start_time=request.form.get("start_time") or None,
                     end_time=request.form.get("end_time") or None,
                     location=request.form.get("location"),
                     description=request.form.get("description"),
                     event_type=request.form.get("event_type"),
                     slack_channel_id=request.form.get("slack_channel_id") or None)
        conn.close()
        flash("Event updated.", "success")
        return redirect(url_for("event_detail", event_id=event_id))

    conn.close()
    return render_template("event_form.html", event=event, active_page="dashboard",
                           today=today_et().isoformat())


@app.route("/event/<int:event_id>/task/<int:task_id>")
def task_detail(event_id, task_id):
    conn = get_db()
    task = get_task(conn, task_id)
    if not task:
        conn.close()
        flash("Task not found.", "error")
        return redirect(url_for("event_detail", event_id=event_id))

    approvals = get_approvals_for_task(conn, task_id)
    active_approval = get_active_approval(conn, task_id)
    team_members = get_team_members(conn)
    can_complete = True

    conn.close()
    return render_template("task_detail.html", task=task, approvals=approvals,
                           active_approval=active_approval, team_members=team_members,
                           can_complete=can_complete, active_page="dashboard")


@app.route("/event/<int:event_id>/task/<int:task_id>/email")
def email_review(event_id, task_id):
    conn = get_db()
    task = get_task(conn, task_id)
    if not task:
        conn.close()
        flash("Task not found.", "error")
        return redirect(url_for("event_detail", event_id=event_id))

    email_copy = get_email_copy_for_task(conn, task_id)
    active_approval = get_active_approval(conn, task_id)
    comments = get_email_comments(conn, task_id)
    team_members = get_team_members(conn, active_only=True)
    conn.close()

    return render_template("email_review.html", task=task, email_copy=email_copy,
                           active_approval=active_approval, comments=comments,
                           team_members=team_members, active_page="dashboard")


@app.route("/settings", methods=["GET", "POST"])
def settings():
    conn = get_db()

    if request.method == "POST":
        slack_token = request.form.get("slack_bot_token", "")
        slack_channel = request.form.get("slack_notification_channel", "")
        mc_key = request.form.get("mailchimp_api_key", "")
        anthropic_key = request.form.get("anthropic_api_key", "")

        if slack_token and "***" not in slack_token:
            set_config(conn, "slack_bot_token", slack_token)
        if slack_channel:
            set_config(conn, "slack_notification_channel", slack_channel)
        if mc_key and "***" not in mc_key:
            set_config(conn, "mailchimp_api_key", mc_key)
        if anthropic_key and "***" not in anthropic_key:
            set_config(conn, "anthropic_api_key", anthropic_key)

        # Outlook settings
        outlook_tenant = request.form.get("outlook_tenant_id", "")
        outlook_client = request.form.get("outlook_client_id", "")
        outlook_secret = request.form.get("outlook_client_secret", "")
        outlook_organizer = request.form.get("outlook_organizer_email", "")
        outlook_attendees = request.form.get("outlook_default_attendees", "")

        if outlook_tenant and "***" not in outlook_tenant:
            set_config(conn, "outlook_tenant_id", outlook_tenant)
        if outlook_client and "***" not in outlook_client:
            set_config(conn, "outlook_client_id", outlook_client)
        if outlook_secret and "***" not in outlook_secret:
            set_config(conn, "outlook_client_secret", outlook_secret)
        if outlook_organizer:
            set_config(conn, "outlook_organizer_email", outlook_organizer)
        if outlook_attendees:
            set_config(conn, "outlook_default_attendees", outlook_attendees)

        conn.close()
        flash("Settings saved.", "success")
        return redirect(url_for("settings"))

    team_members = get_team_members(conn, active_only=False)
    slack_token = get_config(conn, "slack_bot_token") or Config.SLACK_BOT_TOKEN
    slack_channel = get_config(conn, "slack_notification_channel") or Config.SLACK_NOTIFICATION_CHANNEL
    mc_key = get_config(conn, "mailchimp_api_key") or Config.MAILCHIMP_API_KEY
    anthropic_key = get_config(conn, "anthropic_api_key") or Config.ANTHROPIC_API_KEY
    outlook_tenant = get_config(conn, "outlook_tenant_id") or Config.OUTLOOK_TENANT_ID
    outlook_client_id = get_config(conn, "outlook_client_id") or Config.OUTLOOK_CLIENT_ID
    outlook_secret = get_config(conn, "outlook_client_secret") or Config.OUTLOOK_CLIENT_SECRET
    outlook_organizer = get_config(conn, "outlook_organizer_email") or Config.OUTLOOK_ORGANIZER_EMAIL
    outlook_attendees = get_config(conn, "outlook_default_attendees") or ""
    conn.close()

    return render_template("settings.html",
                           team_members=team_members,
                           slack_bot_token_masked=mask_key(slack_token),
                           slack_notification_channel=slack_channel,
                           mailchimp_api_key_masked=mask_key(mc_key),
                           anthropic_api_key_masked=mask_key(anthropic_key),
                           outlook_tenant_id_masked=mask_key(outlook_tenant),
                           outlook_client_id_masked=mask_key(outlook_client_id),
                           outlook_client_secret_masked=mask_key(outlook_secret),
                           outlook_organizer_email=outlook_organizer,
                           outlook_default_attendees=outlook_attendees,
                           active_page="settings")


# ============================================================
# API Routes
# ============================================================

@app.route("/api/event/<int:event_id>/delete", methods=["POST"])
def api_delete_event(event_id):
    conn = get_db()
    event = get_event(conn, event_id)
    if not event:
        conn.close()
        return jsonify({"error": "Event not found"}), 404
    hard_delete_event(conn, event_id)
    conn.close()
    return jsonify({"success": True})


@app.route("/api/team-member", methods=["POST"])
def api_add_team_member():
    data = request.get_json()
    conn = get_db()
    try:
        member_id = create_team_member(conn, data)
        conn.close()
        return jsonify({"success": True, "id": member_id})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/team-member/<int:member_id>/delete", methods=["POST"])
def api_remove_team_member(member_id):
    conn = get_db()
    try:
        deactivate_team_member(conn, member_id)
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/team-member/<int:member_id>/update", methods=["POST"])
def api_update_team_member(member_id):
    data = request.get_json()
    conn = get_db()
    member = get_team_member(conn, member_id)
    if not member:
        conn.close()
        return jsonify({"error": "Member not found"}), 404
    try:
        update_team_member(
            conn, member_id,
            name=data.get("name", member["name"]),
            email=data.get("email", member["email"]),
            slack_user_id=data.get("slack_user_id", member["slack_user_id"]),
            role=data.get("role", member["role"]),
        )
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/deliverable/<int:deliverable_id>/update-status", methods=["POST"])
def api_update_deliverable_status(deliverable_id):
    """Manually update a deliverable's status."""
    data = request.get_json()
    new_status = data.get("status")
    valid = ("pending", "in_progress", "in_review", "approved", "completed")
    if new_status not in valid:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(valid)}"}), 400

    conn = get_db()
    deliverable = get_deliverable(conn, deliverable_id)
    if not deliverable:
        conn.close()
        return jsonify({"error": "Deliverable not found"}), 404

    try:
        update_deliverable(conn, deliverable_id, status=new_status)

        # If marking deliverable as completed, also mark all its tasks as completed
        if new_status == "completed":
            tasks = get_tasks_for_deliverable(conn, deliverable_id)
            for task in tasks:
                if task["status"] != "completed":
                    update_task(conn, task["id"], status="completed",
                                completed_at=datetime.now().isoformat())

        log_activity(conn, event_id=deliverable["event_id"],
                     action="status_override",
                     details={"deliverable": deliverable["label"],
                              "new_status": new_status})
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/task/<int:task_id>/update-status", methods=["POST"])
def api_update_task_status(task_id):
    """Manually update a task's status."""
    data = request.get_json()
    new_status = data.get("status")
    valid = ("pending", "in_progress", "in_review", "completed")
    if new_status not in valid:
        return jsonify({"error": f"Invalid status. Must be one of: {', '.join(valid)}"}), 400

    conn = get_db()
    task = get_task(conn, task_id)
    if not task:
        conn.close()
        return jsonify({"error": "Task not found"}), 404

    try:
        if new_status == "completed":
            mark_task_complete(conn, task_id, completed_by_id=None)
        else:
            update_task(conn, task_id, status=new_status)

        # Sync parent deliverable status based on task states
        tasks = get_tasks_for_deliverable(conn, task["deliverable_id"])
        statuses = [t["status"] if t["id"] != task_id else new_status for t in tasks]
        if all(s == "completed" for s in statuses):
            update_deliverable(conn, task["deliverable_id"], status="completed")
        elif any(s in ("in_review", "completed") for s in statuses):
            update_deliverable(conn, task["deliverable_id"], status="in_review")
        elif any(s == "in_progress" for s in statuses):
            update_deliverable(conn, task["deliverable_id"], status="in_progress")

        log_activity(conn, event_id=task["event_id"], task_id=task_id,
                     action="status_override",
                     details={"task": task["title"], "new_status": new_status})
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/email-tracker/<int:task_id>/update-send-date", methods=["POST"])
def api_update_send_date(task_id):
    data = request.get_json()
    planned_send_date = data.get("planned_send_date")
    if not planned_send_date:
        return jsonify({"error": "planned_send_date is required"}), 400

    conn = get_db()
    task = get_task(conn, task_id)
    if not task:
        conn.close()
        return jsonify({"error": "Task not found"}), 404

    update_planned_send_date(conn, task_id, planned_send_date)

    # Build warning messages
    warnings = []
    try:
        send_date = date.fromisoformat(planned_send_date)
        # Check if Tuesday (1) or Thursday (3)
        if send_date.weekday() not in (1, 3):
            day_name = send_date.strftime("%A")
            warnings.append(f"{day_name} is not a typical send day (Tue/Thu)")

        # Check holidays
        holiday_name = _check_holiday(send_date)
        if holiday_name:
            warnings.append(f"This date falls on {holiday_name}")
    except ValueError:
        pass

    conn.close()
    return jsonify({"success": True, "warnings": warnings})


@app.route("/api/email-tracker/<int:task_id>/update-status", methods=["POST"])
def api_update_tracker_status(task_id):
    """Update a task's approval pipeline from the email tracker.
    Also syncs the parent deliverable's status."""
    data = request.get_json()
    approval_id = data.get("approval_id")
    action = data.get("action")  # "approve" or "reject"
    feedback = data.get("feedback", "")

    conn = get_db()
    task = get_task(conn, task_id)
    if not task:
        conn.close()
        return jsonify({"error": "Task not found"}), 404

    if action == "approve":
        next_step = approve_step(conn, approval_id)
        # If all steps done, update deliverable too
        if not next_step:
            deliverable = get_deliverable(conn, task["deliverable_id"])
            if deliverable:
                update_deliverable(conn, task["deliverable_id"], status="completed")
        else:
            # Update deliverable to in_review when approvals are in progress
            deliverable = get_deliverable(conn, task["deliverable_id"])
            if deliverable and deliverable["status"] not in ("approved", "completed"):
                update_deliverable(conn, task["deliverable_id"], status="in_review")
    elif action == "reject":
        reject_step(conn, approval_id, feedback)
        deliverable = get_deliverable(conn, task["deliverable_id"])
        if deliverable:
            update_deliverable(conn, task["deliverable_id"], status="in_progress")
    else:
        conn.close()
        return jsonify({"error": "Invalid action"}), 400

    log_activity(conn, event_id=task["event_id"], task_id=task_id,
                 action=f"tracker_{action}")
    conn.close()
    return jsonify({"success": True})


@app.route("/api/task/<int:task_id>/comment", methods=["POST"])
def api_add_comment(task_id):
    """Post a feedback comment on an email task and notify via Slack."""
    data = request.get_json()
    body = (data.get("body") or "").strip()
    author_id = data.get("author_id")

    if not body:
        return jsonify({"error": "Comment body is required."}), 400
    if not author_id:
        return jsonify({"error": "Author is required."}), 400

    conn = get_db()
    task = get_task(conn, task_id)
    if not task:
        conn.close()
        return jsonify({"error": "Task not found"}), 404

    author = get_team_member(conn, author_id)
    if not author:
        conn.close()
        return jsonify({"error": "Team member not found"}), 404

    comment_id = create_email_comment(conn, task_id, author_id, body)

    log_activity(conn, event_id=task["event_id"], task_id=task_id,
                 action="email_comment",
                 details={"author": author["name"], "comment": body[:200]})

    # Send Slack notification
    slack = get_slack_client()
    event = get_event(conn, task["event_id"])
    channel = get_notification_channel(event)
    slack_sent = False
    slack_error = None
    if slack and channel:
        try:
            task_url = request.host_url.rstrip("/") + url_for(
                "email_review", event_id=task["event_id"], task_id=task_id)
            event_name = event["name"] if event else "Unknown Event"
            slack.post_email_comment(
                channel, task["title"], event_name,
                author["name"], body, task_url)
            slack_sent = True
        except Exception as e:
            slack_error = str(e)
            app.logger.warning("Slack comment notification failed: %s", e)
    elif not slack:
        slack_error = "Slack not configured — add bot token in Settings"
    elif not channel:
        slack_error = "No Slack channel configured — set in Settings or on the event"

    conn.close()
    return jsonify({
        "success": True,
        "slack_sent": slack_sent,
        "slack_error": slack_error,
        "comment": {
            "id": comment_id,
            "author_name": author["name"],
            "author_role": author["role"],
            "body": body,
            "created_at": "just now",
        },
    })


@app.route("/api/test-slack", methods=["POST"])
def api_test_slack():
    client = get_slack_client()
    if not client:
        return jsonify({"success": False, "error": "No token configured"})
    return jsonify({"success": client.verify_connection()})


@app.route("/api/test-mailchimp", methods=["POST"])
def api_test_mailchimp():
    client = get_mailchimp_client()
    if not client:
        return jsonify({"success": False, "error": "No API key configured"})
    return jsonify({"success": client.verify_connection()})


@app.route("/api/test-anthropic", methods=["POST"])
def api_test_anthropic():
    client = get_anthropic_client()
    if not client:
        return jsonify({"success": False, "error": "No API key configured"})
    return jsonify({"success": client.verify_connection()})


@app.route("/api/test-outlook", methods=["POST"])
def api_test_outlook():
    client = get_outlook_client()
    if not client:
        return jsonify({"success": False, "error": "Outlook not configured"})
    return jsonify({"success": client.verify_connection()})


@app.route("/api/task/<int:task_id>/assign", methods=["POST"])
def api_assign_task(task_id):
    data = request.get_json()
    assignee_id = data.get("assignee_id")
    conn = get_db()

    update_task(conn, task_id, assignee_id=assignee_id, status="in_progress")
    task = get_task(conn, task_id)

    # Update step 1 approver to the assignee
    approvals = get_approvals_for_task(conn, task_id)
    if approvals:
        step1 = approvals[0]
        conn.execute(
            "UPDATE approvals SET approver_id = ? WHERE id = ?",
            (assignee_id, step1["id"]),
        )
        conn.commit()

    log_activity(conn, event_id=task["event_id"], task_id=task_id,
                 action="assigned", details={"assignee": task["assignee_name"]})

    # Send Slack notification to event channel or default channel
    slack = get_slack_client()
    event = get_event(conn, task["event_id"])
    channel = get_notification_channel(event)
    if slack and channel and task:
        try:
            slack.post_task_assigned(
                channel, task["title"], task["event_name"],
                task["assignee_slack_id"], task["assignee_name"],
                task["due_date"][:10],
                task_url(task["event_id"], task_id),
            )
            log_notification(conn, task_id, "assigned")
        except Exception as e:
            app.logger.warning("Slack assign notification failed: %s", e)

    # Add assignee to Outlook calendar invite (non-blocking)
    if event and event["outlook_event_id"]:
        member = get_team_member(conn, assignee_id)
        if member and member["email"]:
            outlook = get_outlook_client()
            if outlook:
                try:
                    outlook.add_attendee(event["outlook_event_id"], member["email"])
                    app.logger.info("Added %s to Outlook invite for event %d",
                                    member["email"], event["id"])
                except Exception as e:
                    app.logger.warning("Failed to add %s to Outlook invite: %s",
                                       member["email"], e)

    conn.close()
    return jsonify({"success": True})


@app.route("/api/task/<int:task_id>/complete", methods=["POST"])
def api_complete_task(task_id):
    conn = get_db()
    mark_task_complete(conn, task_id, completed_by_id=None)
    task = get_task(conn, task_id)
    if task:
        log_activity(conn, event_id=task["event_id"], task_id=task_id, action="completed")
    conn.close()
    return jsonify({"success": True})


@app.route("/api/approval/<int:approval_id>/approve", methods=["POST"])
def api_approve(approval_id):
    conn = get_db()
    approval = get_approval(conn, approval_id)
    if not approval or approval["status"] != "active":
        conn.close()
        return jsonify({"error": "This step is not active."}), 400

    task = get_task(conn, approval["task_id"])
    next_step = approve_step(conn, approval_id)

    log_activity(conn, event_id=task["event_id"] if task else None,
                 task_id=approval["task_id"], action="approved",
                 details={"step": approval["step_label"]})

    # Slack notifications — use event channel
    slack = get_slack_client()
    event = get_event(conn, task["event_id"]) if task else None
    channel = get_notification_channel(event)
    if slack and channel and task:
        try:
            slack.post_approval_result(
                channel, task["title"], task["event_name"],
                approval["step_label"], "approved",
                next_step_label=next_step["step_label"] if next_step else None,
            )
            if next_step:
                slack.post_approval_needed(
                    channel, task["title"], task["event_name"],
                    next_step["step_label"], next_step["step_order"], 5,
                    next_step["approver_slack_id"],
                    task_url(task["event_id"], task["id"]),
                )
        except Exception as e:
            app.logger.warning("Slack approval notification failed: %s", e)

    conn.close()
    return jsonify({"success": True, "next_step": dict(next_step) if next_step else None})


@app.route("/api/approval/<int:approval_id>/unapprove", methods=["POST"])
def api_unapprove(approval_id):
    conn = get_db()
    approval = get_approval(conn, approval_id)
    if not approval or approval["status"] != "approved":
        conn.close()
        return jsonify({"error": "This step is not approved."}), 400

    from database.db import unapprove_step
    result = unapprove_step(conn, approval_id)

    task = get_task(conn, approval["task_id"])
    log_activity(conn, event_id=task["event_id"] if task else None,
                 task_id=approval["task_id"], action="unapproved",
                 details={"step": approval["step_label"]})
    conn.close()
    return jsonify({"success": True})


@app.route("/api/approval/<int:approval_id>/reject", methods=["POST"])
def api_reject(approval_id):
    data = request.get_json()
    feedback = data.get("feedback", "")
    conn = get_db()

    approval = get_approval(conn, approval_id)
    if not approval or approval["status"] != "active":
        conn.close()
        return jsonify({"error": "This step is not active."}), 400

    task = get_task(conn, approval["task_id"])
    prev_step = reject_step(conn, approval_id, feedback)

    log_activity(conn, event_id=task["event_id"] if task else None,
                 task_id=approval["task_id"], action="rejected",
                 details={"step": approval["step_label"], "feedback": feedback})

    # Slack notification — use event channel
    slack = get_slack_client()
    event = get_event(conn, task["event_id"]) if task else None
    channel = get_notification_channel(event)
    if slack and channel and task:
        try:
            slack.post_approval_result(
                channel, task["title"], task["event_name"],
                approval["step_label"], "rejected",
                next_step_label=prev_step["step_label"] if prev_step else None,
                feedback=feedback,
            )
        except Exception as e:
            app.logger.warning("Slack rejection notification failed: %s", e)

    conn.close()
    return jsonify({"success": True})


# --- Email Copy APIs ---

@app.route("/api/email-copy/create/<int:task_id>", methods=["POST"])
def api_create_email_copy(task_id):
    data = request.get_json()
    conn = get_db()
    task = get_task(conn, task_id)
    if not task:
        conn.close()
        return jsonify({"error": "Task not found"}), 404

    existing = get_email_copy_for_task(conn, task_id)
    if existing:
        updates = {}
        if "subject_line" in data:
            updates["subject_line"] = data["subject_line"]
        if "html_content" in data:
            updates["html_content"] = data["html_content"]
        if data.get("pull") and data.get("campaign_id"):
            mc = get_mailchimp_client()
            if mc:
                try:
                    content = mc.get_campaign_content(data["campaign_id"])
                    updates["html_content"] = content.get("html", "")
                    updates["mailchimp_campaign_id"] = data["campaign_id"]
                    updates["last_pulled_at"] = now_et().isoformat()
                    update_email_copy(conn, existing["id"], **updates)
                    conn.close()
                    return jsonify({"success": True, "html": content.get("html", "")})
                except Exception as e:
                    conn.close()
                    return jsonify({"error": str(e)}), 400
        if updates:
            update_email_copy(conn, existing["id"], **updates)
        conn.close()
        return jsonify({"success": True})

    copy_id = create_email_copy(conn, {
        "task_id": task_id,
        "deliverable_id": task["deliverable_id"],
        "subject_line": data.get("subject_line"),
        "html_content": data.get("html_content"),
    })
    conn.close()
    return jsonify({"success": True, "id": copy_id})


@app.route("/api/email-copy/<int:copy_id>", methods=["POST"])
def api_update_email_copy(copy_id):
    data = request.get_json()
    conn = get_db()
    updates = {}
    if "subject_line" in data:
        updates["subject_line"] = data["subject_line"]
    if "html_content" in data:
        updates["html_content"] = data["html_content"]
    if updates:
        update_email_copy(conn, copy_id, **updates)
    conn.close()
    return jsonify({"success": True})


@app.route("/api/email-copy/<int:copy_id>/pull-mailchimp", methods=["POST"])
def api_pull_mailchimp(copy_id):
    data = request.get_json()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return jsonify({"error": "Campaign ID required"}), 400

    mc = get_mailchimp_client()
    if not mc:
        return jsonify({"error": "MailChimp not configured"}), 400

    try:
        content = mc.get_campaign_content(campaign_id)
        campaign = mc.get_campaign(campaign_id)
        settings = campaign.get("settings", {})
        subject_line = settings.get("subject_line", "")

        conn = get_db()
        update_email_copy(conn, copy_id,
                          html_content=content.get("html", ""),
                          subject_line=subject_line,
                          mailchimp_campaign_id=campaign_id,
                          last_pulled_at=now_et().isoformat())
        conn.close()
        return jsonify({
            "success": True,
            "html": content.get("html", ""),
            "subject_line": subject_line,
        })
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/email-copy/<int:copy_id>/push-mailchimp", methods=["POST"])
def api_push_mailchimp(copy_id):
    data = request.get_json()
    campaign_id = data.get("campaign_id")
    if not campaign_id:
        return jsonify({"error": "Campaign ID required"}), 400

    mc = get_mailchimp_client()
    if not mc:
        return jsonify({"error": "MailChimp not configured"}), 400

    conn = get_db()
    email_copy = get_email_copy(conn, copy_id)
    if not email_copy:
        conn.close()
        return jsonify({"error": "Email copy not found"}), 404

    try:
        # Push HTML content
        plain_text = None
        try:
            plain_text = email_copy["plain_text"]
        except (KeyError, IndexError):
            pass
        mc.update_campaign_content(campaign_id, email_copy["html_content"],
                                   plain_text)

        # Push subject line if provided in request or saved in email copy
        subject_line = data.get("subject_line") or email_copy["subject_line"]
        if subject_line:
            mc.update_campaign_settings(campaign_id, subject_line=subject_line)

        update_email_copy(conn, copy_id,
                          mailchimp_campaign_id=campaign_id,
                          last_pushed_at=now_et().isoformat())
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/mailchimp/campaigns")
def api_mailchimp_campaigns():
    mc = get_mailchimp_client()
    if not mc:
        return jsonify({"error": "MailChimp not configured"}), 400
    try:
        campaigns = mc.get_campaigns()
        return jsonify({"campaigns": campaigns})
    except Exception as e:
        return jsonify({"error": str(e)}), 400


@app.route("/api/event/<int:event_id>/suggest-timeline", methods=["POST"])
def api_suggest_timeline(event_id):
    data = request.get_json()
    timeline, compressed = suggest_timeline(
        data["event_date"],
        data.get("deliverable_types", []),
        data.get("email_count", 2),
    )
    return jsonify({"timeline": timeline, "compressed": compressed})


# --- V2: Checklist APIs ---

@app.route("/api/event/<int:event_id>/checklist", methods=["POST"])
def api_add_checklist_item(event_id):
    data = request.get_json()
    data["event_id"] = event_id
    conn = get_db()
    try:
        item_id = create_checklist_item(conn, data)
        log_activity(conn, event_id=event_id, action="checklist_added",
                     details={"item": data["name"]})
        conn.close()
        return jsonify({"success": True, "id": item_id})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


@app.route("/api/checklist/<int:item_id>/update", methods=["POST"])
def api_update_checklist_item(item_id):
    data = request.get_json()
    conn = get_db()
    update_checklist_item(conn, item_id, **data)
    conn.close()
    return jsonify({"success": True})


@app.route("/api/checklist/<int:item_id>/delete", methods=["POST"])
def api_delete_checklist_item(item_id):
    conn = get_db()
    delete_checklist_item(conn, item_id)
    conn.close()
    return jsonify({"success": True})


# --- V2: Slack Summary API ---

@app.route("/api/event/<int:event_id>/slack-summary", methods=["POST"])
def api_slack_summary(event_id):
    conn = get_db()
    event = get_event(conn, event_id)
    if not event:
        conn.close()
        return jsonify({"error": "Event not found"}), 404

    if not event["slack_channel_id"]:
        conn.close()
        return jsonify({"error": "No Slack channel configured for this event"}), 400

    slack = get_slack_client()
    if not slack:
        conn.close()
        return jsonify({"error": "Slack not configured"}), 400

    skip_ai = request.args.get("skip_ai") == "1"
    ai_client = None if skip_ai else get_anthropic_client()

    try:
        # Get latest summary's newest timestamp to only fetch new messages
        latest = get_latest_slack_summary(conn, event_id)
        oldest = latest["latest_ts"] if latest else None

        messages = slack.get_channel_history(event["slack_channel_id"], oldest=oldest)
        if not messages:
            conn.close()
            return jsonify({"error": "No new messages found in the channel"}), 400

        # Filter out bot and system messages — keep only human posts
        human_messages = [
            msg for msg in messages
            if not msg.get("bot_id") and msg.get("subtype") is None
        ]

        if not human_messages:
            conn.close()
            return jsonify({"error": "No human messages found in the channel (only bot/system messages)"}), 400

        if ai_client:
            # AI-powered summary with structured insights — only human messages
            result = ai_client.summarize_slack_messages(human_messages, event["name"])
            save_slack_summary(conn, {
                "event_id": event_id,
                "summary": result["summary"],
                "decisions": result.get("decisions", []),
                "action_items": result.get("action_items", []),
                "deadlines": result.get("deadlines", []),
                "message_count": len(human_messages),
                "oldest_ts": messages[-1].get("ts") if messages else None,
                "latest_ts": messages[0].get("ts") if messages else None,
                "ai_powered": True,
            })
        else:
            # No AI — save human messages only as cleaned-up plain text
            import html as html_mod
            import re

            def clean_slack_text(text, slack_inst):
                """Clean Slack markup into readable text."""
                # Resolve user mentions <@U123> or <@U123|name>
                def replace_user(m):
                    uid = m.group(1)
                    return f"@{slack_inst.get_user_name(uid)}"
                text = re.sub(r"<@(\w+)(?:\|[^>]*)?>", replace_user, text)
                # Clean links: <http://url|label> → label, <http://url> → url
                text = re.sub(r"<(https?://[^|>]+)\|([^>]+)>", r"\2", text)
                text = re.sub(r"<(https?://[^>]+)>", r"\1", text)
                # Channel mentions <#C123|channel-name> → #channel-name
                text = re.sub(r"<#\w+\|([^>]+)>", r"#\1", text)
                # Special mentions <!channel>, <!here>, <!everyone>
                text = re.sub(r"<!channel>", "@channel", text)
                text = re.sub(r"<!here>", "@here", text)
                text = re.sub(r"<!everyone>", "@everyone", text)
                # Decode HTML entities (&amp; &lt; &gt;)
                text = html_mod.unescape(text)
                return text.strip()

            # human_messages already filtered above (before AI check)
            lines = []
            for msg in human_messages:
                user_id = msg.get("user", "unknown")
                user_name = slack.get_user_name(user_id) if user_id != "unknown" else "unknown"
                text = clean_slack_text(msg.get("text", ""), slack)
                if not text:
                    continue
                # Format timestamp
                ts = msg.get("ts", "")
                try:
                    from datetime import datetime as dt_cls
                    msg_time = dt_cls.fromtimestamp(float(ts)).strftime("%b %d, %I:%M %p")
                except (ValueError, OSError):
                    msg_time = ""
                if msg_time:
                    lines.append(f"[{msg_time}] {user_name}: {text}")
                else:
                    lines.append(f"{user_name}: {text}")
            raw_summary = "\n".join(lines) if lines else "No human messages found in the channel."

            save_slack_summary(conn, {
                "event_id": event_id,
                "summary": raw_summary,
                "decisions": [],
                "action_items": [],
                "deadlines": [],
                "message_count": len(human_messages),
                "oldest_ts": messages[-1].get("ts") if messages else None,
                "latest_ts": messages[0].get("ts") if messages else None,
                "ai_powered": False,
            })

        log_activity(conn, event_id=event_id, action="slack_summary",
                     details={"message_count": len(human_messages)})
        conn.close()
        return jsonify({"success": True})
    except Exception as e:
        conn.close()
        return jsonify({"error": str(e)}), 400


# ============================================================
# App Entry Point — use `python run.py` to start the server
# ============================================================

if __name__ == "__main__":
    print("Please use 'python run.py' to start the server.", flush=True)
    print("Running directly via 'python app.py' is not supported.", flush=True)

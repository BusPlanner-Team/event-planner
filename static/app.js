// Shared JS utilities for Event Planner V2

// Auto-dismiss flash messages after 5 seconds
document.addEventListener('DOMContentLoaded', () => {
    const flashes = document.querySelectorAll('.flash');
    flashes.forEach(flash => {
        setTimeout(() => {
            flash.style.opacity = '0';
            flash.style.transition = 'opacity 0.3s';
            setTimeout(() => flash.remove(), 300);
        }, 5000);
    });

    // Initialize flatpickr on all date inputs
    initFlatpickr();

    // Initialize tabs if present
    initTabs();
});


// --- Flatpickr Initialization ---

function initFlatpickr() {
    // Standard date inputs (not in timeline preview, which is dynamic)
    document.querySelectorAll('input[type="date"], input.flatpickr-date').forEach(el => {
        if (!el._flatpickr) {
            flatpickr(el, {
                dateFormat: 'Y-m-d',
                allowInput: true,
            });
        }
    });

    // Datetime inputs
    document.querySelectorAll('input[type="datetime-local"], input.flatpickr-datetime').forEach(el => {
        if (!el._flatpickr) {
            flatpickr(el, {
                dateFormat: 'Y-m-d',
                allowInput: true,
            });
        }
    });

    // Time-only inputs (for start/end time)
    document.querySelectorAll('input.flatpickr-time').forEach(el => {
        if (!el._flatpickr) {
            flatpickr(el, {
                enableTime: true,
                noCalendar: true,
                dateFormat: 'H:i',
                time_24hr: true,
                allowInput: true,
            });
        }
    });
}

// Re-initialize flatpickr for dynamically added elements (e.g., timeline preview)
function reinitFlatpickr(container) {
    if (!container) return;
    container.querySelectorAll('input[type="date"], input[type="datetime-local"], input.flatpickr-date').forEach(el => {
        if (!el._flatpickr) {
            flatpickr(el, {
                dateFormat: 'Y-m-d',
                allowInput: true,
            });
        }
    });
    container.querySelectorAll('input.flatpickr-time').forEach(el => {
        if (!el._flatpickr) {
            flatpickr(el, {
                enableTime: true,
                noCalendar: true,
                dateFormat: 'H:i',
                time_24hr: true,
                allowInput: true,
            });
        }
    });
}


// --- Tab Navigation ---

function initTabs() {
    const tabs = document.querySelectorAll('.tab-btn');
    tabs.forEach(tab => {
        tab.addEventListener('click', () => {
            const target = tab.dataset.tab;
            // Deactivate all tabs and panels
            document.querySelectorAll('.tab-btn').forEach(t => t.classList.remove('active'));
            document.querySelectorAll('.tab-panel').forEach(p => p.classList.remove('active'));
            // Activate clicked tab and panel
            tab.classList.add('active');
            const panel = document.getElementById(`tab-${target}`);
            if (panel) panel.classList.add('active');
        });
    });
}


// --- Checklist AJAX ---

async function addChecklistItem(e, eventId) {
    e.preventDefault();
    const form = e.target;
    const data = Object.fromEntries(new FormData(form));
    if (data.assignee_id) data.assignee_id = parseInt(data.assignee_id);
    const resp = await fetch(`/api/event/${eventId}/checklist`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
    });
    if (resp.ok) {
        location.reload();
    } else {
        const err = await resp.json();
        alert(err.error || 'Failed to add item');
    }
}

async function updateChecklistItem(itemId, field, value) {
    const data = {};
    data[field] = value;
    const resp = await fetch(`/api/checklist/${itemId}/update`, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify(data),
    });
    if (resp.ok) {
        location.reload();
    }
}

async function deleteChecklistItem(itemId) {
    if (!confirm('Remove this item?')) return;
    const resp = await fetch(`/api/checklist/${itemId}/delete`, {method: 'POST'});
    if (resp.ok) location.reload();
}


// --- Delete Event ---

async function deleteEvent(eventId, eventName) {
    if (!confirm(`Delete "${eventName}" and all its tasks? This cannot be undone.`)) return;
    const resp = await fetch(`/api/event/${eventId}/delete`, {method: 'POST'});
    if (resp.ok) {
        window.location.href = '/';
    } else {
        const err = await resp.json();
        alert(err.error || 'Failed to delete event');
    }
}


// --- Slack Summary AJAX ---

async function refreshSlackSummary(eventId, useAI) {
    const statusEl = document.getElementById('slack-summary-status');
    const pullBtn = document.getElementById('pull-messages-btn');
    const aiBtn = document.getElementById('refresh-summary-btn');
    const statusMsg = useAI ? 'Analyzing messages...' : 'Pulling messages...';
    if (statusEl) statusEl.innerHTML = `<span class="spinner"></span> ${statusMsg}`;
    if (pullBtn) pullBtn.disabled = true;
    if (aiBtn) aiBtn.disabled = true;

    const url = `/api/event/${eventId}/slack-summary` + (useAI ? '' : '?skip_ai=1');
    const resp = await fetch(url, {method: 'POST'});
    if (resp.ok) {
        location.reload();
    } else {
        const err = await resp.json();
        if (statusEl) statusEl.innerHTML = `<span class="test-status test-error">${err.error || 'Failed'}</span>`;
        if (pullBtn) pullBtn.disabled = false;
        if (aiBtn) aiBtn.disabled = false;
    }
}


// --- Anthropic Test Connection ---

async function testAnthropic() {
    const statusEl = document.getElementById('anthropic-status');
    statusEl.innerHTML = '<span class="spinner"></span>';
    const resp = await fetch('/api/test-anthropic', {method: 'POST'});
    const data = await resp.json();
    statusEl.innerHTML = data.success
        ? '<span class="test-status test-success">Connected</span>'
        : '<span class="test-status test-error">Failed</span>';
}

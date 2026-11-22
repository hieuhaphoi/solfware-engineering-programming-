/**
 * Attendly - Lecturer PIN Countdown, Live Roster Polling & Manual Status Updater
 * Paired with lecturer/class_attendance.html & attendance_controller.py
 */
document.addEventListener("DOMContentLoaded", () => {
    const root = document.getElementById("pinSessionRoot");
    const countdownBadge = document.getElementById("pinCountdownBadge");
    const countdownText = document.getElementById("pinCountdownText");
    const statusBadge = document.getElementById("sessionStatusBadge");
    const pinDisplay = document.getElementById("activePinCodeDisplay");
    const projectorPin = document.getElementById("projectorPinCode");
    const copyPinBtn = document.getElementById("copyPinBtn");

    const countPresent = document.getElementById("countPresent");
    const countLate = document.getElementById("countLate");
    const countExcused = document.getElementById("countExcused");
    const countAbsent = document.getElementById("countAbsent");
    const checkedInRatio = document.getElementById("checkedInRatio");

    const searchInput = document.getElementById("rosterSearchInput");
    const statusFilter = document.getElementById("rosterStatusFilter");
    const showingText = document.getElementById("rosterShowingText");

    let remainingSeconds = root ? parseInt(root.dataset.remainingSeconds || "0", 10) : 0;
    let isOpen = root ? root.dataset.isOpen === "true" : false;
    const sessionId = root ? root.dataset.sessionId : "";

    function formatHHMMSS(totalSec) {
        const sec = Math.max(0, totalSec);
        const hrs = String(Math.floor(sec / 3600)).padStart(2, "0");
        const mins = String(Math.floor((sec % 3600) / 60)).padStart(2, "0");
        const secs = String(sec % 60).padStart(2, "0");
        return `${hrs}:${mins}:${secs}`;
    }

    function formatTimeShort(isoStr) {
        if (!isoStr) return "—";
        if (isoStr.length >= 16) return isoStr.slice(11, 16);
        return isoStr;
    }

    function applyStatusClass(selectEl, status) {
        const normalized = (status || "ABSENT").toLowerCase();
        selectEl.classList.remove("status-present", "status-late", "status-excused", "status-absent");
        selectEl.classList.add(`status-${normalized}`);
    }

    function updateCountdownUI() {
        if (!countdownText || !countdownBadge) return;
        if (!isOpen || remainingSeconds <= 0) {
            countdownText.textContent = "00:00:00 expired";
            countdownBadge.classList.remove("expiring");
            countdownBadge.classList.add("expired");
            if (statusBadge) {
                statusBadge.className = "badge badge-warning";
                statusBadge.textContent = "Session closed";
            }
            return;
        }

        countdownText.textContent = `${formatHHMMSS(remainingSeconds)} remaining`;
        countdownBadge.classList.remove("expired");
        if (remainingSeconds <= 60) {
            countdownBadge.classList.add("expiring");
        } else {
            countdownBadge.classList.remove("expiring");
        }
    }

    // 1-second countdown interval
    if (root && sessionId) {
        updateCountdownUI();
        setInterval(() => {
            if (isOpen && remainingSeconds > 0) {
                remainingSeconds -= 1;
                updateCountdownUI();
                if (remainingSeconds === 0) {
                    isOpen = false;
                    pollSessionRecords();
                }
            }
        }, 1000);
    }

    // Copy PIN button
    if (copyPinBtn) {
        copyPinBtn.addEventListener("click", async () => {
            const pin = (pinDisplay ? pinDisplay.textContent : copyPinBtn.dataset.pin || "").trim();
            if (!pin || pin.includes("-")) return;
            try {
                await navigator.clipboard.writeText(pin);
                const original = copyPinBtn.textContent;
                copyPinBtn.textContent = "Copied!";
                setTimeout(() => {
                    copyPinBtn.textContent = original;
                }, 1500);
            } catch {
                // Fallback
            }
        });
    }

    // AJAX Regenerate PIN
    const regenerateForm = document.getElementById("regeneratePinForm");
    if (regenerateForm) {
        regenerateForm.addEventListener("submit", async (e) => {
            e.preventDefault();
            try {
                const resp = await fetch(regenerateForm.action, {
                    method: "POST",
                    headers: {
                        "Accept": "application/json",
                        "Content-Type": "application/json",
                    },
                    body: JSON.stringify({}),
                });
                const data = await resp.json();
                if (data.success && data.pin) {
                    const newPin = data.pin.pin_code;
                    if (pinDisplay) pinDisplay.textContent = newPin;
                    if (projectorPin) projectorPin.textContent = newPin;
                    if (copyPinBtn) copyPinBtn.dataset.pin = newPin;
                    remainingSeconds = parseInt(data.pin.remaining_seconds || data.pin.ttl_seconds || 300, 10);
                    isOpen = true;
                    updateCountdownUI();
                } else {
                    regenerateForm.submit();
                }
            } catch {
                regenerateForm.submit();
            }
        });
    }

    // Projector Modal Toggle
    const projectorModal = document.getElementById("projectorModal");
    const openProjectorBtn = document.getElementById("openProjectorBtn");
    const closeProjectorBtn = document.getElementById("closeProjectorBtn");

    if (openProjectorBtn && projectorModal) {
        openProjectorBtn.addEventListener("click", () => {
            projectorModal.classList.add("open");
        });
    }
    if (closeProjectorBtn && projectorModal) {
        closeProjectorBtn.addEventListener("click", () => {
            projectorModal.classList.remove("open");
        });
    }
    if (projectorModal) {
        projectorModal.addEventListener("click", (e) => {
            if (e.target === projectorModal) projectorModal.classList.remove("open");
        });
    }

    // Recalculate summary counts from DOM rows
    function recalculateSummaryFromDOM() {
        const rows = document.querySelectorAll(".roster-row");
        const counts = { PRESENT: 0, LATE: 0, EXCUSED: 0, ABSENT: 0 };
        rows.forEach((row) => {
            const st = (row.dataset.status || "ABSENT").toUpperCase();
            if (counts[st] !== undefined) counts[st] += 1;
        });
        const total = rows.length;
        const checkedIn = counts.PRESENT + counts.LATE;

        if (countPresent) countPresent.textContent = counts.PRESENT;
        if (countLate) countLate.textContent = counts.LATE;
        if (countExcused) countExcused.textContent = counts.EXCUSED;
        if (countAbsent) countAbsent.textContent = counts.ABSENT;
        if (checkedInRatio) checkedInRatio.textContent = `${checkedIn} / ${total}`;
    }

    // Manual status dropdown change via AJAX
    function bindStatusDropdowns() {
        document.querySelectorAll(".status-select").forEach((selectEl) => {
            if (selectEl.dataset.bound === "true") return;
            selectEl.dataset.bound = "true";

            selectEl.addEventListener("change", async () => {
                const newStatus = selectEl.value.toUpperCase();
                const form = selectEl.closest("form");
                const row = selectEl.closest(".roster-row");

                applyStatusClass(selectEl, newStatus);
                if (row) row.dataset.status = newStatus;
                recalculateSummaryFromDOM();

                if (!form) return;
                try {
                    const resp = await fetch(form.action, {
                        method: "POST",
                        headers: {
                            "Accept": "application/json",
                            "Content-Type": "application/json",
                        },
                        body: JSON.stringify({ status: newStatus }),
                    });
                    const data = await resp.json();
                    if (data.success && data.record && row) {
                        const timeCell = row.querySelector(".cell-check-time");
                        const sourceCell = row.querySelector(".cell-source");
                        if (timeCell) {
                            timeCell.textContent = data.record.check_time ? formatTimeShort(data.record.check_time) : "—";
                        }
                        if (sourceCell) {
                            sourceCell.textContent = newStatus === "ABSENT" ? "No check-in" : "Lecturer updated";
                        }
                    }
                } catch {
                    form.submit();
                }
            });
        });
    }

    bindStatusDropdowns();

    // Roster client-side search & status filter
    function filterRosterRows() {
        const q = (searchInput ? searchInput.value : "").trim().toLowerCase();
        const stFilter = statusFilter ? statusFilter.value.toUpperCase() : "ALL";
        const rows = document.querySelectorAll(".roster-row");
        let visibleCount = 0;

        rows.forEach((row) => {
            const sid = row.dataset.studentId || "";
            const sname = row.dataset.studentName || "";
            const st = (row.dataset.status || "ABSENT").toUpperCase();

            const matchQuery = !q || sid.includes(q) || sname.includes(q);
            const matchStatus = stFilter === "ALL" || st === stFilter;

            if (matchQuery && matchStatus) {
                row.style.display = "";
                visibleCount += 1;
            } else {
                row.style.display = "none";
            }
        });

        if (showingText) {
            showingText.textContent = `Showing ${visibleCount} of ${rows.length} students · Status changes save automatically`;
        }
    }

    if (searchInput) searchInput.addEventListener("input", filterRosterRows);
    if (statusFilter) statusFilter.addEventListener("change", filterRosterRows);

    // Live polling every 5 seconds while session is open to reflect student PIN check-ins
    async function pollSessionRecords() {
        if (!sessionId) return;
        try {
            const resp = await fetch(`/lecturer/sessions/${sessionId}/records`, {
                headers: { "Accept": "application/json" },
            });
            if (!resp.ok) return;
            const data = await resp.json();
            if (!data.success) return;

            if (data.session) {
                remainingSeconds = parseInt(data.session.remaining_seconds || 0, 10);
                isOpen = Boolean(data.session.is_open);
                if (pinDisplay && data.session.pin_code) pinDisplay.textContent = data.session.pin_code;
                if (projectorPin && data.session.pin_code) projectorPin.textContent = data.session.pin_code;
                updateCountdownUI();
            }

            if (data.summary) {
                if (countPresent) countPresent.textContent = data.summary.present;
                if (countLate) countLate.textContent = data.summary.late;
                if (countExcused) countExcused.textContent = data.summary.excused;
                if (countAbsent) countAbsent.textContent = data.summary.absent;
                const checkedIn = (data.summary.present || 0) + (data.summary.late || 0);
                if (checkedInRatio) checkedInRatio.textContent = `${checkedIn} / ${data.summary.total}`;
            }

            if (Array.isArray(data.records)) {
                data.records.forEach((rec) => {
                    const row = document.querySelector(`.roster-row[data-record-id="${rec.record_id}"]`);
                    if (!row) return;
                    const st = (rec.status || "ABSENT").toUpperCase();
                    row.dataset.status = st;

                    const selectEl = row.querySelector(".status-select");
                    if (selectEl && document.activeElement !== selectEl) {
                        selectEl.value = st;
                        applyStatusClass(selectEl, st);
                    }
                    const timeCell = row.querySelector(".cell-check-time");
                    if (timeCell) {
                        timeCell.textContent = rec.check_time ? formatTimeShort(rec.check_time) : "—";
                    }
                    const sourceCell = row.querySelector(".cell-source");
                    if (sourceCell) {
                        if (st === "PRESENT") sourceCell.textContent = "PIN check-in";
                        else if (st === "LATE" || st === "EXCUSED") sourceCell.textContent = "Lecturer updated";
                        else sourceCell.textContent = "No check-in";
                    }
                });
                filterRosterRows();
            }
        } catch {
            // Ignore transient polling errors
        }
    }

    if (sessionId) {
        setInterval(() => {
            if (isOpen) pollSessionRecords();
        }, 5000);
    }
});

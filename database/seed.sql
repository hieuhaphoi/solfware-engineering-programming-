-- ============================================================================
-- Seed Data for Student Attendance Management System (PostgreSQL)
-- Sample data aligned with Figma UI screens (Section VI)
-- Note: Default password hash below corresponds to 'password123' (bcrypt)
-- ============================================================================

BEGIN;

-- 1. Insert Base Users (1 Admin, 2 Lecturers, 3 Students)
INSERT INTO Users (userID, username, email, password, status) VALUES
    (1, 'jamie.lee', 'jamie.lee@westbridge.edu', '$2b$12$zOihdBuK6LPvep/KmYtucOqAp90haRRNuUO3unVWFbijU2Lyg.LyS', 'Active'),
    (2, 'maya.chen', 'maya.chen@westbridge.edu', '$2b$12$zOihdBuK6LPvep/KmYtucOqAp90haRRNuUO3unVWFbijU2Lyg.LyS', 'Active'),
    (3, 'elias.reed', 'elias.reed@westbridge.edu', '$2b$12$zOihdBuK6LPvep/KmYtucOqAp90haRRNuUO3unVWFbijU2Lyg.LyS', 'Active'),
    (4, 'alex.morgan', 'alex.morgan@westbridge.edu', '$2b$12$zOihdBuK6LPvep/KmYtucOqAp90haRRNuUO3unVWFbijU2Lyg.LyS', 'Active'),
    (5, 'jordan.lee', 'jordan.lee@westbridge.edu', '$2b$12$zOihdBuK6LPvep/KmYtucOqAp90haRRNuUO3unVWFbijU2Lyg.LyS', 'Active'),
    (6, 'samira.patel', 'samira.patel@westbridge.edu', '$2b$12$zOihdBuK6LPvep/KmYtucOqAp90haRRNuUO3unVWFbijU2Lyg.LyS', 'Active');

-- Synchronize BIGSERIAL sequence after explicit ID inserts
SELECT setval(pg_get_serial_sequence('Users', 'userid'), (SELECT MAX(userID) FROM Users));

-- 2. Insert Role Profiles
INSERT INTO Admins (adminID, userID, role) VALUES
    ('A-1001', 1, 'Administrator');

INSERT INTO Lecturers (lecturerID, userID, fullname) VALUES
    ('L-1001', 2, 'Dr. Maya Chen'),
    ('L-1002', 3, 'Dr. Elias Reed');

INSERT INTO Students (studentID, userID, fullname, phone) VALUES
    ('2026001', 4, 'Alex Morgan', '0901234567'),
    ('2026002', 5, 'Jordan Lee', '0901234568'),
    ('2026003', 6, 'Samira Patel', '0901234569');

-- 3. Insert Courses (Matches Figma Catalog)
INSERT INTO Courses (courseID, coursename, credits) VALUES
    ('CS201', 'Database Systems', 3),
    ('CS105', 'Programming Fundamentals', 3),
    ('MA202', 'Discrete Mathematics', 4),
    ('SE301', 'Software Engineering', 4);

-- 4. Insert Course Sections
INSERT INTO Course_Sections (sectionID, courseID, lecturerID, sectionName, semester, room) VALUES
    ('CS201-A', 'CS201', 'L-1001', 'Section A', 'Fall 2026', 'Room B-204'),
    ('CS105-B', 'CS105', 'L-1001', 'Section B', 'Fall 2026', 'Room A-102'),
    ('MA202-A', 'MA202', 'L-1002', 'Section A', 'Fall 2026', 'Room C-105');

-- 5. Insert Student Enrollments
INSERT INTO Enrollments (studentID, sectionID, enrollDate) VALUES
    ('2026001', 'CS201-A', '2026-08-24'),
    ('2026002', 'CS201-A', '2026-08-24'),
    ('2026003', 'CS201-A', '2026-08-24'),
    ('2026001', 'CS105-B', '2026-08-24'),
    ('2026001', 'MA202-A', '2026-08-24');

-- 6. Insert Sample Attendance Session & Records
INSERT INTO Attendance_Sessions (sessionID, sectionID, sessionDate, pincode, starttime, endtime) VALUES
    (1, 'CS201-A', CURRENT_DATE, '482761', CURRENT_TIMESTAMP, CURRENT_TIMESTAMP + INTERVAL '5 minutes');

SELECT setval(pg_get_serial_sequence('Attendance_Sessions', 'sessionid'), (SELECT MAX(sessionID) FROM Attendance_Sessions));

INSERT INTO Attendance_Records (studentID, sessionID, checktime, status, absent) VALUES
    ('2026001', 1, NULL, 'ABSENT', TRUE),
    ('2026002', 1, CURRENT_TIMESTAMP, 'PRESENT', FALSE),
    ('2026003', 1, CURRENT_TIMESTAMP, 'LATE', FALSE);

COMMIT;

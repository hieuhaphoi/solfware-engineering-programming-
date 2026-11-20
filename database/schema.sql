-- ============================================================================
-- PostgreSQL Database Schema: Student Attendance Management System
-- Based on Section IV: Class Diagram & Database Schema (Entities 2.1 - 2.8 + Enrollments)
-- ============================================================================

BEGIN;

-- Drop existing tables in reverse dependency order for clean initialization
DROP TABLE IF EXISTS Attendance_Records CASCADE;
DROP TABLE IF EXISTS Attendance_Sessions CASCADE;
DROP TABLE IF EXISTS Enrollments CASCADE;
DROP TABLE IF EXISTS Course_Sections CASCADE;
DROP TABLE IF EXISTS Courses CASCADE;
DROP TABLE IF EXISTS Lecturers CASCADE;
DROP TABLE IF EXISTS Students CASCADE;
DROP TABLE IF EXISTS Admins CASCADE;
DROP TABLE IF EXISTS Users CASCADE;

-- ============================================================================
-- 2.1 Users: Stores login account information for all users in the system
-- ============================================================================
CREATE TABLE Users (
    userID BIGSERIAL PRIMARY KEY,
    username VARCHAR(50) NOT NULL UNIQUE,
    email VARCHAR(50) NOT NULL UNIQUE,
    password VARCHAR(255) NOT NULL,
    status VARCHAR(20) NOT NULL DEFAULT 'Active'
        CHECK (status IN ('Active', 'Locked', 'Pending'))
);

-- ============================================================================
-- 2.2 Admins: Stores administrator information (1:1 with Users)
-- ============================================================================
CREATE TABLE Admins (
    adminID VARCHAR(50) PRIMARY KEY,
    userID BIGINT NOT NULL UNIQUE,
    role VARCHAR(50) NOT NULL DEFAULT 'Administrator',
    CONSTRAINT fk_admins_user
        FOREIGN KEY (userID) REFERENCES Users(userID)
        ON DELETE CASCADE ON UPDATE CASCADE
);

-- ============================================================================
-- 2.3 Students: Stores student information (1:1 with Users)
-- ============================================================================
CREATE TABLE Students (
    studentID VARCHAR(50) PRIMARY KEY,
    userID BIGINT NOT NULL UNIQUE,
    fullname VARCHAR(255) NOT NULL,
    phone VARCHAR(11),
    CONSTRAINT fk_students_user
        FOREIGN KEY (userID) REFERENCES Users(userID)
        ON DELETE CASCADE ON UPDATE CASCADE
);

-- ============================================================================
-- 2.4 Lecturers: Stores lecturer information (1:1 with Users)
-- ============================================================================
CREATE TABLE Lecturers (
    lecturerID VARCHAR(50) PRIMARY KEY,
    userID BIGINT NOT NULL UNIQUE,
    fullname VARCHAR(255) NOT NULL,
    CONSTRAINT fk_lecturers_user
        FOREIGN KEY (userID) REFERENCES Users(userID)
        ON DELETE CASCADE ON UPDATE CASCADE
);

-- ============================================================================
-- 2.5 Courses: Stores course catalog information
-- ============================================================================
CREATE TABLE Courses (
    courseID VARCHAR(50) PRIMARY KEY,
    coursename VARCHAR(255) NOT NULL,
    credits INT NOT NULL CHECK (credits > 0)
);

-- ============================================================================
-- 2.6 Course_Sections: Stores course section information opened during a semester
-- ============================================================================
CREATE TABLE Course_Sections (
    sectionID VARCHAR(50) PRIMARY KEY,
    courseID VARCHAR(50) NOT NULL,
    lecturerID VARCHAR(50),
    sectionName VARCHAR(255) NOT NULL,
    semester VARCHAR(50) NOT NULL,
    room VARCHAR(50),
    CONSTRAINT fk_sections_course
        FOREIGN KEY (courseID) REFERENCES Courses(courseID)
        ON DELETE RESTRICT ON UPDATE CASCADE,
    CONSTRAINT fk_sections_lecturer
        FOREIGN KEY (lecturerID) REFERENCES Lecturers(lecturerID)
        ON DELETE SET NULL ON UPDATE CASCADE
);

-- ============================================================================
-- Enrollments: Many-to-Many relationship between Students and Course_Sections
-- (Matches ERD Diagram on Page 38 & Section IV.4 Remarks)
-- ============================================================================
CREATE TABLE Enrollments (
    studentID VARCHAR(50) NOT NULL,
    sectionID VARCHAR(50) NOT NULL,
    enrollDate DATE NOT NULL DEFAULT CURRENT_DATE,
    PRIMARY KEY (studentID, sectionID),
    CONSTRAINT fk_enrollments_student
        FOREIGN KEY (studentID) REFERENCES Students(studentID)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_enrollments_section
        FOREIGN KEY (sectionID) REFERENCES Course_Sections(sectionID)
        ON DELETE CASCADE ON UPDATE CASCADE
);

-- ============================================================================
-- 2.7 Attendance_Sessions: Stores attendance session information per class meeting
-- ============================================================================
CREATE TABLE Attendance_Sessions (
    sessionID BIGSERIAL PRIMARY KEY,
    sectionID VARCHAR(50) NOT NULL,
    sessionDate DATE NOT NULL DEFAULT CURRENT_DATE,
    pincode VARCHAR(10) NOT NULL,
    starttime TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    endtime TIMESTAMP NOT NULL,
    CONSTRAINT fk_sessions_section
        FOREIGN KEY (sectionID) REFERENCES Course_Sections(sectionID)
        ON DELETE CASCADE ON UPDATE CASCADE
);

-- ============================================================================
-- 2.8 Attendance_Records: Stores student attendance results per session
-- ============================================================================
CREATE TABLE Attendance_Records (
    recordID BIGSERIAL PRIMARY KEY,
    studentID VARCHAR(50) NOT NULL,
    sessionID BIGINT NOT NULL,
    checktime TIMESTAMP,
    status VARCHAR(20) NOT NULL DEFAULT 'ABSENT'
        CHECK (status IN ('PRESENT', 'ABSENT', 'LATE', 'EXCUSED')),
    absent BOOLEAN NOT NULL DEFAULT TRUE,
    CONSTRAINT fk_records_student
        FOREIGN KEY (studentID) REFERENCES Students(studentID)
        ON DELETE CASCADE ON UPDATE CASCADE,
    CONSTRAINT fk_records_session
        FOREIGN KEY (sessionID) REFERENCES Attendance_Sessions(sessionID)
        ON DELETE CASCADE ON UPDATE CASCADE,
    -- Unique Constraint (Section IV.2.8): Ensures a student can only check in once per session
    CONSTRAINT uq_student_session UNIQUE (studentID, sessionID)
);

-- ============================================================================
-- Performance Indexes (Supports < 2s response time & 50-100 concurrent check-ins)
-- ============================================================================
CREATE INDEX idx_course_sections_course ON Course_Sections(courseID);
CREATE INDEX idx_course_sections_lecturer ON Course_Sections(lecturerID);
CREATE INDEX idx_enrollments_section ON Enrollments(sectionID);
CREATE INDEX idx_attendance_sessions_section ON Attendance_Sessions(sectionID);
CREATE INDEX idx_attendance_records_session ON Attendance_Records(sessionID);
CREATE INDEX idx_attendance_records_student ON Attendance_Records(studentID);

COMMIT;

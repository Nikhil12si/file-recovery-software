-- schema.sql

-- Represents the users utilizing the recovery software
CREATE TABLE IF NOT EXISTS users (
    id SERIAL PRIMARY KEY,
    username VARCHAR(255) NOT NULL,
    created_at TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP
);

-- Tracks parameters and metrics for an individual recovery workflow run
CREATE TABLE IF NOT EXISTS recovery_sessions (
    id SERIAL PRIMARY KEY,
    user_id INTEGER REFERENCES users(id) ON DELETE CASCADE,
    selected_drive VARCHAR(255) NOT NULL,
    scan_mode VARCHAR(50) NOT NULL CHECK (scan_mode IN ('Quick', 'Deep')),
    destination_path VARCHAR(500),
    start_time TIMESTAMP WITH TIME ZONE DEFAULT CURRENT_TIMESTAMP,
    end_time TIMESTAMP WITH TIME ZONE,
    status VARCHAR(50) DEFAULT 'in_progress'
);

-- Optional indexes to speed up historical lookups
CREATE INDEX idx_recovery_sessions_user_id ON recovery_sessions(user_id);
CREATE INDEX idx_recovery_sessions_status ON recovery_sessions(status);

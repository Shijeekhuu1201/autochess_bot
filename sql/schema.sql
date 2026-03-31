PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS guild_settings (
    guild_id                  INTEGER PRIMARY KEY,
    weekly_channel_id         INTEGER DEFAULT 0,
    weekly_zone_channel_id    INTEGER DEFAULT 0,
    weekly_result_channel_id  INTEGER DEFAULT 0,
    admin_channel_id          INTEGER DEFAULT 0,
    admin_role_id             INTEGER DEFAULT 0,
    updated_at                TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS tournaments (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id                  INTEGER NOT NULL,
    type                      TEXT NOT NULL,
    game_key                  TEXT NOT NULL DEFAULT 'autochess',
    format_key                TEXT NOT NULL DEFAULT 'solo_32',
    slug                      TEXT NOT NULL DEFAULT '',
    title                     TEXT NOT NULL,
    season_name               TEXT NOT NULL DEFAULT 'Season 1',
    entry_fee                 INTEGER NOT NULL DEFAULT 0,
    max_players               INTEGER NOT NULL DEFAULT 32,
    lobby_size                INTEGER NOT NULL DEFAULT 8,
    bo_count                  INTEGER NOT NULL DEFAULT 2,

    score_1                   INTEGER NOT NULL DEFAULT 9,
    score_2                   INTEGER NOT NULL DEFAULT 7,
    score_3                   INTEGER NOT NULL DEFAULT 6,
    score_4                   INTEGER NOT NULL DEFAULT 5,
    score_5                   INTEGER NOT NULL DEFAULT 4,
    score_6                   INTEGER NOT NULL DEFAULT 3,
    score_7                   INTEGER NOT NULL DEFAULT 2,
    score_8                   INTEGER NOT NULL DEFAULT 1,

    start_time                TEXT,
    checkin_time              TEXT,

    prize_total               INTEGER NOT NULL DEFAULT 0,
    prize_1                   INTEGER NOT NULL DEFAULT 0,
    prize_2                   INTEGER NOT NULL DEFAULT 0,
    prize_3                   INTEGER NOT NULL DEFAULT 0,

    rules_text                TEXT NOT NULL DEFAULT '',

    status                    TEXT NOT NULL DEFAULT 'draft',
    announcement_channel_id   INTEGER DEFAULT 0,
    announcement_message_id   INTEGER DEFAULT 0,

    register_channel_id       INTEGER NOT NULL DEFAULT 0,
    register_message_id       INTEGER NOT NULL DEFAULT 0,
    waiting_channel_id        INTEGER NOT NULL DEFAULT 0,
    waiting_summary_message_id INTEGER NOT NULL DEFAULT 0,
    confirmed_channel_id      INTEGER NOT NULL DEFAULT 0,
    confirmed_summary_message_id INTEGER NOT NULL DEFAULT 0,

    created_by                INTEGER NOT NULL,
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at                TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_tournaments_guild_type_status
ON tournaments(guild_id, type, status);

CREATE TABLE IF NOT EXISTS tournament_entries (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id             INTEGER NOT NULL,
    user_id                   INTEGER NOT NULL,
    display_name              TEXT NOT NULL,
    register_order            INTEGER NOT NULL DEFAULT 0,
    payment_status            TEXT NOT NULL DEFAULT 'unpaid',
    status                    TEXT NOT NULL DEFAULT 'registered',
    is_replacement            INTEGER NOT NULL DEFAULT 0,
    replacement_for_entry_id  INTEGER,
    review_message_id         INTEGER NOT NULL DEFAULT 0,
    source                    TEXT NOT NULL DEFAULT 'discord',
    joined_at                 TEXT DEFAULT CURRENT_TIMESTAMP,
    confirmed_at              TEXT,

    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
    FOREIGN KEY (replacement_for_entry_id) REFERENCES tournament_entries(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_entry_unique_user_per_tournament
ON tournament_entries(tournament_id, user_id);

CREATE INDEX IF NOT EXISTS idx_entries_tournament_status
ON tournament_entries(tournament_id, status);

CREATE TABLE IF NOT EXISTS stages (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id             INTEGER NOT NULL,
    stage_key                 TEXT NOT NULL,
    stage_type                TEXT NOT NULL,
    round_order               INTEGER NOT NULL,
    lobby_password            TEXT,
    host_user_id              INTEGER DEFAULT 0,
    game_count                INTEGER NOT NULL DEFAULT 2,
    status                    TEXT NOT NULL DEFAULT 'pending',
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stage_unique
ON stages(tournament_id, stage_key);

CREATE TABLE IF NOT EXISTS stage_slots (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id                  INTEGER NOT NULL,
    slot_no                   INTEGER NOT NULL,
    original_entry_id         INTEGER NOT NULL,
    current_entry_id          INTEGER NOT NULL,
    inherited_from_slot_id    INTEGER,
    total_points              INTEGER NOT NULL DEFAULT 0,
    final_position            INTEGER,
    qualified                 INTEGER NOT NULL DEFAULT 0,
    eliminated                INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (stage_id) REFERENCES stages(id) ON DELETE CASCADE,
    FOREIGN KEY (original_entry_id) REFERENCES tournament_entries(id) ON DELETE RESTRICT,
    FOREIGN KEY (current_entry_id) REFERENCES tournament_entries(id) ON DELETE RESTRICT,
    FOREIGN KEY (inherited_from_slot_id) REFERENCES stage_slots(id) ON DELETE SET NULL
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_stage_slot_unique
ON stage_slots(stage_id, slot_no);

CREATE TABLE IF NOT EXISTS games (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    stage_id                  INTEGER NOT NULL,
    game_no                   INTEGER NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'pending',
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (stage_id) REFERENCES stages(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_game_unique
ON games(stage_id, game_no);

CREATE TABLE IF NOT EXISTS game_results (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    game_id                   INTEGER NOT NULL,
    stage_slot_id             INTEGER NOT NULL,
    placement                 INTEGER NOT NULL,
    points                    INTEGER NOT NULL,
    submitted_at              TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (game_id) REFERENCES games(id) ON DELETE CASCADE,
    FOREIGN KEY (stage_slot_id) REFERENCES stage_slots(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_result_unique_game_slot
ON game_results(game_id, stage_slot_id);

CREATE TABLE IF NOT EXISTS replacements (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id             INTEGER NOT NULL,
    stage_id                  INTEGER NOT NULL,
    stage_slot_id             INTEGER NOT NULL,
    out_entry_id              INTEGER NOT NULL,
    in_entry_id               INTEGER NOT NULL,
    applied_before_game_no    INTEGER NOT NULL,
    reason                    TEXT,
    created_by                INTEGER NOT NULL,
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
    FOREIGN KEY (stage_id) REFERENCES stages(id) ON DELETE CASCADE,
    FOREIGN KEY (stage_slot_id) REFERENCES stage_slots(id) ON DELETE CASCADE,
    FOREIGN KEY (out_entry_id) REFERENCES tournament_entries(id) ON DELETE RESTRICT,
    FOREIGN KEY (in_entry_id) REFERENCES tournament_entries(id) ON DELETE RESTRICT
);

CREATE TABLE IF NOT EXISTS player_profiles (
    user_id                   INTEGER PRIMARY KEY,
    display_name              TEXT NOT NULL,
    avatar_url                TEXT,
    phone_number              TEXT,
    bank_account              TEXT,
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,
    updated_at                TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS player_stats (
    user_id                   INTEGER PRIMARY KEY,
    tournaments_played        INTEGER NOT NULL DEFAULT 0,
    weekly_played             INTEGER NOT NULL DEFAULT 0,
    special_played            INTEGER NOT NULL DEFAULT 0,
    monthly_played            INTEGER NOT NULL DEFAULT 0,
    championships             INTEGER NOT NULL DEFAULT 0,
    runner_ups                INTEGER NOT NULL DEFAULT 0,
    third_places              INTEGER NOT NULL DEFAULT 0,
    podiums                   INTEGER NOT NULL DEFAULT 0,
    total_wins                INTEGER NOT NULL DEFAULT 0,
    total_prize_money         INTEGER NOT NULL DEFAULT 0,
    updated_at                TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (user_id) REFERENCES player_profiles(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS sponsors (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id             INTEGER NOT NULL,
    sponsor_kind              TEXT NOT NULL DEFAULT 'tournament',
    sponsor_name              TEXT NOT NULL,
    sponsor_user_id           INTEGER,
    amount                    INTEGER NOT NULL DEFAULT 0,
    note                      TEXT NOT NULL DEFAULT '',
    logo_url                  TEXT,
    website_url               TEXT,
    display_tier              TEXT NOT NULL DEFAULT 'sponsor',
    is_active                 INTEGER NOT NULL DEFAULT 1,
    created_by                INTEGER NOT NULL,
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sponsors_tournament
ON sponsors(tournament_id);

CREATE TABLE IF NOT EXISTS announcements (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id                  INTEGER NOT NULL DEFAULT 0,
    tournament_id             INTEGER,
    announcement_type         TEXT NOT NULL DEFAULT 'tournament',
    title                     TEXT NOT NULL,
    body                      TEXT NOT NULL DEFAULT '',
    badge                     TEXT NOT NULL DEFAULT 'Announcement',
    button_text               TEXT NOT NULL DEFAULT '',
    button_url                TEXT NOT NULL DEFAULT '',
    image_url                 TEXT NOT NULL DEFAULT '',
    target_channel            TEXT NOT NULL DEFAULT 'announcements',
    status                    TEXT NOT NULL DEFAULT 'draft',
    repeat_hours              INTEGER NOT NULL DEFAULT 0,
    publish_count             INTEGER NOT NULL DEFAULT 0,
    max_publishes             INTEGER NOT NULL DEFAULT 1,
    next_publish_at           TEXT,
    end_at                    TEXT,
    published_message_id      INTEGER NOT NULL DEFAULT 0,
    published_channel_id      INTEGER NOT NULL DEFAULT 0,
    published_at              TEXT,
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE SET NULL
);

CREATE TABLE IF NOT EXISTS tournament_admin_actions (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id                  INTEGER NOT NULL,
    tournament_id             INTEGER NOT NULL,
    action                    TEXT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'queued',
    requested_by              INTEGER NOT NULL DEFAULT 0,
    error_text                TEXT NOT NULL DEFAULT '',
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,
    processed_at              TEXT,

    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_tournament_admin_actions_status
ON tournament_admin_actions(status, created_at);

CREATE INDEX IF NOT EXISTS idx_announcements_status
ON announcements(status);

CREATE INDEX IF NOT EXISTS idx_announcements_tournament
ON announcements(tournament_id);

CREATE TABLE IF NOT EXISTS platform_donations (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    donor_name                TEXT NOT NULL,
    donor_user_id             INTEGER,
    amount                    INTEGER NOT NULL DEFAULT 0,
    note                      TEXT NOT NULL DEFAULT '',
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS supporter_memberships (
    guild_id                  INTEGER NOT NULL,
    user_id                   INTEGER NOT NULL,
    donor_tier                TEXT,
    donor_expires_at          TEXT,
    sponsor_tier              TEXT,
    sponsor_expires_at        TEXT,
    updated_at                TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE INDEX IF NOT EXISTS idx_supporter_memberships_user
ON supporter_memberships(user_id);

CREATE TABLE IF NOT EXISTS role_memberships (
    guild_id                  INTEGER NOT NULL,
    user_id                   INTEGER NOT NULL,
    confirmed_role_expires_at TEXT,
    updated_at                TEXT DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS ranked_queues (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id                  INTEGER NOT NULL,
    channel_id                INTEGER NOT NULL,
    message_id                INTEGER NOT NULL DEFAULT 0,
    queue_type                TEXT NOT NULL,
    title                     TEXT NOT NULL,
    entry_fee                 INTEGER NOT NULL DEFAULT 0,
    max_players               INTEGER NOT NULL DEFAULT 8,
    status                    TEXT NOT NULL DEFAULT 'open',
    winner_user_id            INTEGER,
    winner_user_id_2          INTEGER,
    created_by                INTEGER NOT NULL DEFAULT 0,
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,
    stopped_at                TEXT,
    completed_at              TEXT
);

CREATE INDEX IF NOT EXISTS idx_ranked_queues_guild_type_status
ON ranked_queues(guild_id, queue_type, status, created_at);

CREATE INDEX IF NOT EXISTS idx_ranked_queues_channel
ON ranked_queues(channel_id, status, created_at);

CREATE TABLE IF NOT EXISTS ranked_queue_entries (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    queue_id                  INTEGER NOT NULL,
    user_id                   INTEGER NOT NULL,
    display_name              TEXT NOT NULL,
    status                    TEXT NOT NULL DEFAULT 'queued',
    joined_at                 TEXT DEFAULT CURRENT_TIMESTAMP,
    confirmed_at              TEXT,
    confirm_order             INTEGER NOT NULL DEFAULT 0,

    FOREIGN KEY (queue_id) REFERENCES ranked_queues(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ranked_queue_entries_unique_user
ON ranked_queue_entries(queue_id, user_id);

CREATE INDEX IF NOT EXISTS idx_ranked_queue_entries_status
ON ranked_queue_entries(queue_id, status, joined_at);

CREATE TABLE IF NOT EXISTS payouts (
    id                        INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id             INTEGER NOT NULL,
    entry_id                  INTEGER NOT NULL,
    final_rank                INTEGER NOT NULL,
    amount                    INTEGER NOT NULL DEFAULT 0,
    created_at                TEXT DEFAULT CURRENT_TIMESTAMP,

    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE,
    FOREIGN KEY (entry_id) REFERENCES tournament_entries(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_payouts_tournament
ON payouts(tournament_id);

CREATE INDEX IF NOT EXISTS idx_payouts_entry
ON payouts(entry_id);

CREATE TABLE IF NOT EXISTS player_tournament_results (
    id                INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id     INTEGER NOT NULL,
    user_id           INTEGER NOT NULL,
    display_name      TEXT NOT NULL,
    season_name       TEXT NOT NULL,
    tournament_title  TEXT NOT NULL,
    final_rank        INTEGER NOT NULL,
    prize_amount      INTEGER NOT NULL DEFAULT 0,
    total_points      INTEGER NOT NULL DEFAULT 0,
    recorded_at       TEXT DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (tournament_id) REFERENCES tournaments(id) ON DELETE CASCADE
);

CREATE UNIQUE INDEX IF NOT EXISTS idx_ptr_unique_tournament_user
ON player_tournament_results(tournament_id, user_id);

CREATE INDEX IF NOT EXISTS idx_ptr_user_id
ON player_tournament_results(user_id);

CREATE INDEX IF NOT EXISTS idx_ptr_tournament_id
ON player_tournament_results(tournament_id);

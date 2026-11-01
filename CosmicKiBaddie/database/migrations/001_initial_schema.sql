-- ============================================================================
-- Cosmic ki baddie — Initial Relational Schema (001_initial_schema.sql)
-- Compatible with SQLite 3.35+ and PostgreSQL 13+
-- ============================================================================

CREATE TABLE IF NOT EXISTS schema_migrations (
    version INTEGER PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL
);

-- 1. Server / Guild Core Configuration & Prefixes
CREATE TABLE IF NOT EXISTS guild_configs (
    guild_id INTEGER PRIMARY KEY,
    prefixes_json TEXT NOT NULL DEFAULT '["!"]',
    admin_role_id INTEGER,
    mod_role_id INTEGER,
    support_role_id INTEGER,
    event_manager_role_id INTEGER,
    member_role_id INTEGER,
    mute_role_id INTEGER,
    modlog_channel_id INTEGER,
    security_alert_channel_id INTEGER,
    protection_mode TEXT NOT NULL DEFAULT 'normal', -- normal, enhanced, emergency
    safe_mode INTEGER NOT NULL DEFAULT 0,
    safe_mode_activated_by INTEGER,
    safe_mode_activated_at TEXT,
    xp_enabled INTEGER NOT NULL DEFAULT 1,
    xp_per_message_min INTEGER NOT NULL DEFAULT 15,
    xp_per_message_max INTEGER NOT NULL DEFAULT 25,
    xp_per_voice_minute INTEGER NOT NULL DEFAULT 10,
    xp_cooldown_seconds INTEGER NOT NULL DEFAULT 60,
    xp_ignored_channels_json TEXT NOT NULL DEFAULT '[]',
    xp_ignored_roles_json TEXT NOT NULL DEFAULT '[]',
    level_up_channel_id INTEGER,
    level_up_message TEXT NOT NULL DEFAULT 'GG {user.mention}! You advanced to **Level {level}** in **{server}**!',
    anti_spam_enabled INTEGER NOT NULL DEFAULT 1,
    anti_spam_max_messages INTEGER NOT NULL DEFAULT 6,
    anti_spam_window_seconds INTEGER NOT NULL DEFAULT 5,
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

-- 2. Granular Role & User Command Permission Rules
CREATE TABLE IF NOT EXISTS role_permissions (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    target_type TEXT NOT NULL DEFAULT 'role', -- 'role' or 'user'
    target_id INTEGER NOT NULL,
    command_name TEXT NOT NULL, -- specific command name, category:*, or '*'
    effect TEXT NOT NULL DEFAULT 'allow', -- 'allow' or 'deny'
    created_by INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(guild_id, target_type, target_id, command_name)
);

-- 3. Dynamic Command & Category Management Settings
CREATE TABLE IF NOT EXISTS command_settings (
    guild_id INTEGER NOT NULL,
    command_name TEXT NOT NULL,
    enabled INTEGER NOT NULL DEFAULT 1,
    category_enabled INTEGER NOT NULL DEFAULT 1,
    cooldown_seconds INTEGER NOT NULL DEFAULT 0,
    allowed_roles_json TEXT NOT NULL DEFAULT '[]',
    denied_roles_json TEXT NOT NULL DEFAULT '[]',
    allowed_channels_json TEXT NOT NULL DEFAULT '[]',
    denied_channels_json TEXT NOT NULL DEFAULT '[]',
    aliases_json TEXT NOT NULL DEFAULT '[]',
    updated_at TEXT NOT NULL,
    PRIMARY KEY (guild_id, command_name)
);

-- 4. Moderation Cases & Audit Logs
CREATE TABLE IF NOT EXISTS mod_cases (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    case_number INTEGER NOT NULL,
    action TEXT NOT NULL, -- ban, unban, kick, timeout, untimeout, warn, clearwarnings, mute, unmute, lock, unlock, purge, softban, antinuke
    target_id INTEGER NOT NULL,
    target_name TEXT,
    moderator_id INTEGER NOT NULL,
    moderator_name TEXT,
    reason TEXT NOT NULL,
    duration_seconds INTEGER,
    active INTEGER NOT NULL DEFAULT 1,
    log_message_id INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(guild_id, case_number)
);

-- 5. Active & Historical Member Warnings
CREATE TABLE IF NOT EXISTS warnings (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    moderator_id INTEGER NOT NULL,
    case_number INTEGER NOT NULL,
    reason TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL
);

-- 6. Configurable Warning Escalation Thresholds & Role Punishment Hierarchy
CREATE TABLE IF NOT EXISTS warning_escalations (
    guild_id INTEGER NOT NULL,
    warn_count INTEGER NOT NULL,
    action TEXT NOT NULL, -- timeout, mute, kick, ban
    duration_seconds INTEGER NOT NULL DEFAULT 3600,
    reason_template TEXT NOT NULL DEFAULT 'Automatic escalation at {count} warnings',
    PRIMARY KEY (guild_id, warn_count)
);

CREATE TABLE IF NOT EXISTS mod_role_permissions (
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    allowed_actions_json TEXT NOT NULL DEFAULT '["warn","timeout","mute","kick"]',
    PRIMARY KEY (guild_id, role_id)
);

-- 7. Comprehensive Logging Configuration per Guild
CREATE TABLE IF NOT EXISTS logging_configs (
    guild_id INTEGER PRIMARY KEY,
    default_log_channel_id INTEGER,
    moderation_log_channel_id INTEGER,
    message_log_channel_id INTEGER,
    member_log_channel_id INTEGER,
    server_log_channel_id INTEGER,
    voice_log_channel_id INTEGER,
    ticket_log_channel_id INTEGER,
    config_log_channel_id INTEGER,
    invite_log_channel_id INTEGER,
    embed_color INTEGER NOT NULL DEFAULT 9133302,
    embed_style TEXT NOT NULL DEFAULT 'detailed', -- detailed, compact, minimal
    enabled_events_json TEXT NOT NULL DEFAULT '{"message_delete":true,"message_edit":true,"member_join":true,"member_leave":true,"member_ban":true,"member_unban":true,"role_create":true,"role_delete":true,"role_update":true,"channel_create":true,"channel_delete":true,"channel_update":true,"nickname_change":true,"voice_activity":true,"member_timeout":true,"moderation_action":true,"invite_change":true,"ticket_activity":true,"config_change":true}'
);

-- 8. Autoresponders & Keyword Automation Engine
CREATE TABLE IF NOT EXISTS autoresponders (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    triggers_json TEXT NOT NULL, -- JSON list of trigger strings
    match_type TEXT NOT NULL DEFAULT 'contains', -- exact, contains, startswith, regex
    case_sensitive INTEGER NOT NULL DEFAULT 0,
    response_text TEXT,
    embed_name TEXT,
    embed_json TEXT,
    mention_user INTEGER NOT NULL DEFAULT 1,
    reply_message INTEGER NOT NULL DEFAULT 1,
    delete_trigger INTEGER NOT NULL DEFAULT 0,
    assign_role_id INTEGER,
    remove_role_id INTEGER,
    workflow_action TEXT, -- e.g. 'send_dm', 'add_reaction:🔥', 'log_channel'
    allowed_channels_json TEXT NOT NULL DEFAULT '[]',
    allowed_roles_json TEXT NOT NULL DEFAULT '[]',
    ignored_roles_json TEXT NOT NULL DEFAULT '[]',
    cooldown_seconds INTEGER NOT NULL DEFAULT 5,
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL,
    UNIQUE(guild_id, name)
);

-- 9. Welcome & Goodbye Configuration and Templates
CREATE TABLE IF NOT EXISTS welcome_configs (
    guild_id INTEGER PRIMARY KEY,
    welcome_enabled INTEGER NOT NULL DEFAULT 0,
    welcome_channel_id INTEGER,
    welcome_message TEXT NOT NULL DEFAULT 'Welcome to **{server}**, {user.mention}! You are member #{member_count}.',
    welcome_embed_json TEXT,
    welcome_auto_roles_json TEXT NOT NULL DEFAULT '[]',
    welcome_dm_enabled INTEGER NOT NULL DEFAULT 0,
    welcome_dm_message TEXT,
    welcome_image_enabled INTEGER NOT NULL DEFAULT 0,
    welcome_templates_json TEXT NOT NULL DEFAULT '[]',
    goodbye_enabled INTEGER NOT NULL DEFAULT 0,
    goodbye_channel_id INTEGER,
    goodbye_message TEXT NOT NULL DEFAULT '**{user.name}** has left **{server}**. We now have {member_count} members.',
    goodbye_embed_json TEXT
);

-- 10. Ticket System Configuration, Panels & Active/Archived Tickets
CREATE TABLE IF NOT EXISTS ticket_configs (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    category_id INTEGER,
    closed_category_id INTEGER,
    log_channel_id INTEGER,
    transcript_channel_id INTEGER,
    support_roles_json TEXT NOT NULL DEFAULT '[]',
    max_open_per_user INTEGER NOT NULL DEFAULT 2,
    naming_pattern TEXT NOT NULL DEFAULT 'ticket-{number}-{username}',
    welcome_message TEXT NOT NULL DEFAULT 'Welcome {user.mention}! Support staff ({support_roles}) will assist you shortly. Please describe your issue below.',
    questions_json TEXT NOT NULL DEFAULT '["What do you need help with?","Any additional details or screenshots?"]',
    categories_json TEXT NOT NULL DEFAULT '[{"id":"general","label":"General Support","emoji":"🎫","description":"General community help"},{"id":"report","label":"Member Report","emoji":"🛡️","description":"Report a rule violation"},{"id":"partnership","label":"Side Quest Inquiry","emoji":"🚀","description":"Events, collabs & studio inquiries"}]',
    next_ticket_number INTEGER NOT NULL DEFAULT 1
);

CREATE TABLE IF NOT EXISTS tickets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    ticket_number INTEGER NOT NULL,
    channel_id INTEGER NOT NULL UNIQUE,
    owner_id INTEGER NOT NULL,
    category_key TEXT NOT NULL DEFAULT 'general',
    status TEXT NOT NULL DEFAULT 'open', -- open, claimed, closed, deleted
    claimed_by INTEGER,
    answers_json TEXT NOT NULL DEFAULT '{}',
    transcript_path TEXT,
    created_at TEXT NOT NULL,
    closed_at TEXT,
    closed_by INTEGER,
    close_reason TEXT
);

-- 11. Saved Custom Embed Templates
CREATE TABLE IF NOT EXISTS saved_embeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    embed_json TEXT NOT NULL,
    buttons_json TEXT NOT NULL DEFAULT '[]',
    created_by INTEGER,
    updated_at TEXT NOT NULL,
    UNIQUE(guild_id, name)
);

-- 12. Activity Points, Levels & Role Progression
CREATE TABLE IF NOT EXISTS member_activity (
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    messages_count INTEGER NOT NULL DEFAULT 0,
    voice_minutes INTEGER NOT NULL DEFAULT 0,
    xp INTEGER NOT NULL DEFAULT 0,
    level INTEGER NOT NULL DEFAULT 0,
    last_message_at TEXT,
    last_active_at TEXT,
    PRIMARY KEY (guild_id, user_id)
);

CREATE TABLE IF NOT EXISTS role_rewards (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    reward_type TEXT NOT NULL DEFAULT 'level', -- 'level', 'messages', 'voice_minutes', 'xp'
    threshold INTEGER NOT NULL,
    remove_on_drop INTEGER NOT NULL DEFAULT 1,
    stack_roles INTEGER NOT NULL DEFAULT 1,
    UNIQUE(guild_id, role_id, reward_type)
);

-- 13. Temporary Roles & Self-Assignable Role Panels
CREATE TABLE IF NOT EXISTS temp_roles (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    user_id INTEGER NOT NULL,
    role_id INTEGER NOT NULL,
    assigned_by INTEGER,
    expires_at TEXT NOT NULL,
    active INTEGER NOT NULL DEFAULT 1,
    UNIQUE(guild_id, user_id, role_id)
);

CREATE TABLE IF NOT EXISTS role_menus (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    channel_id INTEGER,
    message_id INTEGER,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    mode TEXT NOT NULL DEFAULT 'multiple', -- 'multiple', 'single', 'verify'
    roles_json TEXT NOT NULL DEFAULT '[]', -- list of {role_id, label, emoji, description}
    created_at TEXT NOT NULL,
    UNIQUE(guild_id, name)
);

-- 14. Scheduled & Repeating Automation Tasks + Event Triggers
CREATE TABLE IF NOT EXISTS scheduled_automations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    automation_type TEXT NOT NULL, -- 'scheduled_once', 'repeating_message', 'auto_channel_clean', 'inactivity_role_prune'
    channel_id INTEGER,
    message_text TEXT,
    embed_name TEXT,
    interval_seconds INTEGER NOT NULL DEFAULT 3600,
    next_run_at TEXT NOT NULL,
    last_run_at TEXT,
    config_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    created_by INTEGER,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS automation_rules (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    name TEXT NOT NULL,
    trigger_event TEXT NOT NULL, -- 'member_join', 'member_leave', 'reaction_add', 'reaction_remove', 'message_milestone', 'channel_create'
    conditions_json TEXT NOT NULL DEFAULT '{}',
    actions_json TEXT NOT NULL DEFAULT '{}', -- e.g. {"assign_role": 123, "remove_role": 456, "send_channel_id": 789, "message": "..."}
    enabled INTEGER NOT NULL DEFAULT 1,
    created_at TEXT NOT NULL,
    UNIQUE(guild_id, name)
);

-- 15. Anti-Nuke Configuration, Trusted List & Security Incidents
CREATE TABLE IF NOT EXISTS antinuke_configs (
    guild_id INTEGER PRIMARY KEY,
    enabled INTEGER NOT NULL DEFAULT 1,
    window_seconds INTEGER NOT NULL DEFAULT 30,
    max_channel_delete INTEGER NOT NULL DEFAULT 3,
    max_channel_create INTEGER NOT NULL DEFAULT 4,
    max_role_delete INTEGER NOT NULL DEFAULT 3,
    max_role_create INTEGER NOT NULL DEFAULT 4,
    max_bans INTEGER NOT NULL DEFAULT 3,
    max_kicks INTEGER NOT NULL DEFAULT 4,
    max_webhooks INTEGER NOT NULL DEFAULT 3,
    block_unverified_bots INTEGER NOT NULL DEFAULT 1,
    monitor_admin_grants INTEGER NOT NULL DEFAULT 1,
    punishment_action TEXT NOT NULL DEFAULT 'quarantine', -- 'strip_roles', 'quarantine', 'kick', 'ban', 'alert_only'
    auto_safe_mode_on_trigger INTEGER NOT NULL DEFAULT 1,
    trusted_users_json TEXT NOT NULL DEFAULT '[]',
    trusted_roles_json TEXT NOT NULL DEFAULT '[]'
);

CREATE TABLE IF NOT EXISTS security_incidents (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    actor_id INTEGER NOT NULL,
    incident_type TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    threshold INTEGER NOT NULL,
    action_taken TEXT NOT NULL,
    details TEXT NOT NULL,
    created_at TEXT NOT NULL
);

-- 16. Giveaways & Entries
CREATE TABLE IF NOT EXISTS giveaways (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    host_id INTEGER NOT NULL,
    prize TEXT NOT NULL,
    description TEXT,
    winner_count INTEGER NOT NULL DEFAULT 1,
    required_role_id INTEGER,
    min_account_age_days INTEGER NOT NULL DEFAULT 0,
    min_server_days INTEGER NOT NULL DEFAULT 0,
    ends_at TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'active', -- 'active', 'ended', 'cancelled'
    winners_json TEXT NOT NULL DEFAULT '[]',
    entries_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL
);

-- 17. Server Events & RSVPs
CREATE TABLE IF NOT EXISTS community_events (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    creator_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    description TEXT NOT NULL,
    starts_at TEXT NOT NULL,
    location TEXT NOT NULL DEFAULT 'Side Quest Stage',
    status TEXT NOT NULL DEFAULT 'scheduled', -- 'scheduled', 'reminded', 'completed', 'cancelled'
    going_users_json TEXT NOT NULL DEFAULT '[]',
    interested_users_json TEXT NOT NULL DEFAULT '[]',
    declined_users_json TEXT NOT NULL DEFAULT '[]',
    reminder_sent INTEGER NOT NULL DEFAULT 0,
    created_at TEXT NOT NULL
);

-- 18. Polls & Votes
CREATE TABLE IF NOT EXISTS polls (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    guild_id INTEGER NOT NULL,
    channel_id INTEGER NOT NULL,
    message_id INTEGER,
    creator_id INTEGER NOT NULL,
    question TEXT NOT NULL,
    poll_type TEXT NOT NULL DEFAULT 'multiple', -- 'multiple', 'yesno'
    anonymous INTEGER NOT NULL DEFAULT 0,
    required_role_id INTEGER,
    options_json TEXT NOT NULL, -- list of option strings
    votes_json TEXT NOT NULL DEFAULT '{}', -- map of user_id_str -> option_index
    ends_at TEXT,
    status TEXT NOT NULL DEFAULT 'active', -- 'active', 'ended'
    created_at TEXT NOT NULL
);

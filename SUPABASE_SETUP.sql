-- ============================================================
-- PI AGENT - SUPABASE DATABASE SETUP
-- Run this entire file in Supabase SQL Editor
-- Project: Project Pi (Personal Intelligence)
-- ============================================================

-- ============================================================
-- TABLE 1: l3_active_memory
-- Active context loaded on every agent startup (~50 entries max)
-- ============================================================

CREATE TABLE IF NOT EXISTS l3_active_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    content TEXT NOT NULL,
    importance INTEGER NOT NULL CHECK (importance BETWEEN 1 AND 10),
    category TEXT NOT NULL,
    active_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    active_until TIMESTAMPTZ,
    editable BOOLEAN DEFAULT TRUE,
    auto_demote BOOLEAN DEFAULT FALSE,
    created_at TIMESTAMPTZ DEFAULT NOW(),
    last_accessed TIMESTAMPTZ DEFAULT NOW(),
    access_count INT DEFAULT 0,
    metadata JSONB DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS idx_l3_active_until ON l3_active_memory(active_until);
CREATE INDEX IF NOT EXISTS idx_l3_importance ON l3_active_memory(importance DESC);
CREATE INDEX IF NOT EXISTS idx_l3_category ON l3_active_memory(category);

-- ============================================================
-- TABLE 2: organized_memory
-- L2 structured knowledge base (searchable, unlimited)
-- ============================================================

CREATE TABLE IF NOT EXISTS organized_memory (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    category TEXT NOT NULL,
    subcategory TEXT,
    title TEXT NOT NULL,
    content JSONB NOT NULL,
    importance INT CHECK (importance BETWEEN 1 AND 10),
    status TEXT DEFAULT 'active',
    created_at TIMESTAMPTZ DEFAULT NOW(),
    updated_at TIMESTAMPTZ DEFAULT NOW(),
    source_l1_ids UUID[],
    tags TEXT[]
);

CREATE INDEX IF NOT EXISTS idx_l2_category ON organized_memory(category);
CREATE INDEX IF NOT EXISTS idx_l2_status ON organized_memory(status);
CREATE INDEX IF NOT EXISTS idx_l2_title_search ON organized_memory
    USING gin(to_tsvector('english', title));
CREATE INDEX IF NOT EXISTS idx_l2_tags ON organized_memory USING gin(tags);

-- ============================================================
-- TABLE 3: raw_wiki
-- L1 complete interaction archive (rolling 30-day window)
-- ============================================================

CREATE TABLE IF NOT EXISTS raw_wiki (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    timestamp TIMESTAMPTZ NOT NULL DEFAULT NOW(),
    thread_id UUID NOT NULL,
    role TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system', 'tool')),
    content TEXT NOT NULL,
    metadata JSONB DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_l1_timestamp ON raw_wiki(timestamp DESC);
CREATE INDEX IF NOT EXISTS idx_l1_thread ON raw_wiki(thread_id);
CREATE INDEX IF NOT EXISTS idx_l1_role ON raw_wiki(role);
CREATE INDEX IF NOT EXISTS idx_l1_content_search ON raw_wiki
    USING gin(to_tsvector('english', content));

-- ============================================================
-- ROW LEVEL SECURITY
-- ============================================================

ALTER TABLE l3_active_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE organized_memory ENABLE ROW LEVEL SECURITY;
ALTER TABLE raw_wiki ENABLE ROW LEVEL SECURITY;

-- Allow full access via service/anon key (Pi uses anon key)
CREATE POLICY "Allow full access on l3_active_memory"
    ON l3_active_memory FOR ALL
    USING (true) WITH CHECK (true);

CREATE POLICY "Allow full access on organized_memory"
    ON organized_memory FOR ALL
    USING (true) WITH CHECK (true);

CREATE POLICY "Allow full access on raw_wiki"
    ON raw_wiki FOR ALL
    USING (true) WITH CHECK (true);

-- ============================================================
-- SEED DATA: Ash's permanent profile
-- ============================================================

INSERT INTO l3_active_memory (
    content,
    importance,
    category,
    active_from,
    active_until,
    editable,
    auto_demote
) VALUES (
    'Ash: CS undergraduate at Georgia State University. AI research assistant working on graph neural networks under Dr. Esra Akbas. Building Project Pi - personal intelligence system. Values: Islamic conduct filter, direct communication style, cost-conscious, competitive mindset. Works on Windows 11, Python, E:\pi project directory.',
    10,
    'permanent_profile',
    NOW(),
    NULL,
    FALSE,
    FALSE
);

-- ============================================================
-- VERIFY (run after setup to confirm tables exist)
-- ============================================================
-- SELECT 'l3_active_memory' as tbl, count(*) FROM l3_active_memory
-- UNION ALL
-- SELECT 'organized_memory', count(*) FROM organized_memory
-- UNION ALL
-- SELECT 'raw_wiki', count(*) FROM raw_wiki;

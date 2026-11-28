PRAGMA foreign_keys = ON;

-- ---------- 条目 ----------
CREATE TABLE IF NOT EXISTS item (
  id            TEXT PRIMARY KEY,
  type          TEXT NOT NULL DEFAULT 'article',
  title         TEXT NOT NULL,
  abstract      TEXT NOT NULL DEFAULT '',
  year          INTEGER,
  venue         TEXT,
  doi           TEXT,
  arxiv_id      TEXT,
  url           TEXT,
  language      TEXT NOT NULL DEFAULT 'en',
  starred       INTEGER NOT NULL DEFAULT 0,
  note_md       TEXT NOT NULL DEFAULT '',
  cites         INTEGER NOT NULL DEFAULT 0,
  needs_review  INTEGER NOT NULL DEFAULT 0,
  added_at      TEXT NOT NULL,
  updated_at    TEXT NOT NULL,
  deleted_at    TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_item_doi   ON item(doi)      WHERE doi      IS NOT NULL AND doi      <> '';
CREATE UNIQUE INDEX IF NOT EXISTS ux_item_arxiv ON item(arxiv_id) WHERE arxiv_id IS NOT NULL AND arxiv_id <> '';
CREATE INDEX IF NOT EXISTS ix_item_year   ON item(year);
CREATE INDEX IF NOT EXISTS ix_item_added  ON item(added_at);

-- ---------- 作者 / 单位 ----------
CREATE TABLE IF NOT EXISTS author (
  id            TEXT PRIMARY KEY,
  display_name  TEXT NOT NULL,
  sort_name     TEXT NOT NULL DEFAULT '',
  orcid         TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_author_name ON author(display_name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS item_author (
  item_id    TEXT NOT NULL REFERENCES item(id)   ON DELETE CASCADE,
  author_id  TEXT NOT NULL REFERENCES author(id) ON DELETE CASCADE,
  position   INTEGER NOT NULL DEFAULT 0,
  role       TEXT NOT NULL DEFAULT 'author',
  PRIMARY KEY (item_id, author_id)
);
CREATE INDEX IF NOT EXISTS ix_item_author ON item_author(item_id, position);

CREATE TABLE IF NOT EXISTS org (
  id             TEXT PRIMARY KEY,
  name           TEXT NOT NULL,
  normalized_name TEXT NOT NULL DEFAULT ''
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_org_name ON org(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS item_org (
  item_id   TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  org_id    TEXT NOT NULL REFERENCES org(id)  ON DELETE CASCADE,
  position  INTEGER NOT NULL DEFAULT 0,
  PRIMARY KEY (item_id, org_id)
);
CREATE INDEX IF NOT EXISTS ix_item_org ON item_org(item_id, position);

-- ---------- 标签 ----------
CREATE TABLE IF NOT EXISTS tag (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  color       TEXT NOT NULL DEFAULT '',
  definition  TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL,
  deleted_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_name ON tag(name COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS tag_alias (
  alias       TEXT PRIMARY KEY,
  tag_id      TEXT NOT NULL REFERENCES tag(id) ON DELETE CASCADE,
  created_at  TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_alias ON tag_alias(alias COLLATE NOCASE);

CREATE TABLE IF NOT EXISTS item_tag (
  item_id     TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  tag_id      TEXT NOT NULL REFERENCES tag(id)  ON DELETE CASCADE,
  source      TEXT NOT NULL DEFAULT 'user',
  created_at  TEXT NOT NULL,
  PRIMARY KEY (item_id, tag_id)
);
CREATE INDEX IF NOT EXISTS ix_item_tag_tag ON item_tag(tag_id);

-- ---------- 标签分组（检索台用） ----------
-- 一个标签只属于一个组：由 tag_group_member.tag_id 做主键在库层面强制。
-- 没有成员行的标签 = 未分组（虚拟组，不建行，不参与排序）。
CREATE TABLE IF NOT EXISTS tag_group (
  id          TEXT PRIMARY KEY,
  name        TEXT NOT NULL,
  color       TEXT NOT NULL DEFAULT '',
  position    INTEGER NOT NULL DEFAULT 0,
  created_at  TEXT NOT NULL,
  deleted_at  TEXT
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_tag_group_name
  ON tag_group(name COLLATE NOCASE) WHERE deleted_at IS NULL;

CREATE TABLE IF NOT EXISTS tag_group_member (
  tag_id    TEXT PRIMARY KEY REFERENCES tag(id) ON DELETE CASCADE,
  group_id  TEXT NOT NULL REFERENCES tag_group(id) ON DELETE CASCADE,
  position  INTEGER NOT NULL DEFAULT 0,
  added_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_tgm_group ON tag_group_member(group_id, position);

-- ---------- 文件 / 批注 ----------
CREATE TABLE IF NOT EXISTS file (
  id             TEXT PRIMARY KEY,
  item_id        TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  rel_path       TEXT NOT NULL,
  sha256         TEXT NOT NULL,
  size           INTEGER NOT NULL DEFAULT 0,
  mime           TEXT NOT NULL DEFAULT 'application/pdf',
  original_name  TEXT NOT NULL DEFAULT '',
  added_at       TEXT NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS ux_file_sha ON file(sha256);
CREATE INDEX IF NOT EXISTS ix_file_item ON file(item_id);

CREATE TABLE IF NOT EXISTS shared_item_key (
  share_id TEXT PRIMARY KEY,
  item_id  TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE
);
CREATE INDEX IF NOT EXISTS ix_shared_item_key_item ON shared_item_key(item_id);

CREATE TABLE IF NOT EXISTS shared_pdf_hash (
  sha256  TEXT NOT NULL,
  item_id TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  PRIMARY KEY (sha256, item_id)
);
CREATE INDEX IF NOT EXISTS ix_shared_pdf_hash_item ON shared_pdf_hash(item_id);

CREATE TABLE IF NOT EXISTS annotation (
  id          TEXT PRIMARY KEY,
  file_id     TEXT NOT NULL REFERENCES file(id) ON DELETE CASCADE,
  page        INTEGER NOT NULL,
  rects_json  TEXT NOT NULL DEFAULT '[]',
  text        TEXT NOT NULL DEFAULT '',
  note_md     TEXT NOT NULL DEFAULT '',
  color       TEXT NOT NULL DEFAULT '',
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_annotation_file ON annotation(file_id, page);

-- ---------- 智能视图 / 对话 ----------
CREATE TABLE IF NOT EXISTS saved_view (
  id           TEXT PRIMARY KEY,
  name         TEXT NOT NULL,
  filter_json  TEXT NOT NULL,
  sort         TEXT NOT NULL DEFAULT 'year',
  created_at   TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation (
  id          TEXT PRIMARY KEY,
  title       TEXT NOT NULL,
  scope_json  TEXT NOT NULL,
  created_at  TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS message (
  id               TEXT PRIMARY KEY,
  conversation_id  TEXT NOT NULL REFERENCES conversation(id) ON DELETE CASCADE,
  role             TEXT NOT NULL CHECK (role IN ('user', 'assistant', 'system')),
  content          TEXT NOT NULL,
  cites_json       TEXT NOT NULL DEFAULT '[]',
  model            TEXT,
  created_at       TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_message_conv ON message(conversation_id, created_at);

-- ---------- 派生数据（可重建） ----------
CREATE TABLE IF NOT EXISTS chunk (
  id          TEXT PRIMARY KEY,
  file_id     TEXT NOT NULL REFERENCES file(id) ON DELETE CASCADE,
  page_from   INTEGER NOT NULL,
  page_to     INTEGER NOT NULL,
  text        TEXT NOT NULL,
  char_start  INTEGER NOT NULL DEFAULT 0,
  char_end    INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS ix_chunk_file ON chunk(file_id, page_from);

-- 检索用的段落（摘要 + PDF 每页），比 chunk 更适合没文件的条目
CREATE TABLE IF NOT EXISTS passage (
  id          TEXT PRIMARY KEY,
  item_id     TEXT NOT NULL REFERENCES item(id) ON DELETE CASCADE,
  kind        TEXT NOT NULL DEFAULT 'page' CHECK (kind IN ('abstract', 'page')),
  page        INTEGER NOT NULL DEFAULT 0,
  text        TEXT NOT NULL,
  created_at  TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS ix_passage_item ON passage(item_id, kind, page);

CREATE TABLE IF NOT EXISTS embedding (
  chunk_id  TEXT NOT NULL REFERENCES chunk(id) ON DELETE CASCADE,
  model     TEXT NOT NULL,
  dim       INTEGER NOT NULL,
  vector    BLOB NOT NULL,
  PRIMARY KEY (chunk_id, model)
);

-- ---------- 配置 ----------
CREATE TABLE IF NOT EXISTS setting (
  key    TEXT PRIMARY KEY,
  value  TEXT NOT NULL
);

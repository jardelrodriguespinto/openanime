-- Enable pgvector extension
CREATE EXTENSION IF NOT EXISTS vector;

-- Table for anime embeddings
CREATE TABLE IF NOT EXISTS animes (
    id SERIAL PRIMARY KEY,
    anime_id TEXT UNIQUE,
    titulo TEXT,
    synopsis TEXT,
    temas TEXT[],
    generos TEXT[],
    estudio TEXT,
    ano INTEGER,
    nota REAL,
    embedding VECTOR(1536),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Table for reviews
CREATE TABLE IF NOT EXISTS reviews (
    id SERIAL PRIMARY KEY,
    anime_id TEXT,
    texto TEXT,
    fonte TEXT,
    sentimento TEXT,
    embedding VECTOR(1536),
    created_at TIMESTAMP DEFAULT NOW()
);

-- Table for documents
CREATE TABLE IF NOT EXISTS documentos (
    id SERIAL PRIMARY KEY,
    user_id TEXT,
    doc_id TEXT,
    nome TEXT,
    tipo TEXT,
    conteudo TEXT,
    resumo TEXT,
    embedding VECTOR(1536),
    data_upload TIMESTAMP DEFAULT NOW()
);

-- Indices for vector search
CREATE INDEX IF NOT EXISTS animes_embedding_idx ON animes USING ivfflat (embedding vector_l2_ops);
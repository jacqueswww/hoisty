-- Which runner plays this environment. Every row that predates pyinfra support
-- is ansible, and the default keeps a hand-written INSERT honest.
ALTER TABLE environment ADD COLUMN tool TEXT NOT NULL DEFAULT 'ansible';

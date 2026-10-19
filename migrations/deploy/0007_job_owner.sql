-- boot_id:pid of the process running the job; a restart reaps only dead ones.
ALTER TABLE job ADD COLUMN owner TEXT;

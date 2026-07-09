-- Добавляет значение cancelled в enum meetingregistrystage (PostgreSQL).
-- Выполнить один раз на БД перед использованием отмены совещаний в реестре.

ALTER TYPE meetingregistrystage ADD VALUE IF NOT EXISTS 'cancelled';
